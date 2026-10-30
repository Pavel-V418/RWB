import pytest

from specforge.llm import ToolSpec, tool_error_message
from specforge.llm.tool_parsing import (
    extract_json,
    extract_tool_calls,
    parse_tool_call,
    split_reasoning,
    strip_think,
)

TOOLS = [
    ToolSpec(
        name="write_file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="run",
        parameters={
            "type": "object",
            "properties": {"cmd": {"type": "string"}, "timeout": {"type": "integer"}},
            "required": ["cmd"],
        },
    ),
]


def test_split_reasoning() -> None:
    assert split_reasoning("<think> a </think>ответ") == ("a", "ответ")
    assert split_reasoning("без размышлений") == (None, "без размышлений")
    assert split_reasoning("начало мысли</think>\nответ") == ("начало мысли", "ответ")
    assert strip_think("<think></think>x") == "x"


def test_extract_json() -> None:
    assert extract_json('Результат: {"a": [1, 2]} конец') == '{"a": [1, 2]}'
    assert extract_json("[1, 2]") == "[1, 2]"
    with pytest.raises(ValueError, match="нет JSON"):
        extract_json("пусто")


def test_hermes_xml_blocks() -> None:
    content = (
        "Пишу файл.\n"
        '<tool_call>\n{"name": "write_file", "arguments": {"path": "a.txt", "content": "x"}}\n'
        "</tool_call>\n"
        '<tool_call>{"name": "run", "arguments": "{\\"cmd\\": \\"ls\\"}"}</tool_call>'
    )
    calls, rest = extract_tool_calls(content, TOOLS)
    assert [(c.name, c.arguments) for c in calls] == [
        ("write_file", {"path": "a.txt", "content": "x"}),
        ("run", {"cmd": "ls"}),
    ]
    assert rest == "Пишу файл."


def test_qwen_coder_xml_with_type_coercion() -> None:
    content = (
        "<tool_call>\n<function=run>\n<parameter=cmd>\npytest -q\n</parameter>\n"
        "<parameter=timeout>\n30\n</parameter>\n</function>\n</tool_call>"
    )
    (call,) = extract_tool_calls(content, TOOLS)[0]
    assert call.valid
    assert call.arguments == {"cmd": "pytest -q", "timeout": 30}


def test_unclosed_tool_call_block_still_parsed() -> None:
    (call,) = extract_tool_calls('<tool_call>{"name": "run", "arguments": {"cmd": "ls"}}', TOOLS)[0]
    assert call.valid


def test_json_fence_and_bare_json() -> None:
    fenced = 'Вызов:\n```json\n{"name": "run", "arguments": {"cmd": "ls"}}\n```'
    calls, rest = extract_tool_calls(fenced, TOOLS)
    assert calls[0].name == "run"
    assert rest == "Вызов:"
    bare = (
        '[{"name": "run", "arguments": {"cmd": "a"}}, {"name": "run", "parameters": {"cmd": "b"}}]'
    )
    assert [c.arguments["cmd"] for c in extract_tool_calls(bare, TOOLS)[0]] == ["a", "b"]


def test_plain_json_answer_is_not_a_tool_call() -> None:
    content = '{"name": "Иван", "arguments": []}'
    assert extract_tool_calls(content, TOOLS) == ([], content)
    assert extract_tool_calls("просто текст", TOOLS) == ([], "просто текст")


@pytest.mark.parametrize(
    ("block", "error"),
    [
        ('<tool_call>{"name": "run", "arguments": {"cmd": </tool_call>', "не является JSON"),
        ('<tool_call>{"tool": "run"}</tool_call>', '"name"'),
        ('<tool_call>{"name": "rm", "arguments": {}}</tool_call>', "неизвестный инструмент 'rm'"),
        ('<tool_call>{"name": "run", "arguments": {}}</tool_call>', "обязательных аргументов: cmd"),
        ('<tool_call>{"name": "run", "arguments": [1]}</tool_call>', "JSON-объектом"),
        (
            '<tool_call>{"name": "write_file", "arguments": '
            '{"path": "a", "content": "", "mode": "w"}}</tool_call>',
            "неизвестные аргументы: mode",
        ),
    ],
)
def test_invalid_calls_produce_errors_for_model(block: str, error: str) -> None:
    (call,) = extract_tool_calls(block, TOOLS)[0]
    assert not call.valid
    assert call.error is not None
    assert error in call.error
    message = tool_error_message(call)
    assert "Исправь вызов" in message


def test_parse_tool_call_from_server_arguments() -> None:
    call = parse_tool_call("run", '{"cmd": "ls"}', TOOLS, "id1")
    assert (call.id, call.arguments, call.raw_arguments) == ("id1", {"cmd": "ls"}, '{"cmd": "ls"}')
    assert parse_tool_call("run", "", TOOLS).error == "не хватает обязательных аргументов: cmd"
