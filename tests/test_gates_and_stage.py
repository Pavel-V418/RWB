import sys
from pathlib import Path

from pydantic import BaseModel

from specforge.core.agent_api import StructuredAgent
from specforge.core.contracts import AgentSpec, StageResult, StageStatus
from specforge.llm import FakeLLM
from specforge.orchestrator.nodes import execute_stage
from tests.conftest import MakeCtx

PY = f'"{sys.executable}"'
PROMPTS = Path(__file__).parent / "fixtures" / "prompts"


class Out(BaseModel):
    text: str


def _gated_agent(gate_commands: list[str]) -> StructuredAgent[Out]:
    class Gated(StructuredAgent[Out]):
        spec = AgentSpec(
            name="gated",
            stage="test",
            kind="structured",
            prompt_dir=str(PROMPTS),
            gate_commands=gate_commands,
        )
        schema = Out
        output_file = "out.json"

    return Gated()


def test_execute_stage_runs_gate_in_output_dir(make_ctx: MakeCtx) -> None:
    check = f"{PY} -c \"import json; json.load(open('out.json', encoding='utf-8'))\""
    ctx = make_ctx(FakeLLM(['{"text": "ok"}']))
    result = execute_stage(_gated_agent([check]), ctx)
    assert result.status == StageStatus.passed
    assert result.gate is not None
    assert result.gate.passed
    assert (ctx.workdir / "logs" / "gated.gate.log").exists()


def test_execute_stage_red_gate_fails_stage(make_ctx: MakeCtx) -> None:
    ctx = make_ctx(FakeLLM(['{"text": "ok"}']))
    result = execute_stage(_gated_agent([f'{PY} -c "raise SystemExit(1)"']), ctx)
    assert result.status == StageStatus.failed
    assert result.gate is not None
    assert result.gate.total_errors == 1


def test_execute_stage_unknown_gate_parser(make_ctx: MakeCtx) -> None:
    class Gated(StructuredAgent[Out]):
        spec = AgentSpec(
            name="gated",
            stage="test",
            kind="structured",
            prompt_dir=str(PROMPTS),
            gate_commands=["make"],
            gate_parsers={"make": "cobol"},
        )
        schema = Out

    result = execute_stage(Gated(), make_ctx(FakeLLM(['{"text": "ok"}'])))
    assert result.status == StageStatus.failed
    assert "cobol" in result.summary


def test_execute_stage_catches_agent_crash(make_ctx: MakeCtx) -> None:
    class Crashing:
        spec = AgentSpec(name="crash", stage="test", kind="tool")

        def run(self, ctx: object) -> StageResult:
            raise RuntimeError("упал")

    result = execute_stage(Crashing(), make_ctx(FakeLLM()))
    assert result.status == StageStatus.failed
    assert "упал" in result.summary
