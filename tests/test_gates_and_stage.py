import sys
from pathlib import Path

from pydantic import BaseModel

from specforge.core.agent_api import StructuredAgent
from specforge.core.contracts import AgentSpec, StageResult, StageStatus
from specforge.gates.runner import run_gate, truncate_tail
from specforge.llm import FakeLLM
from specforge.orchestrator.nodes import execute_stage
from specforge.sandbox.local_sandbox import LocalSandbox
from tests.conftest import MakeCtx

PY = f'"{sys.executable}"'
PROMPTS = Path(__file__).parent / "fixtures" / "prompts"


def test_gate_passes_and_fails(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path)
    ok = run_gate([f"{PY} -c pass"], sandbox, log_path=tmp_path / "ok.log")
    assert ok.passed
    assert ok.total_errors == 0

    bad = run_gate(
        [f"{PY} -c pass", f"{PY} -c \"import sys; print('boom'); sys.exit(3)\""],
        sandbox,
        log_path=tmp_path / "bad.log",
    )
    assert not bad.passed
    assert bad.total_errors == 1
    assert bad.diagnostics[0].code == "exit:3"
    assert "boom" in bad.diagnostics[0].message
    assert "[exit 3]" in (tmp_path / "bad.log").read_text("utf-8")


def test_gate_timeout(tmp_path: Path) -> None:
    gate = run_gate(
        [f'{PY} -c "import time; time.sleep(5)"'],
        LocalSandbox(tmp_path),
        log_path=tmp_path / "t.log",
        timeout_s=0.5,
    )
    assert not gate.passed
    assert "таймаут" in gate.diagnostics[0].message


def test_truncate_tail() -> None:
    assert truncate_tail("abc", 10) == "abc"
    assert truncate_tail("x" * 50 + "END", 3).endswith("\nEND")


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


def test_execute_stage_catches_agent_crash(make_ctx: MakeCtx) -> None:
    class Crashing:
        spec = AgentSpec(name="crash", stage="test", kind="tool")

        def run(self, ctx: object) -> StageResult:
            raise RuntimeError("упал")

    result = execute_stage(Crashing(), make_ctx(FakeLLM()))
    assert result.status == StageStatus.failed
    assert "упал" in result.summary
