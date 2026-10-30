"""Инфраструктурный сбой или ошибка кода — по коду выхода, длительности и выводу команды.

Инфраструктурный сбой (OOM-kill, registry, упавший контейнер, нет места на диске) ремонт не
починит: этап повторяется без ремонта (не больше INFRA_MAX_RETRIES раз, событие stage_retry).
Ошибка компиляции, упавший тест и т.п. — обычный путь: ремонт.
"""

import os
import re

INFRA_MAX_RETRIES = 2
KILLED_EXIT_CODES = frozenset({137})
"""SIGKILL: OOM killer или docker kill."""
DEFAULT_KILL_MAX_S = 10.0
"""137 быстрее этого — убит системой, а не долгий тест, упёршийся в таймаут."""

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), reason)
    for pattern, reason in (
        (r"no space left on device|ENOSPC|disk quota exceeded", "нет места на диске"),
        (
            r"pull access denied|manifest (for .+ )?(not found|unknown)|error pulling image"
            r"|failed to resolve reference|toomanyrequests|unauthorized: authentication required"
            r"|registry[^\n]{0,80}(timeout|unavailable|refused|i/o timeout)",
            "ошибка pull / registry",
        ),
        (
            r"cannot connect to the docker daemon|is the docker daemon running"
            r"|OCI runtime (create|exec) failed|container [0-9a-f]{6,64} is not running"
            r"|no such container|container .{0,40} (died|exited unexpectedly)",
            "упал контейнер / Docker",
        ),
        (r"out of memory|oom-?kill|Killed process \d+|cannot allocate memory", "нехватка памяти"),
    )
)


def kill_max_s() -> float:
    return float(os.environ.get("SPECFORGE_INFRA_KILL_MAX_S", DEFAULT_KILL_MAX_S))


def classify_failure(exit_code: int, duration_s: float, output: str) -> str | None:
    """Причина инфраструктурного сбоя или None — ошибка кода (ремонт)."""
    if exit_code == 0:
        return None
    if exit_code in KILLED_EXIT_CODES and duration_s < kill_max_s():
        return f"процесс убит (код {exit_code}) через {duration_s:.1f} с — вероятно, OOM"
    tail = output[-20_000:]
    for pattern, reason in _PATTERNS:
        if pattern.search(tail):
            return reason
    return None
