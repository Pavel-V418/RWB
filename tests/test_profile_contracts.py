"""Профиль в AgentSpec, его передача агентами и циклами; новые поля контрактов; EventBus."""

import sys
import warnings
from pathlib import Path
from typing import get_type_hints

import pytest
from pydantic import BaseModel

from specforge.core.agent_api import StructuredAgent, ToolAgent, agent_loop
from specforge.core.contracts import (
    AgentSpec,
    GateResult,
    PipelineState,
    ProfileName,
    StageResult,
    StageStatus,
)
from specforge.core.events import Event, EventBus
from specforge.gates.runner import GateRunner
from specforge.llm import Expect, FakeLLM, FakeReply, FakeToolCall
from specforge.sandbox.local_sandbox import LocalSandbox
from tests.conftest import MakeCtx

PROMPTS = Path(__file__).parent / "fixtures" / "prompts"
PY = f'"{sys.executable}"'


class Echo(BaseModel):
    text: str


# --- AgentSpec ----------------------------------------------------------------------------------


def test_agent_spec_profile_defaults() -> None:
    spec = AgentSpec(name="a", stage="s", kind="tool")
    assert spec.profile == ProfileName.CODE
    assert spec.temperature is None
    assert spec.thinking is None
    assert AgentSpec(name="a", stage="s", kind="tool", profile="ANALYSIS").profile == "ANALYSIS"
    with pytest.raises(ValueError, match="profile"):
        AgentSpec(name="a", stage="s", kind="tool", profile="FAST")


def test_agent_spec_thinking_low_temperature_raised() -> None:
    with pytest.warns(UserWarning, match="AgentSpec coder"):
        spec = AgentSpec(name="coder", stage="s", kind="tool", thinking=True, temperature=0.2)
    assert spec.temperature == 0.6
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # без предупреждений в допустимых случаях
        assert (
            AgentSpec(name="a", stage="s", kind="tool", thinking=True, temperature=0.7).temperature
            == 0.7
        )
        assert (
            AgentSpec(name="a", stage="s", kind="tool", thinking=False, temperature=0.1).temperature
            == 0.1
        )


def test_new_optional_fields_and_pipeline_state() -> None:
    gate = GateResult(passed=True)
    assert (gate.failed_command_index, gate.tests_executed) == (None, None)
    assert StageResult(agent="a", status=StageStatus.passed).stopped_reason is None
    assert "events" not in get_type_hints(PipelineState, include_extras=True)


# --- профиль доходит до запроса -----------------------------------------------------------------


def test_structured_agent_uses_spec_profile(make_ctx: MakeCtx) -> None:
    class Analyst(StructuredAgent[Echo]):
        spec = AgentSpec(
            name="analyst",
            stage="s",
            kind="structured",
            prompt_dir=str(PROMPTS),
            profile="ANALYSIS",
        )
        schema = Echo

    llm = FakeLLM(
        [FakeReply(structured=Echo(text="x"), expect=Expect(profile="ANALYSIS", thinking=True))]
    )
    result = Analyst().run(make_ctx(llm))
    assert result.status == StageStatus.passed
    request = llm.calls[0]
    assert (request.temperature, request.max_tokens) == (0.6, 16384)


def test_spec_overrides_only_non_none_fields(make_ctx: MakeCtx) -> None:
    class Reviewer(StructuredAgent[Echo]):
        spec = AgentSpec(
            name="reviewer",
            stage="s",
            kind="structured",
            prompt_dir=str(PROMPTS),
            profile="REVIEW",
            temperature=0.9,
        )
        schema = Echo

    llm = FakeLLM([FakeReply(structured=Echo(text="x"))])
    Reviewer().run(make_ctx(llm))
    request = llm.calls[0]
    assert (request.profile, request.thinking, request.temperature) == ("REVIEW", True, 0.9)
    assert request.top_p == 0.95  # из профиля
    assert request.max_tokens == 8192


def test_tool_agent_uses_spec_profile_and_reports_stop_reason(make_ctx: MakeCtx) -> None:
    class Coder(ToolAgent):
        spec = AgentSpec(
            name="coder",
            stage="s",
            kind="tool",
            prompt_dir=str(PROMPTS),
            profile="REPAIR",
            thinking=False,
        )

    finish = FakeReply(
        tool_calls=[FakeToolCall("finish", {"summary": "ok"})], expect=Expect(profile="REPAIR")
    )
    llm = FakeLLM([finish])
    result = Coder().run(make_ctx(llm))
    assert result.stopped_reason == "finish"
    assert (llm.calls[0].temperature, llm.calls[0].max_tokens) == (0.1, 4096)


def test_agent_loop_defaults_to_code_profile(make_ctx: MakeCtx) -> None:
    llm = FakeLLM([FakeReply(tool_calls=[FakeToolCall("finish", {"summary": "ok"})])])
    agent_loop(make_ctx(llm), "s", "t", [])
    request = llm.calls[0]
    assert (request.profile, request.thinking, request.temperature) == ("CODE", False, 0.2)


# --- шлюз и события -----------------------------------------------------------------------------


def test_gate_reports_failed_command_index(tmp_path: Path) -> None:
    spec = AgentSpec(
        name="g",
        stage="s",
        kind="tool",
        gate_commands=[f"{PY} -c pass", f'{PY} -c "raise SystemExit(1)"', f"{PY} -c pass"],
    )
    gate = GateRunner(runs_dir=tmp_path / "runs").run(spec, LocalSandbox(tmp_path / "w"), "r")
    assert gate.failed_command_index == 1
    ok = spec.model_copy(update={"gate_commands": [f"{PY} -c pass"]})
    assert (
        GateRunner(runs_dir=tmp_path / "runs")
        .run(ok, LocalSandbox(tmp_path / "w"), "r")
        .failed_command_index
        is None
    )


def test_event_bus_fan_out_and_isolation() -> None:
    bus = EventBus()
    got: list[str] = []

    def broken(event: Event) -> None:
        raise RuntimeError("подписчик упал")

    bus.subscribe(broken)
    unsubscribe = bus.subscribe(lambda e: got.append(e.kind))
    bus.emit(Event(kind="a"))
    unsubscribe()
    bus.emit(Event(kind="b"))
    assert got == ["a"]
