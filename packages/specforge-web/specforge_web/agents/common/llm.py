"""LLM-клиент SpecForge для OpenAI-совместимого эндпоинта (vLLM с Qwen3.8-27B, Ollama и т.п.).

Реализует протокол contracts.agent.agent_contract.LLM. Без внешних зависимостей (urllib).
Это временная реализация участника 4: участник 1 может подменить её своим клиентом с тем же интерфейсом.

Переменные окружения:
  SPECFORGE_LLM_URL    базовый URL, напр. http://vllm:8000/v1
  SPECFORGE_LLM_MODEL  имя модели, напр. Qwen/Qwen3.8-27B
  SPECFORGE_LLM_KEY    ключ (для демонстрационного ключа организаторов)
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from specforge_web.contracts.agent.agent_contract import LLMResponse

# Ключевые слова JSON Schema, которые grammar-движки guided decoding (xgrammar/outlines) часто не поддерживают.
_UNSUPPORTED = {"if", "then", "else", "default", "examples", "$id", "$schema", "format", "propertyNames", "dependentRequired"}


def guided_schema(schema: Any) -> Any:
    """Упрощённая копия схемы для guided decoding: без if/then и аннотаций. Полная схема остаётся для шлюза."""
    if isinstance(schema, dict):
        out = {}
        for k, v in schema.items():
            if k in _UNSUPPORTED:
                continue
            if k == "allOf" and isinstance(v, list) and all(isinstance(x, dict) and "if" in x for x in v):
                continue
            out[k] = guided_schema(v)
        return out
    if isinstance(schema, list):
        return [guided_schema(x) for x in schema]
    return schema


def extract_json(text: str) -> Any:
    """JSON из ответа модели: без <think>, без ```-ограждений, первый сбалансированный объект/массив."""
    text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("в ответе модели нет JSON")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if in_str:
            esc = (ch == "\\") and not esc
            if ch == '"' and not esc:
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("JSON в ответе модели не закрыт")


@dataclass
class Usage:
    """Накопительная телеметрия агента (для GateResult и отчёта об эффективности)."""
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    calls: int = 0
    seconds: float = 0.0
    by_tag: dict[str, dict[str, float]] = field(default_factory=dict)

    def add(self, tag: str, r: LLMResponse, seconds: float) -> None:
        self.tokens_in += r.tokens_in
        self.tokens_out += r.tokens_out
        self.tokens_cached += r.tokens_cached
        self.calls += 1
        self.seconds += seconds
        t = self.by_tag.setdefault(tag or "-", {"calls": 0, "tokensIn": 0, "tokensOut": 0, "seconds": 0.0})
        t["calls"] += 1
        t["tokensIn"] += r.tokens_in
        t["tokensOut"] += r.tokens_out
        t["seconds"] = round(t["seconds"] + seconds, 2)


class OpenAICompatLLM:
    def __init__(self, base_url: str | None = None, model: str | None = None, api_key: str | None = None,
                 timeout: float = 600, retries: int = 2):
        self.base_url = (base_url or os.environ.get("SPECFORGE_LLM_URL", "http://localhost:8000/v1")).rstrip("/")
        self.model = model or os.environ.get("SPECFORGE_LLM_MODEL", "Qwen/Qwen3.8-27B")
        self.api_key = api_key or os.environ.get("SPECFORGE_LLM_KEY", "")
        self.timeout, self.retries = timeout, retries
        # SPECFORGE_LLM_PLAIN=1 — эндпоинт без guided decoding и chat_template_kwargs; включается и сам после HTTP 400/422
        self.plain = os.environ.get("SPECFORGE_LLM_PLAIN", "") == "1"

    def complete(self, messages: list[dict[str, Any]], *, json_schema: dict | None = None, thinking: bool = False,
                 max_tokens: int = 8192, temperature: float = 0.2, tag: str = "") -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if not self.plain:
            # Qwen3.x: включение/выключение режима рассуждений через chat template
            body["chat_template_kwargs"] = {"enable_thinking": thinking}
            if json_schema is not None:
                body["response_format"] = {"type": "json_schema", "json_schema": {"name": tag or "output", "schema": guided_schema(json_schema)}}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(f"{self.base_url}/chat/completions", data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                msg = payload["choices"][0]["message"]
                usage = payload.get("usage") or {}
                cached = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
                return LLMResponse(text=msg.get("content") or "", tokens_in=usage.get("prompt_tokens", 0),
                                   tokens_out=usage.get("completion_tokens", 0), tokens_cached=cached)
            except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as e:
                last = e
                if isinstance(e, urllib.error.HTTPError) and e.code in (400, 422) and not self.plain:
                    # Шлюз модели не знает guided decoding / chat_template_kwargs (например, демонстрационный ключ
                    # организаторов): дальше шлём обычный запрос, JSON проверяют extract_json и шлюзы.
                    self.plain = True
                    body.pop("chat_template_kwargs", None)
                    body.pop("response_format", None)
                    continue
                if isinstance(e, urllib.error.HTTPError) and e.code < 500 and e.code != 429:
                    break
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"LLM недоступна: {last}")


class ReplayLLM:
    """Тестовая LLM: отвечает функцией responder(messages, tag) → str (или dict → json)."""

    def __init__(self, responder: Callable[[list[dict[str, Any]], str], Any]):
        self.responder = responder
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages, *, json_schema=None, thinking=False, max_tokens=8192, temperature=0.2, tag="") -> LLMResponse:
        out = self.responder(messages, tag)
        text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        prompt = sum(len(str(m.get("content", ""))) for m in messages) // 4
        self.calls.append({"tag": tag, "thinking": thinking, "schema": json_schema is not None})
        return LLMResponse(text=text, tokens_in=prompt, tokens_out=len(text) // 4)


class TrackedLLM:
    """Обёртка: считает токены/время и парсит JSON с одной повторной попыткой при битом ответе."""

    def __init__(self, llm, usage: Usage | None = None):
        self.llm, self.usage = llm, usage or Usage()

    def json(self, messages: list[dict[str, Any]], schema: dict | None, *, tag: str, thinking: bool = False,
             max_tokens: int = 8192) -> Any:
        for attempt in range(2):
            t0 = time.time()
            r = self.llm.complete(messages, json_schema=schema, thinking=thinking, max_tokens=max_tokens, tag=tag)
            self.usage.add(tag, r, time.time() - t0)
            try:
                return extract_json(r.text)
            except (ValueError, json.JSONDecodeError) as e:
                if attempt:
                    raise
                messages = messages + [{"role": "assistant", "content": r.text[:2000]},
                                       {"role": "user", "content": f"Ответ не является корректным JSON ({e}). Верни только JSON."}]
        raise AssertionError("unreachable")


def llm_from_env():
    return OpenAICompatLLM()
