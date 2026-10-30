"""Песочница: где выполняются команды агента и шлюза и лежат его файлы.

Рабочий каталог песочницы (workspace) — каталог на хосте; внутри песочницы он виден как /work.
Файловые операции идут через `resolve()`: путь может быть относительным или начинаться с /work,
выйти за /work нельзя ни через `..`, ни через симлинки.
"""

import itertools
import re
import threading
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import TracebackType
from typing import Protocol, Self, runtime_checkable

from pydantic import BaseModel

WORKDIR = "/work"


class SandboxError(Exception):
    """Песочница недоступна или не смогла выполнить команду."""


class SandboxPathError(SandboxError):
    """Путь указывает за пределы /work."""


class ExecResult(BaseModel):
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    timed_out: bool = False
    log_path: str | None = None
    """Полный лог команды на хосте (runs/<id>/logs/...)."""

    @property
    def output(self) -> str:
        """stdout и stderr одним текстом."""
        if self.stdout and self.stderr:
            return f"{self.stdout.rstrip()}\n{self.stderr}"
        return self.stdout or self.stderr


@runtime_checkable
class Sandbox(Protocol):
    workspace: Path
    """Каталог на хосте, видимый в песочнице как /work."""

    def exec(
        self, command: str, *, timeout_s: float | None = None, cwd: str | None = None
    ) -> ExecResult:
        """Выполнить shell-команду; `cwd` — относительно /work."""
        ...

    def resolve(self, path: str) -> Path:
        """Путь агента (`src/a.py`, `/work/src/a.py`) → путь на хосте.

        SandboxPathError, если путь ведёт за пределы /work.
        """
        ...

    def read_text(self, path: str) -> str: ...

    def write_text(self, path: str, content: str) -> None: ...

    def close(self) -> None: ...


class BaseSandbox(ABC):
    """Общее для песочниц: пути внутри workspace, файловые операции, лог каждой команды."""

    def __init__(self, workspace: Path, *, logs_dir: Path | None = None, log_prefix: str = ""):
        workspace.mkdir(parents=True, exist_ok=True)
        self.workspace = workspace
        self.logs_dir = logs_dir
        self.log_prefix = log_prefix or uuid.uuid4().hex[:6]
        self._seq = itertools.count(1)
        self._seq_lock = threading.Lock()

    # --- пути и файлы -------------------------------------------------------------------------

    def resolve(self, path: str) -> Path:
        root = self.workspace.resolve()
        target = (root / to_workspace_relative(path)).resolve()
        if not target.is_relative_to(root):
            raise SandboxPathError(f"путь {path!r} указывает за пределы {WORKDIR}")
        return target

    def display(self, host_path: Path) -> str:
        """Путь на хосте → путь относительно /work для сообщений модели."""
        rel = host_path.resolve().relative_to(self.workspace.resolve()).as_posix()
        return "." if rel == "." else rel

    def read_text(self, path: str) -> str:
        return self.resolve(path).read_text(encoding="utf-8", errors="replace")

    def write_text(self, path: str, content: str) -> None:
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")

    # --- команды ------------------------------------------------------------------------------

    def exec(
        self, command: str, *, timeout_s: float | None = None, cwd: str | None = None
    ) -> ExecResult:
        workdir = self.resolve(cwd or ".")
        started = time.monotonic()
        exit_code, stdout, stderr, timed_out = self._run(command, workdir, timeout_s)
        result = ExecResult(
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_s=time.monotonic() - started,
            timed_out=timed_out,
        )
        log_path = self._write_log(result, cwd or ".")
        return result.model_copy(update={"log_path": str(log_path) if log_path else None})

    @abstractmethod
    def _run(
        self, command: str, workdir: Path, timeout_s: float | None
    ) -> tuple[int, str, str, bool]:
        """Выполнить команду в `workdir` (путь на хосте внутри workspace).
        Возвращает (exit_code, stdout, stderr, timed_out)."""

    def _write_log(self, result: ExecResult, cwd: str) -> Path | None:
        if self.logs_dir is None:
            return None
        with self._seq_lock:
            seq = next(self._seq)
        program = result.command.split(maxsplit=1)[0] if result.command.strip() else "cmd"
        program = PurePosixPath(
            program.strip("\"'").replace("\\", "/")
        ).name  # "C:\py.exe" → py.exe
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", program)[:30] or "cmd"
        path = self.logs_dir / f"{self.log_prefix}-{seq:03d}-{slug}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"$ {result.command}\n"
            f"cwd: {WORKDIR}/{cwd}  exit: {result.exit_code}  "
            f"duration: {result.duration_s:.2f}s  timed_out: {result.timed_out}\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n",
            encoding="utf-8",
        )
        return path

    # --- жизненный цикл -----------------------------------------------------------------------

    def close(self) -> None:  # noqa: B027 — по умолчанию освобождать нечего
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def to_workspace_relative(path: str) -> str:
    """`/work/a/b` → `a/b`, `./a` → `./a`; абсолютный путь вне /work — SandboxPathError."""
    p = path.strip().replace("\\", "/") or "."
    if p == WORKDIR or p.startswith(WORKDIR + "/"):
        return p[len(WORKDIR) :].lstrip("/") or "."
    if PurePosixPath(p).is_absolute() or PureWindowsPath(p).drive:
        raise SandboxPathError(f"путь {path!r} указывает за пределы {WORKDIR}")
    return p
