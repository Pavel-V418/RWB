"""Мост к части участника 3 (packages/specforge-backend): backend-агент и DevOps-агент.

Части написаны под свой stdlib-клиент LlmClient: chat(system, user, max_tokens, temperature) -> str
и usage.total. BackendLLM реализует этот протокол поверх LLMClient платформы (профиль CODE, без
thinking — так часть работала с Qwen3), поэтому учёт токенов, ретраи и телеметрия — платформенные.
Генераторы части — bash-скрипты; PYTHON указывает им интерпретатор платформы (нужен pyyaml),
SPECFORGE_PLATFORM=1 — раскладка артефактов для публикатора (.gitlab-ci.yml в 08_infra).
"""

import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from agents._team.bridge import diagnostic
from specforge.core.contracts import Diagnostic, ProfileName
from specforge.llm import ChatMessage, LLMClient


@dataclass
class BackendUsage:
    prompt: int = 0
    completion: int = 0
    calls: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion


class BackendLLM:
    """Протокол specforge_backend.backend.llm.LlmClient поверх LLMClient платформы."""

    def __init__(self, client: LLMClient, profile: ProfileName = ProfileName.CODE) -> None:
        self.client = client
        self.profile = profile
        self.usage = BackendUsage()

    def chat(
        self, system: str, user: str, max_tokens: int = 10000, temperature: float = 0.2
    ) -> str:
        response = self.client.chat(
            [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)],
            profile=self.profile,
            temperature=temperature,
            thinking=False,
            max_tokens=max_tokens,
        )
        self.usage.prompt += response.usage.prompt_tokens
        self.usage.completion += response.usage.completion_tokens
        self.usage.calls += 1
        return response.content


def platform_env() -> None:
    """Переменные для bash-генераторов части (их наследуют subprocess-вызовы)."""
    os.environ.setdefault("PYTHON", sys.executable)
    os.environ["SPECFORGE_PLATFORM"] = "1"


def gate_diagnostics(errors: Iterable[dict[str, Any]], tool: str) -> list[Diagnostic]:
    """{file, line, message} части → Diagnostic платформы."""
    return [
        diagnostic(tool, str(e.get("message", "")), file=e.get("file"), line=e.get("line"))
        for e in errors
    ]
