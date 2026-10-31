"""Агенты-адаптеры к частям команды (packages/): сквозной прогон графа на FakeLLM, мост LLM,
стыковка путей анализа и веба, кэш по коду пакетов."""

import json
from pathlib import Path
from typing import Any

import pytest
from specforge_analysis.llm import LLMError as AnalysisLLMError
from specforge_analysis.schemas import Requirements

from agents._team.bridge import AnalysisLLM, WebLLM
from agents._team.web import platforms, write_team_inputs
from agents.analyst.agent import AGENT as ANALYST
from agents.backend_coder.agent import BackendCoder
from agents.devops.agent import DevOps
from agents.frontend_coder.agent import AGENT as FRONTEND
from agents.frontend_coder.agent import FrontendCoder
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


def test_team_pipeline_end_to_end(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.requirements = CASE1
    llm = FakeLLM([golden_answer] * 80)
    state = h.run(
        [ANALYST, ARCHITECT, UX, NoBuildFrontend(), NoDotnetBackend(), NoDockerDevOps(), WRITER],
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
