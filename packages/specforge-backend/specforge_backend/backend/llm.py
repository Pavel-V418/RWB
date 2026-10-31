"""Клиент OpenAI-совместимого API (LiteLLM организаторов, Qwen3) без внешних зависимостей.

Настройки — из окружения (на vm5 — файл ~/.specforge.env, в git не попадает):
  SPECFORGE_LLM_URL      http://10.10.10.10:4000/v1
  SPECFORGE_LLM_MODEL    qwen3.8-27b
  SPECFORGE_LLM_API_KEY  ключ организаторов
  SPECFORGE_LLM_TPM      лимит токенов в минуту на ключ (по умолчанию 30000)

Лимит общий на ключ, поэтому клиент сам держит скользящее окно в 60 с и ждёт, а не получает 429.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def load_env_file(path: Path = Path.home() / ".specforge.env") -> None:
    """Подхватывает KEY=VALUE из файла, не перетирая уже заданные переменные."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def estimate_tokens(text: str) -> int:
    # Код и русский текст для Qwen-токенизатора: ~3 символа на токен (с запасом)
    return len(text) // 3 + 1


@dataclass
class Usage:
    prompt: int = 0
    completion: int = 0
    calls: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion


@dataclass
class LlmClient:
    url: str
    model: str
    api_key: str
    tokens_per_minute: int = 30_000
    timeout_s: int = 600
    usage: Usage = field(default_factory=Usage)
    _window: deque = field(default_factory=deque)  # (время, токены) за последние 60 с

    @classmethod
    def from_env(cls) -> "LlmClient":
        load_env_file()
        missing = [k for k in ("SPECFORGE_LLM_URL", "SPECFORGE_LLM_MODEL", "SPECFORGE_LLM_API_KEY") if not os.environ.get(k)]
        if missing:
            raise RuntimeError(f"не заданы переменные окружения: {', '.join(missing)} (см. ~/.specforge.env)")
        return cls(
            url=os.environ["SPECFORGE_LLM_URL"].rstrip("/"),
            model=os.environ["SPECFORGE_LLM_MODEL"],
            api_key=os.environ["SPECFORGE_LLM_API_KEY"],
            tokens_per_minute=int(os.environ.get("SPECFORGE_LLM_TPM", "30000")),
        )

    def _wait_for_budget(self, needed: int) -> None:
        while True:
            now = time.monotonic()
            while self._window and now - self._window[0][0] > 60:
                self._window.popleft()
            used = sum(t for _, t in self._window)
            if used + needed <= self.tokens_per_minute or not self._window:
                return
            time.sleep(max(1.0, 60 - (now - self._window[0][0]) + 0.5))

    def chat(self, system: str, user: str, max_tokens: int = 10000, temperature: float = 0.2) -> str:
        """Один запрос. Режим рассуждений Qwen3 выключен: для кода он тратит лимит без пользы."""
        estimate = estimate_tokens(system) + estimate_tokens(user) + max_tokens
        self._wait_for_budget(min(estimate, self.tokens_per_minute))
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user + "\n/no_think"}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }).encode()

        for attempt in range(5):
            request = urllib.request.Request(
                f"{self.url}/chat/completions", data=body, method="POST",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    data = json.loads(response.read())
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 4:
                    retry = e.headers.get("Retry-After")
                    time.sleep(float(retry) if retry and retry.isdigit() else 10 * (attempt + 1))
                    continue
                raise RuntimeError(f"LLM HTTP {e.code}: {e.read()[:500]!r}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < 4:
                    time.sleep(10 * (attempt + 1))
                    continue
                raise RuntimeError(f"LLM недоступна: {e}") from e

        usage = data.get("usage") or {}
        prompt = usage.get("prompt_tokens", estimate_tokens(system + user))
        completion = usage.get("completion_tokens", 0)
        self.usage.prompt += prompt
        self.usage.completion += completion
        self.usage.calls += 1
        self._window.append((time.monotonic(), prompt + completion))
        return THINK.sub("", data["choices"][0]["message"]["content"] or "")
