import json
from pathlib import Path

from pydantic import BaseModel

from specforge.core.agent_api import Agent, StructuredAgent, ToolAgent, agent_loop
from specforge.core.contracts import AgentSpec, StageStatus
from specforge.core.events import Event
from specforge.llm import FakeLLM
from tests.conftest import MakeCtx

PROMPTS = Path(__file__).parent / "fixtures" / "prompts"


class Echo(BaseModel):
    text: str


class EchoAgent(StructuredAgent[Echo]):
    spec = AgentSpec(
        name="echo", stage="test", kind="structured", inputs=["**/*.txt"], prompt_dir=str(PROMPTS)
    )
    schema = Echo
    output_file = "nested/echo.json"


class WriterAgent(ToolAgent):
    spec = AgentSpec(
        name="writer", stage="test", kind="tool", outputs=["*.md"], prompt_dir=str(PROMPTS)
    )


def test_agent_loop_single_call(make_ctx: MakeCtx, events: list[Event]) -> None:
    llm = FakeLLM(["<think>план</think>Готово."])
    outcome = agent_loop(make_ctx(llm), "система", "задача", ["read_file"])
    assert outcome.final_message == "Готово."
    assert outcome.steps == 1
    assert outcome.usage.calls == 1
    assert [m.role for m in llm.calls[0].messages] == ["system", "user"]
    assert events[0].kind == "agent.step"


def test_structured_agent_reads_inputs_and_writes_json(make_ctx: MakeCtx) -> None:
    llm = FakeLLM(['{"text": "привет"}'])
    ctx = make_ctx(llm)
    (ctx.input_dir / "sub").mkdir()
    (ctx.input_dir / "sub" / "b.txt").write_text("второй", "utf-8")
    (ctx.input_dir / "a.txt").write_text("первый", "utf-8")

    agent = EchoAgent()
    assert isinstance(agent, Agent)
    result = agent.run(ctx)

    assert result.status == StageStatus.passed, result.summary
    assert result.artifacts == ["nested/echo.json"]
    assert result.usage.calls == 1
    saved = json.loads((ctx.output_dir / "nested" / "echo.json").read_text("utf-8"))
    assert saved == {"text": "привет"}
    system, user = llm.calls[0].messages
    assert system.content.startswith("Ты — тестовый агент")
    assert user.content.index('path="a.txt"') < user.content.index('path="sub/b.txt"')
    assert "Входы:" in user.content


def test_structured_agent_fails_without_inputs(make_ctx: MakeCtx) -> None:
    llm = FakeLLM()
    result = EchoAgent().run(make_ctx(llm))
    assert result.status == StageStatus.failed
    assert "нет входных файлов" in result.summary
    assert llm.calls == []


def test_structured_agent_reports_llm_failure(make_ctx: MakeCtx) -> None:
    ctx = make_ctx(FakeLLM(["мусор", "снова мусор"]))
    (ctx.input_dir / "a.txt").write_text("x", "utf-8")
    result = EchoAgent().run(ctx)
    assert result.status == StageStatus.failed
    assert result.usage.calls == 2


def test_tool_agent_uses_task_template(make_ctx: MakeCtx) -> None:
    llm = FakeLLM(["сделано"])
    ctx = make_ctx(llm)
    (ctx.output_dir / "notes.md").write_text("x", "utf-8")
    result = WriterAgent().run(ctx)
    assert result.status == StageStatus.passed
    assert result.summary == "сделано"
    assert result.artifacts == ["notes.md"]
    assert ctx.output_dir.as_posix() in llm.calls[0].messages[1].content
