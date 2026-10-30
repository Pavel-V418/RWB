"""Smoke-проверка LLM-эндпоинта: чат, tool call, json_schema, thinking on/off.

    uv run python infra/smoke_llm.py

Читает .env (LLM_BASE_URL, LLM_MODEL, ...). Без LLM_BASE_URL — пропуск с кодом 0.
Печатает таблицу: проверка, результат, токены (prompt/completion/cached), latency.
Код выхода 1, если хоть одна проверка не прошла.
"""

import io
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

from dotenv import load_dotenv
from pydantic import BaseModel

from specforge.core.contracts import Usage
from specforge.llm import ChatMessage, LLMClient, LLMError, ProfileName, ToolSpec

MAX_TOKENS = 4096

ADD_TOOL = ToolSpec(
    name="add",
    description="Сложить два целых числа.",
    parameters={
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    },
)


class Arithmetic(BaseModel):
    expression: str
    result: int


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    usage: Usage
    latency_s: float


def _user(text: str) -> list[ChatMessage]:
    return [ChatMessage(role="user", content=text)]


def check_chat(llm: LLMClient) -> CheckResult:
    r = llm.chat(_user("Ответь одним словом: столица Франции?"), max_tokens=MAX_TOKENS)
    ok = bool(r.content.strip()) and not r.tool_calls
    return CheckResult("chat", ok, r.content.strip()[:60], r.usage, r.latency_s)


def check_tool_call(llm: LLMClient) -> CheckResult:
    r = llm.chat(
        _user("Сколько будет 17 + 25? Обязательно вызови инструмент add."),
        tools=[ADD_TOOL],
        max_tokens=MAX_TOKENS,
    )
    call = r.tool_calls[0] if r.tool_calls else None
    ok = (
        call is not None
        and call.valid
        and call.name == "add"
        and call.arguments
        == {
            "a": 17,
            "b": 25,
        }
    )
    detail = f"{call.name}({call.arguments_json()})" if call else f"нет tool call: {r.content[:60]}"
    if call and call.error:
        detail += f" ошибка: {call.error}"
    return CheckResult("tool_call", ok, detail, r.usage, r.latency_s)


def check_json_schema(llm: LLMClient) -> CheckResult:
    started = time.monotonic()
    with llm.track() as meter:
        value = llm.structured(_user("Вычисли 6 * 7."), Arithmetic, max_tokens=MAX_TOKENS)
    latency = time.monotonic() - started
    return CheckResult(
        "json_schema", value.result == 42, value.model_dump_json(), meter.usage, latency
    )


def check_thinking(llm: LLMClient, enabled: bool) -> CheckResult:
    profile = ProfileName.ANALYSIS if enabled else ProfileName.CODE
    r = llm.chat(
        _user("Сколько будет 12 * 13? Ответь числом."), profile=profile, max_tokens=MAX_TOKENS
    )
    has_reasoning = bool(r.reasoning and r.reasoning.strip())
    ok = has_reasoning == enabled and "156" in r.content
    detail = f"reasoning={len(r.reasoning or '')} симв., ответ={r.content.strip()[:30]!r}"
    return CheckResult(f"thinking_{'on' if enabled else 'off'}", ok, detail, r.usage, r.latency_s)


def check_thinking_on(llm: LLMClient) -> CheckResult:
    return check_thinking(llm, enabled=True)


def check_thinking_off(llm: LLMClient) -> CheckResult:
    return check_thinking(llm, enabled=False)


CHECKS: list[Callable[[LLMClient], CheckResult]] = [
    check_chat,
    check_tool_call,
    check_json_schema,
    check_thinking_on,
    check_thinking_off,
]


def run_checks(llm: LLMClient) -> list[CheckResult]:
    results = []
    for check in CHECKS:
        try:
            results.append(check(llm))
        except LLMError as exc:
            name = getattr(check, "__name__", "check").removeprefix("check_")
            results.append(
                CheckResult(name, False, f"{type(exc).__name__}: {exc}"[:200], Usage(), 0)
            )
    return results


def format_table(results: list[CheckResult]) -> str:
    header = ("проверка", "итог", "prompt", "compl", "cached", "latency")
    lines = ["{:14} {:5} {:>7} {:>7} {:>7} {:>8}  детали".format(*header)]
    for r in results:
        u = r.usage
        verdict = "OK" if r.ok else "FAIL"
        lines.append(
            f"{r.name:14} {verdict:5} {u.prompt_tokens:>7} {u.completion_tokens:>7} "
            f"{u.cached_tokens:>7} {r.latency_s:>7.2f}s  {r.detail}"
        )
    return "\n".join(lines)


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):  # кириллица в pipe на Windows (cp1251)
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    if not os.environ.get("LLM_BASE_URL"):
        print("SKIP: LLM_BASE_URL не задан")
        return 0
    llm = LLMClient.from_env()
    print(f"эндпоинт {llm.config.base_url}, модель {llm.config.model}")
    results = run_checks(llm)
    print(format_table(results))
    total = llm.usage
    print(
        f"всего: {total.calls} вызовов, {total.total_tokens} токенов, cached {total.cached_tokens}"
    )
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
