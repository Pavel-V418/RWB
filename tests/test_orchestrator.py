"""Граф конвейера на фейковых агентах: параллельная группа, ремонт, degraded, enabled_if,
resume после падения процесса, кэш этапов."""

import shutil
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from specforge.core.agent_api import AgentContext
from specforge.core.contracts import AgentSpec, CaseProfile, RunConfig, StageResult, StageStatus
from specforge.core.events import Event
from specforge.llm import FakeLLM, FakeReply, FakeToolCall, LLMClient
from specforge.orchestrator.budget import BudgetLimits
from specforge.orchestrator.graph import (
    SERDE,
    GraphConfig,
    OrchestratorError,
    build_graph,
    resume_run,
    start_run,
)
from specforge.orchestrator.nodes import GraphState
from specforge.orchestrator.registry import Registry

PY = f'"{sys.executable}"'
GATE = f"{PY} check.py"
CHECK = """import sys
value = open("value.txt", encoding="utf-8").read().strip()
if value != "good":
    print(f"value.txt:1: error: expected good, got {value}")
    sys.exit(1)
"""

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="нужен git")


class ProcessCrash(BaseException):
    """Имитация падения процесса: execute_stage ловит только Exception."""


Hook = Callable[[AgentContext], None]


class FakeAgent:
    """Агент без LLM: пишет файлы в output_dir и возвращает заданный статус."""

    def __init__(
        self,
        spec: AgentSpec,
        files: dict[str, str] | None = None,
        *,
        status: StageStatus = StageStatus.passed,
        hook: Hook | None = None,
    ) -> None:
        self.spec = spec
        self.files = files if files is not None else {f"{spec.name}.txt": spec.name}
        self.status = status
        self.hook = hook
        self.calls = 0

    def run(self, ctx: AgentContext) -> StageResult:
        self.calls += 1
        if self.hook is not None:
            self.hook(ctx)
        for rel, content in self.files.items():
            path = ctx.output_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return StageResult(agent=self.spec.name, status=self.status, artifacts=list(self.files))


def spec(name: str, **kwargs: Any) -> AgentSpec:
    defaults: dict[str, Any] = {
        "name": name,
        "stage": name,
        "kind": "tool",
        "outputs": ["*.txt"],
        "max_repairs": 0,
    }
    return AgentSpec(**{**defaults, **kwargs})


def five_agents(barrier: threading.Barrier | None = None) -> dict[str, FakeAgent]:
    """a → (b ∥ c) → d; e (has_mobile) после a."""

    def meet(ctx: AgentContext) -> None:
        if barrier is not None:
            barrier.wait()  # b и c должны встретиться — иначе они шли не параллельно

    return {
        "a": FakeAgent(spec("a")),
        "b": FakeAgent(
            spec("b", depends_on=["a"], parallel_group="gen", inputs=["a/*.txt"]), hook=meet
        ),
        "c": FakeAgent(
            spec("c", depends_on=["a"], parallel_group="gen", inputs=["a/*.txt"]), hook=meet
        ),
        "d": FakeAgent(spec("d", depends_on=["b", "c"], inputs=["b/*.txt", "c/*.txt"])),
        "e": FakeAgent(spec("e", depends_on=["a"], enabled_if="has_mobile")),
    }


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.runs_dir = tmp_path / "runs"
        self.requirements = tmp_path / "req.md"
        self.requirements.write_text("# ТЗ\n", encoding="utf-8")
        self.saver = InMemorySaver(serde=SERDE)
        self.events: list[Event] = []

    def config(self, llm: LLMClient | None = None, **kwargs: Any) -> GraphConfig:
        return GraphConfig(
            run=RunConfig(llm_base_url="http://fake", model="fake", max_concurrency=4),
            llm=llm or FakeLLM([]),
            runs_dir=self.runs_dir,
            emit=self.events.append,
            checkpointer=self.saver,
            **kwargs,
        )

    def run(
        self,
        agents: dict[str, FakeAgent] | list[Any],
        run_id: str = "r1",
        profile: CaseProfile | None = None,
        **kwargs: Any,
    ) -> GraphState:

        registry = Registry(agents.values() if isinstance(agents, dict) else agents)
        config = self.config(**kwargs)
        graph = build_graph(registry, config)
        return start_run(
            graph,
            registry,
            config,
            run_id=run_id,
            requirements=self.requirements,
            profile=profile,
        )

    def kinds(self, kind: str) -> list[Event]:
        return [e for e in self.events if e.kind == kind]


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def statuses(state: GraphState) -> dict[str, str]:
    return {name: r.status.value for name, r in state["stage_results"].items()}


# --- граф ---------------------------------------------------------------------------------------


def test_five_agents_parallel_group_and_enabled_if(h: Harness) -> None:
    agents = five_agents(threading.Barrier(2, timeout=10))
    state = h.run(agents, use_cache=False)

    assert statuses(state) == {
        "a": "passed",
        "b": "passed",
        "c": "passed",
        "d": "passed",
        "e": "skipped",
    }
    assert state["stage_results"]["e"].stopped_reason == "enabled_if"
    assert agents["e"].calls == 0
    root = h.runs_dir / "r1"
    assert (root / "artifacts" / "input" / "req.md").is_file()
    assert (root / "artifacts" / "d" / "d.txt").read_text("utf-8") == "d"
    manifest = (root / "artifacts" / "b" / "manifest.json").read_text("utf-8")
    assert '"b.txt"' in manifest
    assert '"sha256"' in manifest
    assert (root / "pipeline.json").is_file()
    assert h.kinds("run_finished")


def test_enabled_if_from_profile_flags(h: Harness) -> None:
    agents = five_agents()
    state = h.run(agents, profile=CaseProfile(has_mobile=True), use_cache=False)
    assert statuses(state)["e"] == "passed"


def test_profile_agent_builds_case_profile(h: Harness) -> None:
    profiler = FakeAgent(
        spec("profiler", stage="profile", outputs=["case_profile.json"]),
        {"case_profile.json": CaseProfile(has_mobile=True).model_dump_json()},
    )
    agents = [profiler, FakeAgent(spec("e", depends_on=["profiler"], enabled_if="has_mobile"))]
    state = h.run(agents, use_cache=False)
    assert state["profile"].has_mobile
    assert statuses(state) == {"profiler": "passed", "e": "passed"}
    assert h.kinds("profile_built")


def test_failed_stage_without_artifacts_skips_dependents(h: Harness) -> None:
    agents = five_agents()
    agents["b"] = FakeAgent(
        spec("b", depends_on=["a"], parallel_group="gen"), {}, status=StageStatus.failed
    )
    state = h.run(agents, use_cache=False)

    assert statuses(state)["b"] == "failed"
    assert statuses(state)["c"] == "passed"
    assert statuses(state)["d"] == "skipped"
    assert "зависимость b: failed" in state["stage_results"]["d"].summary
    assert agents["d"].calls == 0
    assert (h.runs_dir / "r1" / "pipeline.json").is_file()  # конвейер дошёл до finalize


def test_degraded_stage_with_artifacts_runs_dependents(h: Harness) -> None:
    agents = five_agents()
    agents["b"] = FakeAgent(
        spec("b", depends_on=["a"], parallel_group="gen"), status=StageStatus.degraded
    )
    agents["e"] = FakeAgent(spec("e", depends_on=["x"]))
    agents["x"] = FakeAgent(spec("x"), {}, status=StageStatus.degraded)
    state = h.run(agents, use_cache=False)

    assert statuses(state)["b"] == "degraded"
    assert statuses(state)["d"] == "passed"  # degraded с артефактами — идём дальше
    assert statuses(state)["e"] == "skipped"  # degraded без артефактов — нет
    assert "без артефактов" in state["stage_results"]["e"].summary


def call(name: str, **arguments: Any) -> FakeReply:
    return FakeReply(tool_calls=[FakeToolCall(name, arguments)])


def gated(name: str, **kwargs: Any) -> FakeAgent:
    return FakeAgent(
        spec(
            name,
            outputs=["value.txt", "check.py"],
            gate_commands=[GATE],
            gate_parsers={GATE: "generic"},
            max_repairs=2,
            **kwargs,
        ),
        {"value.txt": "bad\n", "check.py": CHECK},
    )


@needs_git
def test_red_gate_repaired_to_passed(h: Harness) -> None:
    llm = FakeLLM(
        [
            call("str_replace", path="value.txt", old="bad", new="good"),
            call("finish", summary="исправил value.txt"),
        ]
    )
    agents = [
        FakeAgent(spec("a")),
        gated("g", depends_on=["a"]),
        FakeAgent(spec("z", depends_on=["g"])),
    ]
    state = h.run(agents, llm=llm, use_cache=False)

    result = state["stage_results"]["g"]
    assert result.status == StageStatus.passed, result.summary
    assert result.gate is not None
    assert result.gate.passed
    assert result.attempts == 2
    assert statuses(state)["z"] == "passed"
    assert state["usage_total"].calls == 2
    assert (h.runs_dir / "r1" / "artifacts" / "g" / "value.txt").read_text(
        "utf-8"
    ).strip() == "good"
    llm.assert_exhausted()


@needs_git
def test_repairs_exhausted_degraded_and_pipeline_continues(h: Harness) -> None:
    llm = FakeLLM([call("finish", summary="не знаю"), call("finish", summary="не знаю")])
    agents = [gated("g"), FakeAgent(spec("z", depends_on=["g"]))]
    state = h.run(agents, llm=llm, use_cache=False)

    assert statuses(state) == {"g": "degraded", "z": "passed"}
    assert h.kinds("escalation")


# --- resume -------------------------------------------------------------------------------------


def test_resume_after_process_crash(h: Harness) -> None:
    crash = {"armed": True}

    def explode(ctx: AgentContext) -> None:
        if crash["armed"]:
            crash["armed"] = False
            raise ProcessCrash

    agents = five_agents()
    agents["d"].hook = explode
    with pytest.raises(ProcessCrash):
        h.run(agents, use_cache=False)
    assert [agents[n].calls for n in "abcd"] == [1, 1, 1, 1]

    # «новый процесс»: новый граф и реестр поверх того же хранилища чекпойнтов
    registry = Registry(agents.values())
    config = h.config(use_cache=False)
    state = resume_run(build_graph(registry, config), registry, config, run_id="r1")

    assert statuses(state)["d"] == "passed"
    assert [agents[n].calls for n in "abcd"] == [1, 1, 1, 2]  # a, b, c не перезапускались
    assert (h.runs_dir / "r1" / "pipeline.json").is_file()


def test_resume_from_stage_reruns_it_and_later_steps(h: Harness) -> None:
    agents = five_agents()
    h.run(agents, use_cache=False)

    registry = Registry(agents.values())
    config = h.config(use_cache=False)
    state = resume_run(build_graph(registry, config), registry, config, run_id="r1", from_stage="b")

    assert statuses(state)["d"] == "passed"
    assert {n: agents[n].calls for n in "abcd"} == {"a": 1, "b": 2, "c": 2, "d": 2}


def test_resume_unknown_run_and_stage(h: Harness) -> None:
    agents = five_agents()

    registry = Registry(agents.values())
    config = h.config(use_cache=False)
    graph = build_graph(registry, config)
    with pytest.raises(OrchestratorError, match="нет чекпойнта"):
        resume_run(graph, registry, config, run_id="nope")
    h.run(agents, use_cache=False)
    with pytest.raises(OrchestratorError, match="нет агента или этапа"):
        resume_run(graph, registry, config, run_id="r1", from_stage="zzz")


# --- кэш этапов ---------------------------------------------------------------------------------


def test_stage_cache_reuses_passed_outputs(h: Harness) -> None:
    agents = five_agents()
    h.run(agents, run_id="r1")
    state = h.run(agents, run_id="r2")

    assert {n: agents[n].calls for n in "abcd"} == {"a": 1, "b": 1, "c": 1, "d": 1}
    assert statuses(state)["d"] == "passed"
    assert state["stage_results"]["d"].summary.startswith("кэш ")
    assert state["usage_total"].calls == 0
    assert (h.runs_dir / "r2" / "artifacts" / "d" / "d.txt").read_text("utf-8") == "d"
    assert {e.agent for e in h.kinds("stage_cached")} == {"a", "b", "c", "d"}


def test_stage_cache_misses_when_input_changes(h: Harness) -> None:
    agents = five_agents()
    h.run(agents, run_id="r1")
    agents["a"].files = {"a.txt": "изменилось"}
    h.run(agents, run_id="r2")
    # a не кэшируется по входам (их нет), но его выход изменился → b, c, d пересчитаны
    assert agents["a"].calls == 1
    assert {n: agents[n].calls for n in "bcd"} == {"b": 1, "c": 1, "d": 1}


def test_stage_cache_ignores_failed_results(h: Harness) -> None:
    agents = five_agents()
    agents["d"].status = StageStatus.failed
    h.run(agents, run_id="r1")
    h.run(agents, run_id="r2")
    assert agents["d"].calls == 2


# --- бюджет в графе -----------------------------------------------------------------------------


def test_budget_exhausted_skips_remaining_stages(h: Harness) -> None:

    agents = five_agents()
    agents["a"].hook = lambda ctx: time.sleep(0.05)
    state = h.run(
        agents,
        use_cache=False,
        budget=BudgetLimits(global_tokens=1_000_000, wall_clock_s=0.01),
    )
    assert statuses(state) == {
        "a": "passed",
        "b": "skipped",
        "c": "skipped",
        "d": "skipped",
        "e": "skipped",
    }
    assert state["stage_results"]["b"].stopped_reason == "budget"
    assert state["stage_results"]["d"].stopped_reason == "dependency"
    assert any(e.data.get("decision") == "exhausted" for e in h.kinds("budget_warning"))
