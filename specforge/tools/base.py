"""Каркас инструментов агента: описание для модели + реализация над песочницей."""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from pydantic import BaseModel, JsonValue

from specforge.llm import ToolSpec
from specforge.sandbox.base import Sandbox

Args = Mapping[str, JsonValue]


class ToolError(Exception):
    """Ошибка, понятная модели: её текст вернётся как результат вызова."""


class ToolResult(BaseModel):
    content: str
    """Что увидит модель."""
    stub: str
    """Короткая заглушка, которой compaction заменит content в старой истории."""
    changed: str | None = None
    """Путь изменённого файла (относительно /work), если инструмент менял файлы."""


@dataclass
class ToolEnv:
    sandbox: Sandbox
    protected_globs: Sequence[str] = field(default_factory=tuple)
    max_run_timeout_s: float = 1800.0


ToolFn = Callable[[ToolEnv, Args], ToolResult]


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    fn: ToolFn

    @property
    def name(self) -> str:
        return self.spec.name


# --- аргументы -----------------------------------------------------------------------------------


def arg_str(args: Args, name: str, default: str | None = None) -> str:
    value = args.get(name, default)
    if not isinstance(value, str):
        raise ToolError(f"аргумент {name!r} должен быть строкой")
    return value


def arg_int(args: Args, name: str, default: int | None = None) -> int | None:
    value = args.get(name, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ToolError(f"аргумент {name!r} должен быть целым числом")
    try:
        return int(value)
    except ValueError:
        raise ToolError(f"аргумент {name!r} должен быть целым числом") from None


# --- пути ----------------------------------------------------------------------------------------


def relative(env: ToolEnv, path: Path) -> str:
    rel = path.resolve().relative_to(env.sandbox.workspace.resolve()).as_posix()
    return "." if rel in ("", ".") else rel


def check_writable(env: ToolEnv, rel_path: str) -> None:
    for pattern in env.protected_globs:
        if glob_match(rel_path, pattern):
            raise ToolError(f"файл {rel_path} защищён от изменений (protected_globs: {pattern})")


def glob_match(path: str, pattern: str) -> bool:
    """Glob по POSIX-пути: `*` и `?` не пересекают `/`, `**` — любое число каталогов."""
    return _glob_regex(pattern).fullmatch(path) is not None


@cache
def _glob_regex(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


# --- вывод ---------------------------------------------------------------------------------------


def truncate_middle(text: str, head: int, tail: int, note: str = "") -> str:
    """Оставить начало и конец текста; в середину — пометку о пропуске."""
    if len(text) <= head + tail:
        return text
    skipped = len(text) - head - tail
    marker = f"\n…[пропущено {skipped} символов{'; ' + note if note else ''}]…\n"
    return text[:head] + marker + text[len(text) - tail :]
