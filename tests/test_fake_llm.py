import pytest
from pydantic import BaseModel

from specforge.llm import (
    ChatMessage,
    Expect,
    FakeExpectationError,
    FakeLLM,
    FakeReply,
    FakeToolCall,
    LLMError,
    LLMRequest,
    ProfileName,
    ToolSpec,
)

USER = [ChatMessage(role="user", content="привет")]
TOOL = ToolSpec(name="noop")


class Out(BaseModel):
    x: int


def test_script_kinds() -> None:
    def dynamic(request: LLMRequest) -> str:
        return f"эхо: {request.messages[-1].content}"

    llm = FakeLLM(
        [
            "текст",
            FakeReply(tool_calls=[FakeToolCall("noop")]),
            FakeReply(structured=Out(x=1)),
            dynamic,
        ]
    )
    assert llm.chat(USER).content == "текст"
    assert llm.chat(USER, tools=[TOOL]).tool_calls[0].name == "noop"
    assert llm.structured(USER, Out) == Out(x=1)
    assert llm.chat(USER).content == "эхо: привет"
    llm.assert_exhausted()


def test_expectations_pass_and_fail() -> None:
    llm = FakeLLM(
        [
            FakeReply(
                content="ok",
                expect=Expect(profile="REVIEW", thinking=True, tools=[], contains="прив"),
            ),
            FakeReply(content="нет", expect=Expect(profile="CODE", tools=["noop"])),
        ]
    )
    llm.chat(USER, profile=ProfileName.REVIEW)
    with pytest.raises(FakeExpectationError) as info:
        llm.chat(USER, profile=ProfileName.ANALYSIS)
    message = str(info.value)
    assert "вызов №2" in message
    assert "profile='ANALYSIS'" in message
    assert "tools=[]" in message


def test_expect_check_callable() -> None:
    llm = FakeLLM([FakeReply(expect=Expect(check=lambda r: r.max_tokens == 10))])
    with pytest.raises(FakeExpectationError, match="check"):
        llm.chat(USER)


def test_exhaustion_and_leftovers() -> None:
    llm = FakeLLM(["a", "b"])
    llm.chat(USER)
    with pytest.raises(FakeExpectationError, match="не использовано ответов: 1"):
        llm.assert_exhausted()
    llm.chat(USER)
    with pytest.raises(LLMError, match="закончились"):
        llm.chat(USER)


def test_token_estimate_counts_prompt_tools_and_completion() -> None:
    llm = FakeLLM([FakeReply(content="x" * 40, reasoning="y" * 40)])
    r = llm.chat([ChatMessage(role="user", content="z" * 400)], tools=[TOOL])
    assert r.usage.completion_tokens == 20
    assert r.usage.prompt_tokens > 100
    assert r.usage.calls == 1
