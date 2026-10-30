"""Запуск шлюза этапа: команды из AgentSpec.gate_commands в песочнице.

Пока каждая упавшая команда даёт одну Diagnostic с хвостом вывода. Точные Diagnostic
(файл/строка/код) появятся с парсерами из gates/parsers/.
"""

import time
from collections.abc import Sequence
from pathlib import Path

from specforge.core.contracts import Diagnostic, GateResult
from specforge.sandbox.base import Sandbox

DEFAULT_TAIL_CHARS = 2000


def truncate_tail(text: str, limit: int) -> str:
    """Оставить конец текста: в логах сборки ошибки обычно внизу."""
    if len(text) <= limit:
        return text
    return f"…[усечено {len(text) - limit} симв.]\n{text[-limit:]}"


def run_gate(
    commands: Sequence[str],
    sandbox: Sandbox,
    *,
    log_path: Path,
    cwd: str | None = None,
    timeout_s: float | None = 600.0,
    tail_chars: int = DEFAULT_TAIL_CHARS,
) -> GateResult:
    started = time.monotonic()
    diagnostics: list[Diagnostic] = []
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        for command in commands:
            res = sandbox.exec(command, cwd=cwd, timeout_s=timeout_s)
            log.write(f"$ {command}\n[exit {res.exit_code}]\n{res.stdout}\n{res.stderr}\n\n")
            if res.exit_code == 0:
                continue
            reason = (
                "превысила таймаут" if res.timed_out else f"завершилась с кодом {res.exit_code}"
            )
            output = (res.stdout + "\n" + res.stderr).strip()
            diagnostics.append(
                Diagnostic(
                    tool=command.split(maxsplit=1)[0] if command.strip() else "gate",
                    code=f"exit:{res.exit_code}",
                    message=f"`{command}` {reason}\n{truncate_tail(output, tail_chars)}",
                )
            )
    errors = sum(d.severity == "error" for d in diagnostics)
    return GateResult(
        passed=errors == 0,
        diagnostics=diagnostics,
        total_errors=errors,
        log_path=str(log_path),
        duration_s=time.monotonic() - started,
    )
