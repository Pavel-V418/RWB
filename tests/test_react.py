import sys
from pathlib import Path
from typing import Any

from specforge.core.agent_api import AgentContext, agent_loop
from specforge.core.contracts import Usage
from specforge.core.events import Event
from specforge.executor.react import LoopSettings, run_react_loop
from specforge.llm import Expect, FakeLLM, FakeReply, FakeToolCall, LLMRequest, LLMUnavailableError
from tests.conftest import MakeCtx

PY = f'"{sys.executable}"'
ALL_TOOLS = ["read_file", "write_file", "str_replace", "list_dir", "grep", "run"]


def call(name: str, expect: Expect | None = None, **arguments: Any) -> FakeReply:
    return FakeReply(tool_calls=[FakeToolCall(name, arguments)], expect=expect)


def finish(summary: str = "готово", expect: Expect | None = None) -> FakeReply:
    return call("finish", expect=expect, summary=summary)


def last_message(text: str) -> Expect:
    return Expect(contains=text)


def loop(ctx: AgentContext, tools: list[str] = ALL_TOOLS, **kwargs: Any) -> Any:
    settings = kwargs.pop("settings", LoopSettings())
    return run_react_loop(
        ctx,
        "Ты — агент.",
        "Почини функцию add.",
        tools,
        max_steps=kwargs.pop("max_steps", 10),
        temperature=0.2,
        thinking=False,
        settings=settings,
    )


def test_read_replace_run_finish(make_ctx: MakeCtx, events: list[Event]) -> None:
    llm = FakeLLM(
        [
            call("read_file", path="app.py"),
            call(
                "str_replace",
                expect=last_message("return a - b"),
                path="/work/app.py",
                old="return a - b",
                new="return a + b",
            ),
            call("run", expect=last_message("заменено в app.py"), cmd=f"{PY} check.py"),
            finish("add исправлена", expect=last_message("exit 0")),
        ]
    )
    ctx = make_ctx(llm)
    (ctx.output_dir / "app.py").write_text("def add(a, b):\n    return a - b\n", "utf-8")
    (ctx.output_dir / "check.py").write_text(
        "from app import add\nassert add(2, 3) == 5\nprint('OK')\n", "utf-8"
    )

    outcome = agent_loop(ctx, "Ты — агент.", "Почини функцию add.", ALL_TOOLS)

    assert outcome.stopped_reason == "finish"
    assert outcome.summary == "add исправлена"
    assert outcome.steps == 4
    assert outcome.changed_files == ["app.py"]
    assert outcome.usage.calls == 4
    assert "return a + b" in (ctx.output_dir / "app.py").read_text("utf-8")
    llm.assert_exhausted()

    kinds = [e.kind for e in events]
    assert kinds.count("step_started") == 4
    assert kinds.count("tool_called") == 3
    assert kinds.count("tool_result") == 3
    assert kinds[-1] == "agent_finished"
    assert events[-1].data["stopped_reason"] == "finish"
    run_result = [e for e in events if e.kind == "tool_result"][-1]
    assert run_result.data["tool"] == "run"
    assert run_result.data["ok"] is True
    assert list((ctx.workdir / "logs").glob("*.log"))  # полный лог run


def test_prefix_is_byte_identical_between_calls(make_ctx: MakeCtx) -> None:
    llm = FakeLLM([call("list_dir"), call("grep", pattern="x"), finish()])
    loop(make_ctx(llm))
    prefixes = {tuple(m.model_dump_json() for m in request.messages[:2]) for request in llm.calls}
    tools = {tuple(t.model_dump_json() for t in request.tools) for request in llm.calls}
    assert len(prefixes) == 1
    assert len(tools) == 1
    # история только растёт: каждый запрос — продолжение предыдущего
    for previous, current in zip(llm.calls, llm.calls[1:], strict=False):
        assert current.messages[: len(previous.messages)] == previous.messages


def test_several_calls_in_one_response_run_in_order(make_ctx: MakeCtx) -> None:
    llm = FakeLLM(
        [
            FakeReply(
                tool_calls=[
                    FakeToolCall("write_file", {"path": "a.txt", "content": "первый"}),
                    FakeToolCall("read_file", {"path": "a.txt"}),
                ]
            ),
            finish(expect=last_message("первый")),
        ]
    )
    outcome = loop(make_ctx(llm))
    assert outcome.stopped_reason == "finish"
    roles = [m.role for m in llm.calls[1].messages]
    assert roles == ["system", "user", "assistant", "tool", "tool"]


def test_tool_errors_are_returned_to_model(make_ctx: MakeCtx) -> None:
    llm = FakeLLM(
        [
            call("str_replace", path="app.py", old="нет такого", new="x"),
            call("delete_everything", expect=last_message("не найден")),
            call("read_file", expect=last_message("неизвестный инструмент"), path="../secret"),
            call("write_file", expect=last_message("за пределы /work"), path="app.py", content="y"),
            finish(expect=last_message("используй str_replace")),
        ]
    )
    ctx = make_ctx(llm)
    (ctx.output_dir / "app.py").write_text("x = 1\n", "utf-8")
    outcome = loop(ctx)
    assert outcome.stopped_reason == "finish"
    assert outcome.changed_files == []
    llm.assert_exhausted()


def test_loop_detector_stops_on_third_identical_call(make_ctx: MakeCtx) -> None:
    llm = FakeLLM([call("list_dir", path=".")] * 3 + [finish()])
    outcome = loop(make_ctx(llm))
    assert outcome.stopped_reason == "loop"
    assert "3 раза подряд" in outcome.summary
    assert outcome.steps == 3
    assert llm.remaining == 1
    tool_results = [m for m in llm.calls[-1].messages if m.role == "tool"]
    assert len(tool_results) == 2  # третий одинаковый вызов не выполнялся


def test_different_arguments_are_not_a_loop(make_ctx: MakeCtx) -> None:
    llm = FakeLLM(
        [
            call("list_dir", path="."),
            call("list_dir", path=".", depth=1),
            call("list_dir", path="."),
            finish(),
        ]
    )
    assert loop(make_ctx(llm)).stopped_reason == "finish"


def test_text_without_tool_calls_is_nudged_then_stopped(make_ctx: MakeCtx) -> None:
    llm = FakeLLM(
        ["я подумаю", FakeReply(content="ещё подумаю", expect=last_message("finish")), "всё"]
    )
    outcome = loop(make_ctx(llm))
    assert outcome.stopped_reason == "loop"
    assert "без вызова инструментов" in outcome.summary


def test_max_steps(make_ctx: MakeCtx) -> None:
    llm = FakeLLM([call("grep", pattern=str(i)) for i in range(5)])
    outcome = loop(make_ctx(llm), max_steps=3)
    assert (outcome.stopped_reason, outcome.steps) == ("max_steps", 3)


def test_token_budget(make_ctx: MakeCtx) -> None:
    big = Usage(prompt_tokens=9_000, completion_tokens=2_000, calls=1)
    llm = FakeLLM([FakeReply(tool_calls=[FakeToolCall("list_dir")], usage=big), finish()])
    outcome = loop(make_ctx(llm))  # budget_tokens=10_000 в make_ctx
    assert outcome.stopped_reason == "token_budget"
    assert outcome.steps == 1
    assert outcome.usage.total_tokens == 11_000


def test_llm_failure_ends_loop_with_error(make_ctx: MakeCtx) -> None:
    llm = FakeLLM([call("list_dir"), LLMUnavailableError("GPU упал")])
    outcome = loop(make_ctx(llm))
    assert outcome.stopped_reason == "error"
    assert "GPU упал" in outcome.summary


def test_unknown_tool_in_spec(make_ctx: MakeCtx) -> None:
    llm = FakeLLM()
    outcome = loop(make_ctx(llm), tools=["read_file", "teleport"])
    assert outcome.stopped_reason == "error"
    assert "teleport" in outcome.summary
    assert llm.calls == []


def test_only_spec_tools_are_offered(make_ctx: MakeCtx) -> None:
    llm = FakeLLM([call("run", cmd="echo", expect=Expect(tools=["read_file", "finish"])), finish()])
    loop(make_ctx(llm), tools=["read_file"])
    assert "неизвестный инструмент 'run'" in llm.calls[1].messages[-1].content


def test_protected_globs(make_ctx: MakeCtx) -> None:
    llm = FakeLLM(
        [
            call("str_replace", path="tests/contract/api.txt", old="v1", new="v2"),
            finish(expect=last_message("защищён")),
        ]
    )
    ctx = make_ctx(llm)
    ctx.protected_globs = ["tests/contract/**"]
    (ctx.output_dir / "tests" / "contract").mkdir(parents=True)
    (ctx.output_dir / "tests" / "contract" / "api.txt").write_text("v1", "utf-8")
    loop(ctx)
    assert (ctx.output_dir / "tests" / "contract" / "api.txt").read_text("utf-8") == "v1"


def test_compaction_replaces_old_results_with_stubs(make_ctx: MakeCtx, events: list[Event]) -> None:
    def noisy(i: int) -> FakeReply:
        return call("run", cmd=f"{PY} -c \"print('шум-{i} ' * 400)\"")

    llm = FakeLLM([noisy(1), noisy(2), noisy(3), finish()])
    settings = LoopSettings(compact_at=500, keep_last=1)
    outcome = loop(make_ctx(llm), settings=settings)
    assert outcome.stopped_reason == "finish"

    last: LLMRequest = llm.calls[-1]
    tool_messages = [m.content for m in last.messages if m.role == "tool"]
    assert tool_messages[0].startswith("[run: exit 0, ")
    assert "лог:" in tool_messages[0]
    assert "шум-3" in tool_messages[-1]  # последний шаг целиком
    assert last.messages[:2] == llm.calls[0].messages[:2]
    assert any(e.kind == "history_compacted" for e in events)


def test_tool_result_event_is_truncated(make_ctx: MakeCtx, events: list[Event]) -> None:
    llm = FakeLLM([call("run", cmd=f"{PY} -c \"print('x' * 3000)\""), finish()])
    loop(make_ctx(llm))
    (result,) = [e for e in events if e.kind == "tool_result"]
    assert isinstance(result.data["result"], str)
    assert len(result.data["result"]) <= 501


def test_relative_logs_dir_inside_run(make_ctx: MakeCtx) -> None:
    llm = FakeLLM([call("run", cmd=f"{PY} -c pass"), finish()])
    ctx = make_ctx(llm)
    loop(ctx)
    logs = list((ctx.workdir / "logs").glob("*.log"))
    assert len(logs) == 1
    assert Path(logs[0]).read_text("utf-8").startswith("$ ")
