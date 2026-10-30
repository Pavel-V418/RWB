"""Реестр (валидация, план шагов), BudgetController, workspace артефактов, CLI list-agents."""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from specforge import cli
from specforge.core.artifacts import RunWorkspace
from specforge.core.contracts import AgentSpec, RunConfig, StageResult, StageStatus
from specforge.core.events import Event
from specforge.llm import Expect, FakeLLM, FakeReply
from specforge.orchestrator.budget import (
    BudgetController,
    BudgetLimits,
    StageLimits,
    active_seconds,
)
from specforge.orchestrator.registry import Registry, RegistryError, globs_overlap, plan_steps
from tests.test_orchestrator import FakeAgent, spec


def registry(*specs: AgentSpec) -> Registry:
    return Registry(FakeAgent(s) for s in specs)


# --- реестр -------------------------------------------------------------------------------------


def test_registry_rejects_unknown_dependency() -> None:
    with pytest.raises(RegistryError, match="неизвестного агента 'ghost'"):
        registry(spec("a", depends_on=["ghost"]))


def test_registry_rejects_cycle() -> None:
    with pytest.raises(RegistryError, match="цикл зависимостей: a → b → c → a"):
        registry(
            spec("a", depends_on=["b"]),
            spec("b", depends_on=["c"]),
            spec("c", depends_on=["a"]),
        )


def test_registry_rejects_overlapping_outputs() -> None:
    with pytest.raises(RegistryError, match="outputs пересекаются"):
        registry(
            spec("a", stage="web", outputs=["src/**/*.ts"]),
            spec("b", stage="web", outputs=["src/app/main.ts"]),
        )
    # одинаковые шаблоны в разных этапах — разные каталоги artifacts/<stage>/
    registry(spec("a", outputs=["*.ts"]), spec("b", outputs=["*.ts"]))


def test_registry_rejects_bad_groups_and_names() -> None:
    with pytest.raises(RegistryError) as info:
        registry(
            spec("a", parallel_group="g"),
            spec("b", parallel_group="g", depends_on=["a"], outputs=["b/*.txt"]),
            spec("finalize", outputs=["f.txt"]),
            spec("x", stage="input", outputs=["x.txt"]),
        )
    text = str(info.value)
    assert "b зависит от a той же группы" in text
    assert "finalize: имя зарезервировано" in text
    assert "недопустимое имя этапа 'input'" in text


def test_registry_profile_agent_rules() -> None:
    with pytest.raises(RegistryError, match="несколько агентов этапа profile"):
        registry(spec("p1", stage="profile", outputs=["a"]), spec("p2", stage="profile"))
    reg = registry(spec("p", stage="profile", outputs=["case_profile.json"]), spec("a"))
    assert reg.profile_agent is not None
    assert reg.steps == [("a",)]
    assert reg.node_of("p") == "profile"
    assert reg.node_of("a") == "a"


@pytest.mark.parametrize(
    ("a", "b", "overlap"),
    [
        ("web/*.ts", "web/*.ts", True),
        ("web/**", "web/src/x.ts", True),
        ("web/src/**/*.ts", "web/src/app/x.ts", True),
        ("web/**/*.ts", "web/src/**/*.js", True),  # приближённо: общий префикс и **
        ("web/*.ts", "web/*.js", False),
        ("api/*.yaml", "web/*.yaml", False),
    ],
)
def test_globs_overlap(a: str, b: str, overlap: bool) -> None:
    assert globs_overlap(a, b) is overlap


def test_plan_steps_groups_ready_members_together() -> None:
    specs = {
        s.name: s
        for s in (
            spec("a"),
            spec("b", depends_on=["a"], parallel_group="gen"),
            spec("c", depends_on=["a"], parallel_group="gen"),
            spec("d", depends_on=["b", "c"]),
            spec("e", depends_on=["a"]),
        )
    }
    assert plan_steps(specs, set()) == [("a",), ("b", "c"), ("d",), ("e",)]


def test_plan_steps_waits_for_whole_group() -> None:
    # c входит в группу, но готов только после x: сначала x, затем b ∥ c
    specs = {
        s.name: s
        for s in (
            spec("b", parallel_group="gen"),
            spec("c", parallel_group="gen", depends_on=["x"]),
            spec("x"),
        )
    }
    assert plan_steps(specs, set()) == [("x",), ("b", "c")]


def test_discover_skips_private_agents() -> None:
    assert "_example" not in Registry.discover().agents


def test_cli_list_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = registry(
        spec("a"),
        spec("b", depends_on=["a"], parallel_group="g"),
        spec("c", parallel_group="g", depends_on=["a"]),
    )
    monkeypatch.setattr(cli, "build_registry", lambda: reg)
    result = CliRunner().invoke(cli.app, ["list-agents"])
    assert result.exit_code == 0, result.output
    assert "План: profile(флаги CLI) → a → b | c → finalize" in result.output


def test_cli_list_agents_reports_invalid_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> Registry:
        return registry(spec("a", depends_on=["ghost"]))

    monkeypatch.setattr(cli, "build_registry", broken)
    result = CliRunner().invoke(cli.app, ["list-agents"])
    assert result.exit_code == 1
    assert "ghost" in result.output


# --- бюджет -------------------------------------------------------------------------------------


def controller(events: list[Event], **kwargs: Any) -> BudgetController:
    limits = BudgetLimits(global_tokens=1000, wall_clock_s=100.0, **kwargs)
    return BudgetController(limits, run_id="r", emit=events.append)


def decisions(events: list[Event]) -> list[str]:
    return [str(e.data["decision"]) for e in events if e.kind == "budget_warning"]


def test_budget_below_thresholds_no_warnings() -> None:
    events: list[Event] = []
    d = controller(events).decide(
        spec("a", max_repairs=3, token_budget=100), tokens_used=500, elapsed_s=10
    )
    assert (d.thinking_off, d.max_repairs, d.exhausted) == (False, 3, None)
    assert events == []


def test_budget_over_70_percent_turns_thinking_off() -> None:
    events: list[Event] = []
    d = controller(events).decide(
        spec("a", max_repairs=3, token_budget=100), tokens_used=710, elapsed_s=0
    )
    assert d.thinking_off
    assert d.max_repairs == 3
    assert decisions(events) == ["thinking_off"]


def test_budget_over_85_percent_single_repair_by_time() -> None:
    events: list[Event] = []
    d = controller(events).decide(
        spec("a", max_repairs=3, token_budget=100), tokens_used=0, elapsed_s=90
    )
    assert d.thinking_off
    assert d.max_repairs == 1
    assert decisions(events) == ["thinking_off", "single_repair"]


def test_budget_clamps_stage_tokens_and_exhausts() -> None:
    events: list[Event] = []
    budget = controller(events, stages={"a": StageLimits(tokens=500)})
    d = budget.decide(spec("a"), tokens_used=600, elapsed_s=0)
    assert d.token_budget == 400
    assert decisions(events) == ["stage_tokens_clamped"]
    d = budget.decide(spec("a"), tokens_used=1000, elapsed_s=0)
    assert d.exhausted is not None
    assert "токены 1000/1000" in d.exhausted


def test_budget_stage_time_exceeded_warning() -> None:
    events: list[Event] = []
    budget = controller(events, stages={"a": StageLimits(time_s=5)})
    budget.after_stage(spec("a"), StageResult(agent="a", status=StageStatus.passed, duration_s=7))
    assert decisions(events) == ["stage_time_exceeded"]


def test_budget_from_run_config() -> None:
    limits = BudgetLimits.from_run_config(
        RunConfig(llm_base_url="x", model="m", global_token_budget=42, wall_clock_limit_s=9)
    )
    assert (limits.global_tokens, limits.wall_clock_s) == (42, 9)


def test_active_seconds_merges_overlaps() -> None:
    assert active_seconds([]) == 0
    assert active_seconds([[0, 10], [5, 12], [20, 25]]) == 17


def test_forced_thinking_overrides_agent_request() -> None:
    llm = FakeLLM([FakeReply(content="ok", expect=Expect(thinking=False))]).with_thinking(False)
    llm.chat([], thinking=True)


# --- workspace ----------------------------------------------------------------------------------


def test_manifest_lists_files_with_sha256(tmp_path: Path) -> None:
    ws = RunWorkspace(tmp_path)
    stage = ws.clean_outputs("web", ["*.ts"])
    (stage / "a.ts").write_text("a", "utf-8")
    (stage / "sub").mkdir()
    (stage / "sub" / "b.ts").write_text("b", "utf-8")
    (stage / ".specforge").mkdir()
    (stage / ".specforge" / "HEAD").write_text("git", "utf-8")

    manifest = ws.write_manifest("web", "coder")
    assert [e.path for e in manifest.files] == ["a.ts", "sub/b.ts"]
    assert manifest.files[0].sha256 == (
        "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"
    )
    assert manifest.agents == ["coder"]
    assert ws.verify("web") == []

    (stage / "a.ts").write_text("changed", "utf-8")
    (stage / "c.ts").write_text("c", "utf-8")
    assert ws.verify("web") == ["изменён a.ts", "лишний c.ts"]

    ws.clean_outputs("web", ["*.ts"])
    assert not (stage / "a.ts").exists()
    assert (stage / "sub" / "b.ts").exists()  # `*` не уходит в подкаталоги
