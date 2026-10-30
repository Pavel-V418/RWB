"""Общее для парсеров вывода шлюза."""

import json
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from specforge.core.contracts import Diagnostic

MAX_MESSAGE_CHARS = 1500
SKIP_DIRS = frozenset(
    {".git", ".specforge", "node_modules", "bin", "obj", ".gradle", "__pycache__", ".venv"}
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


@dataclass
class ParseContext:
    command: str
    workspace: Path
    """Каталог на хосте, видимый в песочнице как /work (там же — отчёты тестов)."""
    exit_code: int = 0
    started_at: float = field(default_factory=time.time)
    """Время запуска команды (epoch): отчёты старше — от прошлых запусков."""


ParseFn = Callable[[str, ParseContext], list[Diagnostic]]


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def clip(message: str, limit: int = MAX_MESSAGE_CHARS) -> str:
    message = message.strip()
    return message if len(message) <= limit else message[:limit] + "…"


def load_json(text: str) -> object | None:
    """JSON из вывода команды: целиком или первый документ, начинающийся с `[`/`{` в строке."""
    text = strip_ansi(text).strip()
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        return parsed
    decoder = json.JSONDecoder()
    for match in re.finditer(r"^[\[{]", text, re.MULTILINE):
        try:
            value: object = decoder.raw_decode(text, match.start())[0]
        except json.JSONDecodeError:
            continue
        return value
    return None


def fresh_reports(ctx: ParseContext, patterns: tuple[str, ...]) -> Iterator[Path]:
    """Файлы отчётов в workspace, созданные этим запуском команды."""
    root = ctx.workspace
    seen: set[Path] = set()
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if path in seen or not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
                continue
            if path.stat().st_mtime < ctx.started_at - 2:  # запас на грубое разрешение mtime
                continue
            seen.add(path)
            yield path


def find_source(ctx: ParseContext, file_name: str) -> str | None:
    """Найти файл по имени (стек JVM даёт только имя): путь относительно workspace."""
    for path in ctx.workspace.rglob(file_name):
        rel = path.relative_to(ctx.workspace)
        if path.is_file() and not any(p in SKIP_DIRS or p == "build" for p in rel.parts):
            return rel.as_posix()
    return None
