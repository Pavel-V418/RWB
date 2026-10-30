"""Langfuse: trace на прогон (id из run_id) и generation на каждый вызов LLM — хук LLMClient.

Включается, если заданы LANGFUSE_PUBLIC_KEY и LANGFUSE_SECRET_KEY (LANGFUSE_HOST — адрес
self-hosted Langfuse); иначе no-op. Вложенных span-ов нет: generation-ы лежат прямо в trace.
Ошибка Langfuse не мешает вызову LLM.
"""

import os
from collections.abc import Mapping
from typing import Any

import structlog

from specforge.llm import LLMClient
from specforge.llm.usage import LLMCallRecord

log = structlog.get_logger(__name__)

REQUIRED_ENV = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")


class LangfuseHook:
    """on_call-хук: generation в trace прогона."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self._failed = False

    def __call__(self, record: LLMCallRecord) -> None:
        if self._failed:
            return
        try:
            self._send(record)
        except Exception:
            self._failed = True  # недоступный Langfuse не должен тормозить каждый вызов
            log.exception("langfuse.failed")

    def _send(self, record: LLMCallRecord) -> None:
        run_id = record.run_id or "no-run"
        trace_id = self.client.create_trace_id(seed=run_id)
        generation = self.client.start_observation(
            trace_context={"trace_id": trace_id},
            name=f"{record.stage or '-'}/{record.agent or '-'}",
            as_type="generation",
            model=record.model,
            usage_details={
                "input": record.usage.prompt_tokens,
                "output": record.usage.completion_tokens,
                "cached": record.usage.cached_tokens,
            },
            metadata={
                "run_id": run_id,
                "stage": record.stage,
                "agent": record.agent,
                "profile": record.profile,
                "attempts": record.attempts,
                "latency_s": record.latency_s,
                "tool_calls": record.tool_calls,
                "finish_reason": record.finish_reason,
                "schema": record.structured_schema,
            },
            level="ERROR" if record.error else "DEFAULT",
            status_message=record.error,
        )
        generation.end()

    def flush(self) -> None:
        if not self._failed:
            self.client.flush()


def langfuse_hook_from_env(env: Mapping[str, str] | None = None) -> LangfuseHook | None:
    """Хук по LANGFUSE_*; None — Langfuse не настроен или недоступен."""
    env = os.environ if env is None else env
    if not all(env.get(k) for k in REQUIRED_ENV):
        return None
    try:
        from langfuse import Langfuse  # noqa: PLC0415 — SDK нужен только с ключами

        client = Langfuse(
            public_key=env["LANGFUSE_PUBLIC_KEY"],
            secret_key=env["LANGFUSE_SECRET_KEY"],
            host=env.get("LANGFUSE_HOST") or None,
        )
    except Exception:
        log.exception("langfuse.init_failed")
        return None
    return LangfuseHook(client)


def install_langfuse(llm: LLMClient, env: Mapping[str, str] | None = None) -> LangfuseHook | None:
    """Подключить Langfuse к LLMClient, если он настроен."""
    hook = langfuse_hook_from_env(env)
    if hook is not None:
        llm.add_hook(hook)
    return hook
