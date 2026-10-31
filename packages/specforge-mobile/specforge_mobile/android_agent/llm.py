"""
Клиент LLM с OpenAI-совместимым /chat/completions (Qwen организаторов, vLLM и т. п.).

Настройки только из переменных окружения — ключ в репозиторий не попадает:
  LLM_BASE_URL   например https://host/v1  (к нему добавляется /chat/completions)
  LLM_API_KEY    ключ (может быть пустым для локального сервера)
  LLM_MODEL      имя модели
  LLM_NO_THINK   1 (по умолчанию) — просить Qwen3 не рассуждать вслух: меньше токенов
  LLM_TIMEOUT    секунд на запрос (по умолчанию 300)

Интерфейс для агента: chat(messages, **opts) -> LLMReply(text, prompt_tokens, completion_tokens).
Если участник 1 передаст свой объект llm с другим интерфейсом — адаптер в agent._ask().
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

THINK = re.compile(r"<think>.*?</think>\s*", re.S)


@dataclass
class LLMReply:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class OpenAICompatLLM:
    def __init__(self, base_url: str, api_key: str, model: str, *, temperature: float = 0.2,
                 max_tokens: int = 8000, timeout: int = 300, no_think: bool = True, retries: int = 6):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key, self.model = api_key, model
        self.temperature, self.max_tokens, self.timeout = temperature, max_tokens, timeout
        self.no_think, self.retries = no_think, retries

    @classmethod
    def from_env(cls) -> "OpenAICompatLLM":
        missing = [k for k in ("LLM_BASE_URL", "LLM_MODEL") if not os.environ.get(k)]
        if missing:
            raise RuntimeError(f"Не заданы переменные окружения: {', '.join(missing)}")
        return cls(os.environ["LLM_BASE_URL"], os.environ.get("LLM_API_KEY", ""), os.environ["LLM_MODEL"],
                   timeout=int(os.environ.get("LLM_TIMEOUT", "300")),
                   no_think=os.environ.get("LLM_NO_THINK", "1") == "1")

    def chat(self, messages: list[dict], **opts) -> LLMReply:
        body = {"model": self.model, "messages": messages,
                "temperature": opts.get("temperature", self.temperature),
                "max_tokens": opts.get("max_tokens", self.max_tokens)}
        if self.no_think:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = json.dumps(body).encode("utf-8")

        last: Exception | None = None
        for attempt in range(self.retries):
            wait = min(60, 5 * 2 ** attempt)  # 5, 10, 20, 40, 60 с — сервер общий для всех команд
            try:
                req = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                text = payload["choices"][0]["message"].get("content") or ""
                usage = payload.get("usage") or {}
                return LLMReply(THINK.sub("", text).strip(), usage.get("prompt_tokens", 0),
                                usage.get("completion_tokens", 0))
            except urllib.error.HTTPError as e:
                last = e
                # сервер не знает chat_template_kwargs — повторяем без него
                if e.code == 400 and "chat_template_kwargs" in body:
                    body.pop("chat_template_kwargs")
                    data = json.dumps(body).encode("utf-8")
                    continue
                if e.code not in (408, 429, 500, 502, 503, 504):
                    raise RuntimeError(f"LLM HTTP {e.code}: {e.read()[:300]!r}") from e
                retry_after = e.headers.get("Retry-After") if e.headers else None
                if retry_after and retry_after.isdigit():
                    wait = min(120, int(retry_after) + 1)
            except (urllib.error.URLError, TimeoutError) as e:
                last = e
            time.sleep(wait)
        raise RuntimeError(f"LLM недоступна после {self.retries} попыток: {last}")
