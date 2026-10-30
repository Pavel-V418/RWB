"""Песочница на локальном subprocess — для тестов и локального запуска агентов без Docker."""

import subprocess
import time
from pathlib import Path

from specforge.sandbox.base import ExecResult

TIMEOUT_EXIT_CODE = 124


class LocalSandbox:
    def __init__(self, root: Path) -> None:
        self.root = root

    def exec(
        self, command: str, *, cwd: str | None = None, timeout_s: float | None = None
    ) -> ExecResult:
        workdir = self.root / cwd if cwd else self.root
        started = time.monotonic()
        try:
            proc = subprocess.run(  # shell=True: команды шлюза пишет автор агента
                command,
                shell=True,
                cwd=workdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return ExecResult(
                command=command,
                exit_code=TIMEOUT_EXIT_CODE,
                stdout=_as_text(exc.stdout),
                stderr=_as_text(exc.stderr),
                duration_s=time.monotonic() - started,
                timed_out=True,
            )
        return ExecResult(
            command=command,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_s=time.monotonic() - started,
        )


def _as_text(data: str | bytes | None) -> str:
    if data is None:
        return ""
    return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
