"""Агенты-адаптеры к частям команды (packages/): сквозной прогон графа на FakeLLM, мост LLM,
стыковка путей анализа и веба, кэш по коду пакетов."""

import json
from pathlib import Path
from typing import Any

import pytest
from specforge_analysis.llm import LLMError as AnalysisLLMError
from specforge_analysis.schemas import Requirements

from agents._team.bridge import AnalysisLLM, WebLLM
from agents._team.mobile import MobileLLM
from agents._team.web import platforms, write_team_inputs
from agents.analyst.agent import AGENT as ANALYST
from agents.android_coder.agent import AGENT as ANDROID
from agents.android_coder.agent import AndroidCoder
from agents.backend_coder.agent import BackendCoder
from agents.devops.agent import DevOps
from agents.frontend_coder.agent import AGENT as FRONTEND
from agents.frontend_coder.agent import FrontendCoder
from agents.qa.agent import AGENT as QA
from agents.qa.agent import Qa
from agents.system_architect.agent import AGENT as ARCHITECT
from agents.tech_writer.agent import AGENT as WRITER
from agents.ux_designer.agent import AGENT as UX
from specforge.core.agent_api import AgentContext
from specforge.core.contracts import StageResult, StageStatus
from specforge.llm import ChatMessage, FakeLLM, LLMConfig
from specforge.llm.types import LLMRequest
from specforge.orchestrator.cache import stage_key
from specforge.orchestrator.publisher import RepoLayout
from tests.test_orchestrator import Harness

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "packages" / "specforge-analysis" / "golden" / "case1_parking" / "llm"
CASE1 = ROOT / "examples" / "case1" / "tz.md"


def golden_answer(request: LLMRequest) -> str:
    """Эталонный ответ анализа, чьи поля входят в схему запроса; веб и прочее — «{}»: части
    уходят в детерминированную страховку."""
    props = set(((request.response_schema or {}).get("properties") or {}).keys())
    best, best_overlap = "{}", 0
    for path in sorted(GOLDEN.glob("*.json")):
        raw = path.read_text(encoding="utf-8")
        keys = set(json.loads(raw))
        if keys and keys <= props and len(keys) > best_overlap:
            best, best_overlap = raw, len(keys)
    return best


class NoBuildFrontend(FrontendCoder):
    """Без шлюза npm: в тестах нет Node; сборку проверяет песочница specforge/node:22."""

    spec = FrontendCoder.spec.model_copy(
        update={"gate_commands": [], "gate_parsers": {}, "sandbox_image": None}
    )


class NoDotnetBackend(BackendCoder):
    """Без dotnet и Docker (в тестах их нет): этап отдаёт метку; настоящий шлюз — внутри части."""

    def run(self, ctx: AgentContext) -> StageResult:
        (ctx.output_dir / "README.md").write_text("backend\n", encoding="utf-8")
        return self.result(StageStatus.passed, started=0.0, artifacts=self.collect_artifacts(ctx))


class NoDockerDevOps(DevOps):
    """Без Docker: стенд не поднимается, этап отдаёт метку."""

    def run(self, ctx: AgentContext) -> StageResult:
        (ctx.output_dir / "README-run.md").write_text("stand\n", encoding="utf-8")
        return self.result(StageStatus.passed, started=0.0, artifacts=self.collect_artifacts(ctx))


class NoGradleAndroid(AndroidCoder):
    """Без Gradle и образа сборки (в тестах их нет): этап отдаёт метку; шлюз — внутри части."""

    def run(self, ctx: AgentContext) -> StageResult:
        (ctx.output_dir / "README.md").write_text("android\n", encoding="utf-8")
        return self.result(StageStatus.passed, started=0.0, artifacts=self.collect_artifacts(ctx))


class NoStandQa(Qa):
    """Без стенда и сканеров: этап отдаёт метку."""

    def run(self, ctx: AgentContext) -> StageResult:
        (ctx.output_dir / "report.md").write_text("qa\n", encoding="utf-8")
        return self.result(StageStatus.passed, started=0.0, artifacts=self.collect_artifacts(ctx))


def _context(tmp_path: Path, llm: FakeLLM) -> AgentContext:
    (tmp_path / "art").mkdir(exist_ok=True)
    (tmp_path / "out").mkdir(exist_ok=True)
    return AgentContext(
        run_id="r1",
        input_dir=tmp_path / "art",
        output_dir=tmp_path / "out",
        workdir=tmp_path,
        llm=llm,
        budget_tokens=10_000,
    )


def test_team_pipeline_end_to_end(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.requirements = CASE1
    llm = FakeLLM([golden_answer] * 80)
    state = h.run(
        [
            ANALYST,
            ARCHITECT,
            UX,
            NoBuildFrontend(),
            NoDotnetBackend(),
            NoDockerDevOps(),
            NoGradleAndroid(),
            NoStandQa(),
            WRITER,
        ],
        llm=llm,
        use_cache=False,
    )

    results = state["stage_results"]
    statuses = {name: results[name].status for name in results}
    assert all(s in (StageStatus.passed, StageStatus.degraded) for s in statuses.values()), {
        n: (r.status, r.summary) for n, r in results.items()
    }
    art = tmp_path / "runs" / "r1" / "artifacts"
    req = Requirements.model_validate_json(
        (art / "01_requirements" / "requirements.json").read_text(encoding="utf-8")
    )
    assert req.functional
    assert (art / "02_system" / "openapi.yaml").is_file()
    assert (art / "02_system" / "schema.sql").is_file()
    ux = art / "03_ux"
    assert {p.name for p in ux.glob("screen-spec.*.json")} == {
        "screen-spec.web.json",
        "screen-spec.android.json",  # needs_mobile_app из профиля анализа
    }
    screens = json.loads((ux / "screens.json").read_text(encoding="utf-8"))["screens"]
    assert {s["platform"] for s in screens} == {"web", "android"}
    assert (ux / "docs" / "traceability.md").is_file()
    assert (art / "05_web" / "admin" / "package.json").is_file()
    docs = art / "09_docs"
    assert (docs / "mkdocs.yml").is_file()
    assert (docs / "docs" / "ui" / "traceability.md").is_file()  # интерфейс веба — в сайте
    assert "ui/" in (docs / "mkdocs.yml").read_text(encoding="utf-8")
    # вызовы модели — через LLMClient платформы: токены учтены у этапов, metrics.jsonl для отчёта
    assert results["analyst"].usage.calls >= 2
    assert (art / "01_requirements" / "metrics.jsonl").is_file()
    analyst_gate = results["analyst"].gate
    assert analyst_gate is not None
    assert all(d.tool == "requirements" for d in analyst_gate.diagnostics)


def test_analysis_llm_bridge_records_and_wraps_errors(tmp_path: Path) -> None:
    llm = FakeLLM(["{}", "{}"], config=LLMConfig(model="fake", structured_attempts=2))
    bridge = AnalysisLLM(llm, metrics_file=tmp_path / "metrics.jsonl")
    with pytest.raises(AnalysisLLMError):
        bridge.structured("extract", [{"role": "user", "content": "ТЗ"}], Requirements)
    record = json.loads((tmp_path / "metrics.jsonl").read_text(encoding="utf-8"))
    assert (record["name"], record["ok"], record["model"]) == ("extract", False, "fake")
    assert [c.profile for c in llm.calls] == ["ANALYSIS", "ANALYSIS"]


def test_web_llm_bridge_sends_guided_schema(tmp_path: Path) -> None:
    llm = FakeLLM(['{"screens": []}'])
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"screens": {"type": "array", "default": []}},
        "if": {"required": ["x"]},
    }
    answer = WebLLM(llm).complete(
        [{"role": "user", "content": "экраны"}], json_schema=schema, tag="ux.plan.web"
    )
    request = llm.calls[0]
    assert request.response_schema_name == "ux_plan_web"
    assert request.response_schema == {
        "type": "object",
        "properties": {"screens": {"type": "array"}},
    }
    assert answer.text == '{"screens": []}'
    assert request.messages == [ChatMessage(role="user", content="экраны")]


def test_platforms_and_team_inputs(tmp_path: Path) -> None:
    req = tmp_path / "01_requirements"
    req.mkdir()
    assert platforms(tmp_path) == ["web"]  # профиля нет — только web
    (req / "profile.json").write_text('{"needs_admin_ui": true, "needs_mobile_app": true}', "utf-8")
    assert platforms(tmp_path) == ["web", "android"]
    (req / "profile.json").write_text('{"platforms": ["android"], "needs_web_ui": true}', "utf-8")
    assert platforms(tmp_path) == ["android"]

    ux = tmp_path / "03_ux"
    ux.mkdir()
    spec = {
        "screens": [
            {"id": "list", "title": "Список", "roles": ["admin"], "requirements": ["FR-1", "FR-2"]}
        ]
    }
    (ux / "screen-spec.web.json").write_text(json.dumps(spec), "utf-8")
    assert write_team_inputs(ux) == 1
    assert json.loads((ux / "traceability.json").read_text("utf-8")) == {
        "FR-1": ["list"],
        "FR-2": ["list"],
    }


def test_layout_maps_team_stages_and_cache_tracks_package_code(tmp_path: Path) -> None:
    layout = RepoLayout.load()
    assert layout.place("09_docs", "mkdocs.yml") == "mkdocs.yml"
    assert layout.place("09_docs", "docs/index.md") == "docs/index.md"
    assert layout.place("09_docs", "metrics.jsonl") is None  # служебный файл этапа
    assert layout.place("05_web", "admin/package.json") == "web/admin/package.json"
    assert layout.place("08_infra", ".gitlab-ci.yml") == ".gitlab-ci.yml"

    source = tmp_path / "pkg"
    source.mkdir()
    (source / "module.py").write_text("A = 1", encoding="utf-8")

    class Tracked(FrontendCoder):
        cache_sources = (source,)

    agent = Tracked()
    before = stage_key(agent, tmp_path)
    (source / "module.py").write_text("A = 2", encoding="utf-8")
    assert stage_key(agent, tmp_path) != before
    assert FRONTEND.spec.gate_commands  # в бою сборку проверяет шлюз платформы


def test_mobile_llm_bridge_goes_through_platform_client() -> None:
    llm = FakeLLM(["экраны"])
    bridge = MobileLLM(llm)
    reply = bridge.chat(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}], max_tokens=100
    )
    assert reply.text == "экраны"
    assert bridge.usage.calls == 1
    request = llm.calls[0]
    assert request.profile == "CODE"
    assert [m.role for m in request.messages] == ["system", "user"]


def test_android_coder_skips_when_mobile_not_needed(tmp_path: Path) -> None:
    llm = FakeLLM([])
    ctx = _context(tmp_path, llm)
    (ctx.input_dir / "01_requirements").mkdir()
    (ctx.input_dir / "01_requirements" / "profile.json").write_text(
        '{"needs_mobile_app": false}', "utf-8"
    )
    result = ANDROID.run(ctx)
    assert result.status == StageStatus.passed
    assert (ctx.output_dir / "README.md").is_file()
    assert not llm.calls  # модель не вызывалась


def test_android_coder_without_ux_spec_fails_cleanly(tmp_path: Path) -> None:
    ctx = _context(tmp_path, FakeLLM([]))
    result = ANDROID.run(ctx)  # профиля нет — строим, но спеки экранов нет
    assert result.status == StageStatus.failed
    assert result.gate is not None
    assert "screen-spec.android.json" in result.gate.diagnostics[0].message


def test_qa_without_stand_reports_and_publishes_ci(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SPECFORGE_QA_STAND", "off")
    monkeypatch.setattr("shutil.which", lambda _name: None)  # сканеров нет — пропускаются
    ctx = _context(tmp_path, FakeLLM([]))
    result = QA.run(ctx)
    assert result.status == StageStatus.passed, result.summary
    for name in ("security.json", "report.md", "ci/security.gitlab-ci.yml", "e2e/route_e2e.py"):
        assert (ctx.output_dir / name).is_file(), name
    assert result.gate is not None
    assert any(d.severity == "warning" for d in result.gate.diagnostics)  # стенд не поднимался
    layout = RepoLayout.load()
    assert (
        layout.place("07_tests", "ci/tests.gitlab-ci.yml") == "reports/tests/ci/tests.gitlab-ci.yml"
    )
