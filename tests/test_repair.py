"""repair_loop на FakeLLM + LocalSandbox + git-снимках."""

import dataclasses
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from specforge.core.agent_api import AgentContext, ToolAgent
from specforge.core.contracts import AgentSpec, GateResult, StageStatus
from specforge.core.events import Event
from specforge.gates.runner import GateRunner
from specforge.llm import Expect, FakeLLM, FakeReply, FakeToolCall
from specforge.orchestrator.nodes import execute_stage
from specforge.repair.fix_memory import InMemoryFixMemory, signature_of
from specforge.repair.loop import repair_loop
from specforge.repair.snapshots import Snapshotter
from specforge.sandbox.local_sandbox import LocalSandbox
from tests.conftest import MakeCtx

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="нужен git")

PY = f'"{sys.executable}"'
GATE = f"{PY} check.py"
PROMPTS = Path(__file__).parent / "fixtures" / "prompts"
CALC = "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"
CHECK = """import sys
from calc import add, mul

errors = []
if add(2, 3) != 5:
    errors.append("calc.py:2: error: add(2, 3) returned wrong value")
if mul(2, 3) != 6:
    errors.append("calc.py:6: error: mul(2, 3) returned wrong value")
print("\\n".join(errors))
sys.exit(1 if errors else 0)
"""


def call(name: str, expect: Expect | None = None, **arguments: Any) -> FakeReply:
    return FakeReply(tool_calls=[FakeToolCall(name, arguments)], expect=expect)


def finish(summary: str, expect: Expect | None = None) -> FakeReply:
    return call("finish", expect=expect, summary=summary)


def make_spec(**kwargs: Any) -> AgentSpec:
    defaults: dict[str, Any] = {
        "name": "coder",
        "stage": "code",
        "kind": "tool",
        "prompt_dir": str(PROMPTS),
        "gate_commands": [GATE],
        "gate_parsers": {GATE: "generic"},
        "max_repairs": 2,
    }
    return AgentSpec(**{**defaults, **kwargs})


@pytest.fixture
def setup(make_ctx: MakeCtx) -> Any:
    def factory(llm: FakeLLM, spec: AgentSpec) -> tuple[AgentContext, GateResult]:
        ctx = make_ctx(llm)
        sandbox = LocalSandbox(ctx.output_dir, logs_dir=ctx.workdir / "logs")
        ctx = dataclasses.replace(ctx, sandbox=sandbox, budget_tokens=500_000)
        (ctx.output_dir / "calc.py").write_text(CALC, "utf-8")
        (ctx.output_dir / "check.py").write_text(CHECK, "utf-8")
        gate = GateRunner(logs_dir=ctx.workdir / "logs").run(spec, sandbox, ctx.run_id)
        assert not gate.passed
        return ctx, gate

    return factory


def calc(ctx: AgentContext) -> str:
    return (ctx.output_dir / "calc.py").read_text("utf-8")


def kinds(events: list[Event]) -> list[str]:
    return [e.kind for e in events]


def test_fixed_on_second_attempt(setup: Any, events: list[Event]) -> None:
    spec = make_spec()
    llm = FakeLLM(
        [
            call("read_file", path="calc.py", expect=Expect(profile="REPAIR", thinking=False)),
            call("str_replace", path="calc.py", old="return a - b", new="return a + b + 1"),
            call("run", cmd=GATE),
            finish("поменял знак"),
            call(
                "str_replace",
                path="calc.py",
                old="return a + b + 1",
                new="return a + b",
                expect=Expect(contains="Предыдущие попытки"),
            ),
            finish("в add был неверный оператор и лишняя единица"),
        ]
    )
    ctx, gate = setup(llm, spec)
    memory = InMemoryFixMemory()

    result = repair_loop(spec, ctx, gate, memory=memory)

    assert result.status == StageStatus.passed, result.summary
    assert result.attempts == 2
    assert result.gate is not None
    assert result.gate.passed
    assert "return a + b\n" in calc(ctx)
    assert result.usage.calls == 6
    llm.assert_exhausted()
    assert kinds(events).count("repair_attempt") == 2
    assert "repair_succeeded" in kinds(events)
    assert "escalation" not in kinds(events)

    (fix,) = memory.items
    assert fix.fix_summary == "в add был неверный оператор и лишняя единица"
    assert "+    return a + b\n" in fix.diff
    assert fix.signature == signature_of(gate.diagnostics[0])
    # run в ремонте ограничен командами шлюза
    assert llm.calls[0].tool_names == ["read_file", "str_replace", "grep", "run", "finish"]


def test_rollback_when_errors_grow(setup: Any, events: list[Event]) -> None:
    spec = make_spec(max_repairs=1)
    llm = FakeLLM(
        [
            call("str_replace", path="calc.py", old="return a * b", new="return a / b"),
            call("write_file", path="extra.py", content="x = 1\n"),  # write_file недоступен
            finish("переписал mul"),
        ]
    )
    ctx, gate = setup(llm, spec)

    result = repair_loop(spec, ctx, gate, memory=InMemoryFixMemory())

    assert result.status == StageStatus.degraded
    assert calc(ctx) == CALC  # откат к снимку
    rolled = [e for e in events if e.kind == "repair_rolled_back"]
    assert rolled
    assert "ошибок стало больше: 1 → 2" in str(rolled[0].data["reason"])
    escalation = next(e for e in events if e.kind == "escalation")
    assert escalation.data["restored_green"] is False
    assert "неизвестный инструмент 'write_file'" in llm.calls[2].messages[-1].content


def test_degraded_restores_last_green_snapshot(setup: Any, events: list[Event]) -> None:
    spec = make_spec(max_repairs=1)
    llm = FakeLLM([finish("не знаю, как чинить")])
    ctx, _ = setup(llm, spec)
    assert ctx.sandbox is not None
    good = CALC.replace("a - b", "a + b")
    (ctx.output_dir / "calc.py").write_text(good, "utf-8")
    Snapshotter(ctx.sandbox).mark_green()
    (ctx.output_dir / "calc.py").write_text(CALC, "utf-8")  # этап сломал код
    (ctx.output_dir / "junk.py").write_text("broken = (\n", "utf-8")
    gate = GateRunner(logs_dir=ctx.workdir / "logs").run(spec, ctx.sandbox, ctx.run_id)

    result = repair_loop(spec, ctx, gate, memory=InMemoryFixMemory())

    assert result.status == StageStatus.degraded
    assert "зелёному снимку" in result.summary
    assert calc(ctx) == good
    assert not (ctx.output_dir / "junk.py").exists()
    assert next(e for e in events if e.kind == "escalation").data["restored_green"] is True


def test_protected_files_and_foreign_commands_refused(setup: Any, events: list[Event]) -> None:
    spec = make_spec(protected_globs=["contracts/**"])
    llm = FakeLLM(
        [
            call("str_replace", path="contracts/api.yaml", old="total", new="sum"),
            call("run", cmd="rm -rf /", expect=Expect(contains="защищён")),
            finish(
                "КОНТРАКТ: в схеме Order нет поля sum, код ждёт его",
                expect=Expect(contains="только команды шлюза"),
            ),
        ]
    )
    ctx, gate = setup(llm, spec)
    contract = ctx.output_dir / "contracts" / "api.yaml"
    contract.parent.mkdir()
    contract.write_text("Order:\n  total: number\n", "utf-8")

    result = repair_loop(spec, ctx, gate, memory=InMemoryFixMemory())

    assert result.status == StageStatus.degraded
    assert result.attempts == 1  # эскалация сразу, без второй попытки
    assert "КОНТРАКТ:" in result.summary
    assert contract.read_text("utf-8") == "Order:\n  total: number\n"
    llm.assert_exhausted()
    escalation = next(e for e in events if e.kind == "escalation")
    assert str(escalation.data["reason"]).startswith("КОНТРАКТ:")
    task = llm.calls[0].messages[1].content
    assert "contracts/**" in task  # защищённые файлы перечислены в задаче


def test_suppression_is_rolled_back(setup: Any, events: list[Event]) -> None:
    spec = make_spec()
    llm = FakeLLM(
        [
            call(
                "str_replace",
                path="calc.py",
                old="return a - b",
                new="return a + b  # type: ignore",
            ),
            finish("починил"),
            call(
                "str_replace",
                path="calc.py",
                old="return a - b",
                new="return a + b",
                expect=Expect(contains="запрещённая правка (# type: ignore / noqa)"),
            ),
            finish("починил без подавления"),
        ]
    )
    ctx, gate = setup(llm, spec)
    result = repair_loop(spec, ctx, gate, memory=InMemoryFixMemory())
    assert result.status == StageStatus.passed
    assert "type: ignore" not in calc(ctx)
    assert "repair_rolled_back" in kinds(events)


def test_known_fixes_are_offered(setup: Any, events: list[Event]) -> None:
    spec = make_spec(max_repairs=1)
    llm = FakeLLM(
        [
            call(
                "str_replace",
                path="calc.py",
                old="return a - b",
                new="return a + b",
                expect=Expect(contains="Известные решения"),
            ),
            finish("ok"),
        ]
    )
    ctx, gate = setup(llm, spec)
    memory = InMemoryFixMemory()
    signature = signature_of(gate.diagnostics[0])
    memory.record(signature, "в прошлый раз помогла замена минуса на плюс", "-a - b\n+a + b")

    result = repair_loop(spec, ctx, gate, memory=memory)

    assert result.status == StageStatus.passed
    assert "в прошлый раз помогла замена минуса на плюс" in llm.calls[0].messages[1].content
    assert "fix_memory_hits" in kinds(events)


def test_budget_exhausted_before_repair(setup: Any) -> None:
    spec = make_spec()
    llm = FakeLLM()
    ctx, gate = setup(llm, spec)
    ctx = dataclasses.replace(ctx, budget_tokens=0)
    result = repair_loop(spec, ctx, gate, memory=InMemoryFixMemory())
    assert result.status == StageStatus.degraded
    assert "бюджет" in result.summary
    assert result.attempts == 0


def test_execute_stage_repairs_red_gate(make_ctx: MakeCtx) -> None:
    class Coder(ToolAgent):
        spec = make_spec(tools=["write_file"], max_repairs=1)

    llm = FakeLLM(
        [
            call("write_file", path="calc.py", content=CALC),
            call("write_file", path="check.py", content=CHECK),
            finish("написал calc"),
            call("str_replace", path="calc.py", old="return a - b", new="return a + b"),
            finish("исправил add"),
        ]
    )
    ctx = make_ctx(llm)
    ctx = dataclasses.replace(ctx, budget_tokens=500_000)
    result = execute_stage(Coder(), ctx)

    assert result.status == StageStatus.passed, result.summary
    assert result.attempts == 2
    assert result.gate is not None
    assert result.gate.passed
    assert result.usage.calls == 5
    assert "ремонт: починено" in result.summary
    # зелёное состояние запомнено для будущих откатов
    sandbox = LocalSandbox(ctx.output_dir)
    assert Snapshotter(sandbox).green() is not None
