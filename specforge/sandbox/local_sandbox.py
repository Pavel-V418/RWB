"""Песочница на локальном subprocess — для тестов и локального запуска без Docker.

Изоляции нет: команды выполняются на хосте (shell ОС) с рабочим каталогом внутри workspace.
Без `workspace` создаётся временный каталог, удаляемый в close().
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from specforge.sandbox.base import BaseSandbox

TIMEOUT_EXIT_CODE = 124


class LocalSandbox(BaseSandbox):
    def __init__(
        self,
        workspace: Path | None = None,
        *,
        logs_dir: Path | None = None,
        log_prefix: str = "",
    ) -> None:
        self._temporary = workspace is None
        root = Path(tempfile.mkdtemp(prefix="specforge-")) if workspace is None else workspace
        super().__init__(root, logs_dir=logs_dir, log_prefix=log_prefix)

    def _run(
        self, command: str, workdir: Path, timeout_s: float | None
    ) -> tuple[int, str, str, bool]:
        try:
            proc = subprocess.run(  # shell=True: команды пишет агент/автор шлюза, изоляции нет
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
            return TIMEOUT_EXIT_CODE, _as_text(exc.stdout), _as_text(exc.stderr), True
        return proc.returncode, proc.stdout, proc.stderr, False

    def close(self) -> None:
        if self._temporary:
            shutil.rmtree(self.workspace, ignore_errors=True)


def _as_text(data: str | bytes | None) -> str:
    if data is None:
        return ""
    return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
