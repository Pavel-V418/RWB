"""Клиент LLM для агентов анализа.

* OpenAICompatLLM — любой OpenAI-совместимый сервер (vLLM с Qwen3.8-27B, Ollama, LM Studio).
  Структурный вывод через JSON Schema, переключение thinking, дисковый кэш, учёт метрик.
* FakeLLM — отдаёт заранее подготовленные JSON-ответы из папки. Нужен, чтобы отлаживать
  пайплайн и раздавать эталон команде без модели.

Каждый вызов пишет строку в metrics.jsonl: этап, токены, длительность, номер попытки,
причина повтора. Из этих строк собирается отчёт об эффективности.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol, TypeVar

import requests
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


@dataclass
class CallRecord:
    name: str
    input_tokens: int
    output_tokens: int
    duration_s: float
    attempt: int
    cached: bool = False
    ok: bool = True
    retry_reason: str | None = None
    thinking: bool = False
    model: str = ""


@dataclass
class Metrics:
    calls: list[CallRecord] = field(default_factory=list)
    sink: Path | None = None

    def add(self, rec: CallRecord) -> None:
        self.calls.append(rec)
        if self.sink:
            self.sink.parent.mkdir(parents=True, exist_ok=True)
            with self.sink.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")

    def summary(self, since: int = 0) -> dict:
        calls = self.calls[since:]
        return {
            "llm_calls": len(calls),
            "input_tokens": sum(c.input_tokens for c in calls),
            "output_tokens": sum(c.output_tokens for c in calls),
            "retries": sum(1 for c in calls if c.attempt > 1),
            "cached_calls": sum(1 for c in calls if c.cached),
            "duration_s": round(sum(c.duration_s for c in calls), 2),
        }


class LLM(Protocol):
    metrics: Metrics

    def structured(
        self,
        name: str,
        messages: list[dict],
        schema: type[T],
        *,
        thinking: bool = False,
        max_tokens: int = 8192,
    ) -> T: ...


# --------------------------------------------------------------------------- helpers

_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def extract_json(text: str) -> str:
    """Достаёт JSON-объект из ответа модели (срезает <think>, ```json и пояснения)."""
    text = _THINK_RE.sub("", text).strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        return fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("в ответе нет JSON-объекта")
    return text[start : end + 1]


def _dump_prompt(dump_dir: Path | None, name: str, messages: list[dict], extra: str = "") -> None:
    if not dump_dir:
        return
    dump_dir.mkdir(parents=True, exist_ok=True)
    body = "\n\n".join(f"### {m['role']}\n\n{m['content']}" for m in messages)
    (dump_dir / f"{name}.prompt.md").write_text(body + extra, encoding="utf-8")


# --------------------------------------------------------------------------- real client


def _rate_limit_delay(text: str) -> int:
    """Секунды до сброса лимита из ответа LiteLLM ("Limit resets at: 2026-10-30 15:11:09 UTC"), иначе 60."""
    m = re.search(r"resets at:?\s*(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d)", text)
    if m:
        try:
            reset = datetime.datetime.fromisoformat(m.group(1).replace(" ", "T")).replace(
                tzinfo=datetime.timezone.utc)
            sec = (reset - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
            return int(min(max(sec, 0) + 3, 120))
        except ValueError:
            pass
    return 60


@dataclass
class LLMSettings:
    base_url: str = "http://localhost:8000/v1"
    api_key: str = "EMPTY"
    model: str = "Qwen3.8-27B"
    structured_mode: str = "json_schema"  # json_schema | guided_json | prompt
    thinking_param: str = "chat_template_kwargs"  # chat_template_kwargs | none
    temperature: float = 0.1
    seed: int = 42
    timeout_s: int = 600
    max_attempts: int = 2  # 1 попытка + 1 повтор с текстом ошибки валидации
    cache_dir: str = ".cache/llm"
    tpm_limit: int = 0       # лимит токенов в минуту на ключ (0 — нет); ограничивает max_tokens запроса
    rate_wait_max: int = 15  # сколько раз ждать сброса лимита при HTTP 429

    @classmethod
    def from_env(cls) -> "LLMSettings":
        e = os.environ.get
        return cls(
            base_url=e("LLM_BASE_URL", cls.base_url).rstrip("/"),
            api_key=e("LLM_API_KEY", cls.api_key),
            model=e("LLM_MODEL", cls.model),
            structured_mode=e("LLM_STRUCTURED", cls.structured_mode),
            thinking_param=e("LLM_THINKING_PARAM", cls.thinking_param),
            temperature=float(e("LLM_TEMPERATURE", cls.temperature)),
            seed=int(e("LLM_SEED", cls.seed)),
            timeout_s=int(e("LLM_TIMEOUT", cls.timeout_s)),
            max_attempts=int(e("LLM_MAX_ATTEMPTS", cls.max_attempts)),
            cache_dir=e("LLM_CACHE_DIR", cls.cache_dir),
            tpm_limit=int(e("LLM_TPM_LIMIT", cls.tpm_limit)),
            rate_wait_max=int(e("LLM_RATE_WAIT_MAX", cls.rate_wait_max)),
        )


class OpenAICompatLLM:
    def __init__(self, settings: LLMSettings | None = None, metrics: Metrics | None = None,
                 dump_dir: Path | None = None):
        self.s = settings or LLMSettings.from_env()
        self.metrics = metrics or Metrics()
        self.dump_dir = dump_dir

    # -- cache
    def _cache_path(self, payload: dict) -> Path | None:
        if not self.s.cache_dir:
            return None
        key = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return Path(self.s.cache_dir) / f"{key}.json"

    def _post(self, body: dict) -> dict:
        url = f"{self.s.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.s.api_key}"}
        last: Exception | None = None
        waits = 0
        i = 0
        while i < 3:  # сетевые сбои и 5xx
            try:
                r = requests.post(url, json=body, headers=headers, timeout=self.s.timeout_s)
                if r.status_code == 429 and waits < self.s.rate_wait_max:
                    waits += 1
                    delay = _rate_limit_delay(r.text)
                    print(f"[llm] лимит запросов (429), жду {delay} с ({waits}/{self.s.rate_wait_max})",
                          file=sys.stderr, flush=True)
                    time.sleep(delay)
                    continue
                if r.status_code >= 500:
                    raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
                if r.status_code >= 400:
                    raise LLMError(f"HTTP {r.status_code}: {r.text[:500]}")  # не повторяем
                return r.json()
            except (requests.ConnectionError, requests.Timeout, LLMError) as exc:
                last = exc
                if isinstance(exc, LLMError) and "HTTP 4" in str(exc):
                    raise
                i += 1
                time.sleep(2 * i)
        raise LLMError(f"LLM недоступна: {last}")

    def structured(self, name, messages, schema, *, thinking=False, max_tokens=8192):
        json_schema = schema.model_json_schema()
        msgs = [dict(m) for m in messages]
        if self.s.structured_mode == "prompt":
            msgs[0]["content"] += (
                "\n\nОтвечай ТОЛЬКО JSON-объектом по схеме:\n```json\n"
                + json.dumps(json_schema, ensure_ascii=False) + "\n```"
            )
        _dump_prompt(self.dump_dir, name, msgs)

        if self.s.tpm_limit:
            est_in = len(json.dumps(msgs, ensure_ascii=False)) // 3 + 200
            max_tokens = max(1024, min(max_tokens, self.s.tpm_limit - est_in - 500))
        for attempt in range(1, self.s.max_attempts + 1):
            body: dict = {
                "model": self.s.model,
                "messages": msgs,
                "temperature": self.s.temperature,
                "seed": self.s.seed,
                "max_tokens": max_tokens,
            }
            if self.s.structured_mode == "json_schema":
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": schema.__name__, "schema": json_schema},
                }
            elif self.s.structured_mode == "guided_json":
                body["guided_json"] = json_schema
            if self.s.thinking_param == "chat_template_kwargs":
                body["chat_template_kwargs"] = {"enable_thinking": thinking}

            cache_file = self._cache_path(body)
            t0 = time.monotonic()
            if cache_file and cache_file.exists():
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                cached = True
            else:
                data = self._post(body)
                cached = False
            dt = time.monotonic() - t0

            usage = data.get("usage") or {}
            content = (data["choices"][0]["message"].get("content") or "")
            rec = CallRecord(
                name=name, attempt=attempt, cached=cached, thinking=thinking, model=self.s.model,
                input_tokens=0 if cached else int(usage.get("prompt_tokens", 0)),
                output_tokens=0 if cached else int(usage.get("completion_tokens", 0)),
                duration_s=round(dt, 2),
            )
            try:
                result = schema.model_validate_json(extract_json(content))
            except (ValidationError, LLMError, json.JSONDecodeError) as exc:
                rec.ok = False
                rec.retry_reason = str(exc)[:500]
                self.metrics.add(rec)
                if attempt == self.s.max_attempts:
                    raise LLMError(f"{name}: невалидный ответ после {attempt} попыток: {exc}") from exc
                msgs = msgs + [
                    {"role": "assistant", "content": content[:4000]},
                    {"role": "user", "content": "Ответ не прошёл валидацию схемы. Исправь и верни "
                                                f"полный JSON заново. Ошибка:\n{str(exc)[:1500]}"},
                ]
                continue
            self.metrics.add(rec)
            if cache_file and not cached:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return result
        raise LLMError("недостижимо")


# --------------------------------------------------------------------------- fake client


class FakeLLM:
    """Отдаёт <fixtures_dir>/<name>.json. Токены оцениваются как длина/4 и помечаются cached=True,
    чтобы прогоны на фикстурах не смешивались с честными замерами."""

    def __init__(self, fixtures_dir: str | Path, metrics: Metrics | None = None,
                 dump_dir: Path | None = None):
        self.dir = Path(fixtures_dir)
        self.metrics = metrics or Metrics()
        self.dump_dir = dump_dir

    def has(self, name: str) -> bool:
        return (self.dir / f"{name}.json").exists()

    def structured(self, name, messages, schema, *, thinking=False, max_tokens=8192):
        _dump_prompt(self.dump_dir, name, messages)
        path = self.dir / f"{name}.json"
        if not path.exists():
            raise LLMError(f"FakeLLM: нет фикстуры {path}")
        raw = path.read_text(encoding="utf-8")
        prompt_chars = sum(len(m["content"]) for m in messages)
        self.metrics.add(CallRecord(
            name=name, attempt=1, cached=True, thinking=thinking, model="fake",
            input_tokens=prompt_chars // 4, output_tokens=len(raw) // 4, duration_s=0.0,
        ))
        return schema.model_validate_json(raw)


def make_llm(spec: str | None, metrics: Metrics, dump_dir: Path | None = None) -> LLM:
    """spec: None/'openai' -> настоящий клиент из переменных окружения; 'fake:<dir>' -> FakeLLM."""
    if spec and spec.startswith("fake:"):
        return FakeLLM(spec[5:], metrics=metrics, dump_dir=dump_dir)
    return OpenAICompatLLM(metrics=metrics, dump_dir=dump_dir)
