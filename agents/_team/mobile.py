"""Мост к части участника 5 (packages/specforge-mobile): Android-агент и этап тестов/безопасности.

Часть написана под свой stdlib-клиент: chat(messages, **opts) -> LLMReply(text, prompt_tokens,
completion_tokens) — так вызывают и Android-агент, и LLM-триаж находок сканеров. MobileLLM реализует
этот протокол поверх LLMClient платформы (профиль CODE, без thinking — так часть работала с Qwen3),
поэтому учёт токенов, ретраи и телеметрия — платформенные.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from specforge_mobile.android_agent.llm import LLMReply

from agents._team.bridge import diagnostic
from specforge.core.contracts import Diagnostic, ProfileName
from specforge.llm import ChatMessage, LLMClient

DEFAULT_MAX_TOKENS = 8000


@dataclass
class MobileUsage:
    prompt: int = 0
    completion: int = 0
    calls: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion


class MobileLLM:
    """Протокол specforge_mobile.android_agent.llm.OpenAICompatLLM поверх LLMClient платформы."""

    def __init__(self, client: LLMClient, profile: ProfileName = ProfileName.CODE) -> None:
        self.client = client
        self.profile = profile
        self.usage = MobileUsage()

    def chat(self, messages: list[dict[str, Any]], **opts: Any) -> Any:
        response = self.client.chat(
            [ChatMessage(role=m["role"], content=str(m.get("content") or "")) for m in messages],
            profile=self.profile,
            temperature=float(opts.get("temperature", 0.2)),
            thinking=False,
            max_tokens=int(opts.get("max_tokens", DEFAULT_MAX_TOKENS)),
        )
        prompt, completion = response.usage.prompt_tokens, response.usage.completion_tokens
        self.usage.prompt += prompt
        self.usage.completion += completion
        self.usage.calls += 1
        return LLMReply(response.content, prompt, completion)


def mobile_diagnostics(errors: Iterable[dict[str, Any]], tool: str) -> list[Diagnostic]:
    """{file, line, message, category} части → Diagnostic платформы (file пустой — без файла)."""
    return [
        diagnostic(
            str(e.get("tool") or tool),
            str(e.get("message", "")),
            code=str(e["category"]) if e.get("category") else None,
            file=str(e["file"]) if e.get("file") else None,
            line=int(e["line"]) if e.get("line") else None,
        )
        for e in errors
    ]


def notes_diagnostics(notes: Iterable[str], tool: str) -> list[Diagnostic]:
    return [diagnostic(tool, note, severity="warning") for note in notes]
