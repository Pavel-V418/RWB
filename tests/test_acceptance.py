"""Приёмщик: регрессия после позднего этапа, запуск и healthcheck, документация, Trivy, покрытие,
резюме через LLM (REVIEW); место в графе."""

import json
import shutil
from pathlib import Path

import pytest

from agents.acceptance.agent import AcceptanceAgent, Verdict, build_verdict, fallback_summary
from agents.acceptance.checks import (
    check_coverage,
    check_docs,
    check_runtime,
    check_trivy,
    is_prometheus,
)
from specforge.core.contracts import StageStatus
from specforge.llm import Expect, FakeLLM, FakeReply, LLMError
from specforge.orchestrator.nodes import StageSnapshot
from specforge.orchestrator.publisher import RepoLayout
from specforge.orchestrator.registry import Registry, RegistryError
from tests.test_orchestrator import CHECK, FakeAgent, Harness, spec

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="нужен git")
PROM = (
    "# HELP http_requests_total Requests"
    + chr(10)
    + "# TYPE http_requests_total counter"
    + chr(10)
    + 'http_requests_total{code="200"} 3'
    + chr(10)
)


class FakeCompose:
    def __init__(self, *, available: bool = True, up: bool = True, e2e: bool = True) -> None:
        self._available, self._up, self._e2e = available, up, e2e
        self.calls: list[str] = []

    def available(self) -> bool:
        return self._available

    def up(self, compose_file: Path) -> tuple[bool, str]:
        self.calls.append("up")
        return self._up, "" if self._up else "service api exited with code 1"

    def ports(self, compose_file: Path) -> dict[str, int]:
        return {"api": 8080, "web": 4200}

    def run_script(self, script: Path, cwd: Path) -> tuple[bool, str]:
        self.calls.append("e2e")
        return self._e2e, "" if self._e2e else "сценарий заказа упал"

    def down(self, compose_file: Path) -> None:
        self.calls.append("down")


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def templates(tmp_path: Path) -> Path:
    path = tmp_path / "doc_templates.yaml"
    path.write_text(
        "templates:\n"
        "  tz: {sections: [Цел|Goal, Требования, Ограничения]}\n"
        "  api: {sections: [Описание], keys: [openapi, info, paths]}\n",
        encoding="utf-8",
    )
    return path


@needs_git
def test_late_stage_regression_lands_in_verdict(tmp_path: Path) -> None:
    gate = "python check.py"
    backend = FakeAgent(
        spec(
            "backend",
            outputs=["*.txt", "*.py"],
            gate_commands=[gate],
            gate_parsers={gate: "generic"},
        ),
        {"value.txt": "good\n", "check.py": CHECK},
    )
    # поздний этап (security-исправления) пишет в тот же каталог итогового репозитория
    security = FakeAgent(spec("security", depends_on=["backend"]), {"value.txt": "bad\n"})
    layout = RepoLayout(stages={"backend": "backend/", "security": "backend/"})
    compose = FakeCompose(available=False)
    acceptance = AcceptanceAgent(
        layout=layout, templates=templates(tmp_path), compose=lambda project: compose
    )
    llm = FakeLLM(
        [
            FakeReply(
                content="Не готово к сдаче: регрессия backend.",
                expect=Expect(profile="REVIEW", contains="backend"),
            )
        ]
    )
    state = Harness(tmp_path).run([backend, security, acceptance], llm=llm, use_cache=False)

    assert state["stage_results"]["backend"].status == StageStatus.passed  # state верит этапу
    result = state["stage_results"]["acceptance"]
    assert result.status == StageStatus.degraded
    verdict = Verdict.model_validate_json(
        (tmp_path / "runs" / "r1" / "artifacts" / "acceptance" / "verdict.json").read_text("utf-8")
    )
    assert not verdict.ready
    assert len(verdict.regressions) == 1
    assert verdict.regressions[0].startswith("backend: шлюз был зелёным")
    assert verdict.blockers[0] == verdict.regressions[0]
    assert {g.agent: g.passed_now for g in verdict.gates} == {"backend": False}
    assert "нет docker-compose в итоговом репозитории" in verdict.not_done
    assert verdict.summary == "Не готово к сдаче: регрессия backend."
    llm.assert_exhausted()


def test_runtime_health_metrics_and_e2e(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    write(repo, "infra/docker-compose.yml", "services:\n  api: {build: ../backend}\n")
    write(repo, "e2e/run.sh", "echo ok\n")
    assert is_prometheus(PROM)
    assert not is_prometheus('{"requests": 3}')

    def healthy(url: str) -> tuple[int, str]:
        return (200, '{"status":"Healthy"}') if url.endswith("/health") else (200, PROM)

    ok = check_runtime(repo, FakeCompose(), healthy)
    assert ok.up_ok
    assert [(s.service, s.health, s.metrics_ok) for s in ok.services] == [("api", "Healthy", True)]
    assert ok.e2e_ok is True

    def broken(url: str) -> tuple[int, str]:
        return (503, "Unhealthy: db down") if url.endswith("/health") else (200, '{"x": 1}')

    compose = FakeCompose(e2e=False)
    bad = check_runtime(repo, compose, broken)
    assert compose.calls == ["up", "e2e", "down"]  # down — всегда
    verdict = build_verdict(
        StageSnapshot(results={}, specs={}, order=[]),
        [],
        bad,
        [],
        check_trivy(repo),
        check_coverage(repo),
    )
    assert not verdict.ready
    assert "api: /health: Unhealthy" in verdict.blockers
    assert any("/metrics" in b for b in verdict.blockers)
    assert any(b.startswith("e2e e2e/run.sh упал") for b in verdict.blockers)

    down = check_runtime(repo, FakeCompose(up=False), healthy)
    assert down.up_ok is False
    assert check_runtime(tmp_path / "empty", FakeCompose(), healthy).note.startswith(
        "нет docker-compose"
    )


def test_docs_trivy_and_requirement_coverage(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    write(repo, "docs/tz/tz.md", "# ТЗ\n## Цели проекта\n## Требования\n")  # нет «Ограничения»
    write(repo, "docs/api/openapi.yaml", "openapi: 3.1.0\ninfo: {title: x}\npaths: {}\n")
    write(repo, "backend/Orders.cs", "class Orders {}")
    write(repo, "backend/tests/OrdersTests.cs", "class OrdersTests {}")
    write(
        repo,
        "docs/traceability.md",
        "| FR | Код | Тест |\n|---|---|---|\n"
        "| FR-1 | backend/Orders.cs | backend/tests/OrdersTests.cs |\n"
        "| FR-2 | backend/Payments.cs | |\n",
    )
    write(
        repo,
        "reports/trivy-backend.json",
        json.dumps(
            {
                "Results": [
                    {
                        "Vulnerabilities": [
                            {"VulnerabilityID": "CVE-1", "PkgName": "libx", "Severity": "HIGH"},
                            {"VulnerabilityID": "CVE-2", "PkgName": "liby", "Severity": "LOW"},
                        ]
                    }
                ]
            }
        ),
    )
    from agents.acceptance.checks import load_templates  # noqa: PLC0415

    docs = check_docs(repo, load_templates(templates(tmp_path)))
    share = {d.doc_type: d.share for d in docs}
    assert share["tz"] == pytest.approx(2 / 3, abs=0.01)
    assert share["api"] == 1.0  # OpenAPI: ключи верхнего уровня
    security = check_trivy(repo)
    assert security.counts == {"HIGH": 1, "LOW": 1}
    coverage = check_coverage(repo)
    assert (coverage.total, coverage.covered, coverage.share) == (2, 1, 0.5)
    assert coverage.uncovered[0].id == "FR-2"

    verdict = build_verdict(
        StageSnapshot(results={}, specs={}, order=[]),
        [],
        check_runtime(repo, FakeCompose(available=False)),
        docs,
        security,
        coverage,
    )
    assert not verdict.ready
    assert verdict.blockers == ["уязвимости HIGH и выше: 1 (CVE-1 (libx, HIGH))"]
    assert any(r.startswith("«tz»: 67% разделов") and "Ограничения" in r for r in verdict.risks)
    assert any(n.startswith("FR-2: код: backend/Payments.cs") for n in verdict.not_done)
    assert "Не готово" in fallback_summary(verdict)


def test_summary_falls_back_without_llm_and_registry_order(tmp_path: Path) -> None:
    acceptance = AcceptanceAgent(
        layout=RepoLayout(),
        templates=templates(tmp_path),
        compose=lambda project: FakeCompose(available=False),
    )
    llm = FakeLLM([LLMError("модель недоступна")])
    state = Harness(tmp_path).run([FakeAgent(spec("a")), acceptance], llm=llm, use_cache=False)
    verdict = json.loads(
        (tmp_path / "runs" / "r1" / "artifacts" / "acceptance" / "verdict.json").read_text("utf-8")
    )
    assert verdict["summary"].startswith(("Готово", "Не готово"))
    assert any("резюме LLM не получено" in r for r in verdict["risks"])
    reports = tmp_path / "runs" / "r1" / "artifacts" / "acceptance"
    assert (reports / "test-report.md").read_text("utf-8").startswith("# Отчёт о тестировании")
    assert (reports / "security-report.md").is_file()
    assert "acceptance" in state["stage_results"]

    registry = Registry([FakeAgent(spec("b", depends_on=["a"])), acceptance, FakeAgent(spec("a"))])
    assert registry.steps[-1] == ("acceptance",)
    with pytest.raises(RegistryError, match="зависит от приёмщика"):
        Registry([acceptance, FakeAgent(spec("late", depends_on=["acceptance"]))])
