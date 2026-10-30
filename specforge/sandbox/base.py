"""Интерфейс песочницы. Предварительный: расширится вместе с DockerSandbox."""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class ExecResult(BaseModel):
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    timed_out: bool = False


@runtime_checkable
class Sandbox(Protocol):
    def exec(
        self, command: str, *, cwd: str | None = None, timeout_s: float | None = None
    ) -> ExecResult:
        """Выполнить shell-команду; `cwd` — относительно корня песочницы."""
        ...
