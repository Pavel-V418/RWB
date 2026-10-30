"""Файловые инструменты: read_file, write_file, str_replace, list_dir, grep."""

import re
from pathlib import Path

from specforge.llm import ToolSpec
from specforge.tools.base import (
    Args,
    Tool,
    ToolEnv,
    ToolError,
    ToolResult,
    arg_int,
    arg_str,
    check_writable,
    glob_match,
    relative,
)

READ_MAX_LINES = 400
LIST_MAX_ENTRIES = 300
GREP_MAX_MATCHES = 100
GREP_MAX_FILE_BYTES = 1_000_000
GREP_LINE_CHARS = 200
SKIP_DIRS = frozenset(
    {".git", ".specforge", "node_modules", "__pycache__", ".venv", "bin", "obj", "build", ".gradle"}
    | {"dist"}
)


def _existing_file(env: ToolEnv, path: str) -> Path:
    target = env.sandbox.resolve(path)
    if not target.exists():
        raise ToolError(f"файл {path} не найден (list_dir покажет содержимое каталога)")
    if not target.is_file():
        raise ToolError(f"{path} — каталог, а не файл")
    return target


def _read(target: Path) -> str:
    data = target.read_bytes()
    if b"\0" in data[:8192]:
        raise ToolError("файл двоичный, прочитать его как текст нельзя")
    return data.decode("utf-8", errors="replace")


# --- read_file -----------------------------------------------------------------------------------


def read_file(env: ToolEnv, args: Args) -> ToolResult:
    target = _existing_file(env, arg_str(args, "path"))
    rel = relative(env, target)
    lines = _read(target).splitlines()
    total = len(lines)
    start = arg_int(args, "start_line") or 1
    end = arg_int(args, "end_line") or total
    if start < 1 or (total and start > total) or end < start:
        raise ToolError(f"неверный диапазон {start}–{end}: в файле {total} строк")
    end = min(end, total, start + READ_MAX_LINES - 1)
    body = "\n".join(f"{n:>5}\t{lines[n - 1]}" for n in range(start, end + 1))
    header = f"{rel} (строки {start}–{end} из {total})" if total else f"{rel} (пустой файл)"
    if end < total:
        header += f"; дальше — read_file с start_line={end + 1}"
    return ToolResult(
        content=f"{header}\n{body}".rstrip(),
        stub=f"[read_file: {rel}, строки {start}–{end} из {total}]",
    )


READ_FILE = Tool(
    ToolSpec(
        name="read_file",
        description=(
            f"Прочитать текстовый файл с номерами строк, не больше {READ_MAX_LINES} строк "
            "за вызов. Номера строк в ответ не входят в содержимое файла."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь относительно /work"},
                "start_line": {"type": "integer", "description": "С какой строки (с 1)"},
                "end_line": {"type": "integer", "description": "По какую строку включительно"},
            },
            "required": ["path"],
        },
    ),
    read_file,
)


# --- write_file ----------------------------------------------------------------------------------


def write_file(env: ToolEnv, args: Args) -> ToolResult:
    path = arg_str(args, "path")
    content = arg_str(args, "content")
    target = env.sandbox.resolve(path)
    rel = relative(env, target)
    if target.exists():
        raise ToolError(
            f"файл {rel} уже существует; write_file создаёт только новые файлы — "
            "для правки используй str_replace"
        )
    check_writable(env, rel)
    env.sandbox.write_text(rel, content)
    lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    return ToolResult(
        content=f"создан {rel} ({lines} строк)",
        stub=f"[write_file: {rel}, {lines} строк]",
        changed=rel,
    )


WRITE_FILE = Tool(
    ToolSpec(
        name="write_file",
        description=(
            "Создать НОВЫЙ файл с содержимым (каталоги создаются автоматически). "
            "Существующие файлы меняй через str_replace."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь относительно /work"},
                "content": {"type": "string", "description": "Полное содержимое файла"},
            },
            "required": ["path", "content"],
        },
    ),
    write_file,
)


# --- str_replace ---------------------------------------------------------------------------------


def str_replace(env: ToolEnv, args: Args) -> ToolResult:
    target = _existing_file(env, arg_str(args, "path"))
    rel = relative(env, target)
    old = arg_str(args, "old")
    new = arg_str(args, "new")
    if not old:
        raise ToolError("old не может быть пустым")
    check_writable(env, rel)
    text = _read(target)
    count = text.count(old)
    if count == 0:
        raise ToolError(
            f"фрагмент old не найден в {rel}; проверь пробелы и отступы (read_file покажет текст)"
        )
    if count > 1:
        raise ToolError(
            f"фрагмент old встречается в {rel} {count} раз(а); добавь окружающие строки, "
            "чтобы совпадение было единственным"
        )
    index = text.index(old)
    updated = text[:index] + new + text[index + len(old) :]
    env.sandbox.write_text(rel, updated)
    first = text.count("\n", 0, index) + 1
    lines = updated.splitlines()
    last = min(len(lines), first + new.count("\n") + 2)
    snippet = "\n".join(f"{n:>5}\t{lines[n - 1]}" for n in range(max(1, first - 2), last + 1))
    return ToolResult(
        content=f"заменено в {rel}, строка {first}:\n{snippet}",
        stub=f"[str_replace: {rel}, строка {first}]",
        changed=rel,
    )


STR_REPLACE = Tool(
    ToolSpec(
        name="str_replace",
        description=(
            "Заменить в файле фрагмент old на new. old должен встречаться в файле ровно один "
            "раз — копируй его из read_file точно, с отступами, но без номеров строк."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Путь относительно /work"},
                "old": {"type": "string", "description": "Заменяемый фрагмент (уникальный)"},
                "new": {"type": "string", "description": "Новый фрагмент"},
            },
            "required": ["path", "old", "new"],
        },
    ),
    str_replace,
)


# --- list_dir ------------------------------------------------------------------------------------


def list_dir(env: ToolEnv, args: Args) -> ToolResult:
    root = env.sandbox.resolve(arg_str(args, "path", "."))
    depth = arg_int(args, "depth", 2) or 1
    if not root.is_dir():
        raise ToolError(f"{arg_str(args, 'path', '.')} — не каталог")
    entries: list[str] = []
    truncated = False

    def walk(directory: Path, level: int) -> None:
        nonlocal truncated
        for child in sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name)):
            if len(entries) >= LIST_MAX_ENTRIES:
                truncated = True
                return
            indent = "  " * (level - 1)
            if child.is_dir():
                skipped = child.name in SKIP_DIRS
                entries.append(f"{indent}{child.name}/{' (пропущен)' if skipped else ''}")
                if level < depth and not skipped and not child.is_symlink():
                    walk(child, level + 1)
            else:
                entries.append(f"{indent}{child.name}")

    walk(root, 1)
    rel = relative(env, root)
    tail = f"\n…[показаны первые {LIST_MAX_ENTRIES} записей]" if truncated else ""
    return ToolResult(
        content=f"{rel}/\n" + "\n".join(entries) + tail if entries else f"{rel}/ (пусто)",
        stub=f"[list_dir: {rel}, {len(entries)} записей]",
    )


LIST_DIR = Tool(
    ToolSpec(
        name="list_dir",
        description="Дерево каталога (каталоги со слешем на конце).",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Каталог относительно /work"},
                "depth": {"type": "integer", "description": "Глубина обхода, по умолчанию 2"},
            },
        },
    ),
    list_dir,
)


# --- grep ----------------------------------------------------------------------------------------


def grep(env: ToolEnv, args: Args) -> ToolResult:
    pattern = arg_str(args, "pattern")
    file_glob = arg_str(args, "glob", "**/*")
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"неверное регулярное выражение: {exc}") from None
    root = env.sandbox.workspace.resolve()
    matches: list[str] = []
    for path in sorted(root.rglob("*")):
        if len(matches) >= GREP_MAX_MATCHES:
            break
        rel_parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts[:-1]) or not path.is_file():
            continue
        if not path.resolve().is_relative_to(root):  # симлинк наружу /work
            continue
        rel = "/".join(rel_parts)
        if not glob_match(rel, file_glob) or path.stat().st_size > GREP_MAX_FILE_BYTES:
            continue
        try:
            text = _read(path)
        except ToolError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                matches.append(f"{rel}:{number}: {line.strip()[:GREP_LINE_CHARS]}")
                if len(matches) >= GREP_MAX_MATCHES:
                    break
    if not matches:
        return ToolResult(content="совпадений нет", stub=f"[grep: {pattern!r}, 0 совпадений]")
    tail = (
        f"\n…[показаны первые {GREP_MAX_MATCHES} совпадений]"
        if len(matches) >= GREP_MAX_MATCHES
        else ""
    )
    return ToolResult(
        content="\n".join(matches) + tail,
        stub=f"[grep: {pattern!r}, {len(matches)} совпадений]",
    )


GREP = Tool(
    ToolSpec(
        name="grep",
        description=f"Поиск по регулярному выражению в файлах /work (до {GREP_MAX_MATCHES} строк).",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Регулярное выражение Python"},
                "glob": {"type": "string", "description": "Фильтр файлов, например src/**/*.ts"},
            },
            "required": ["pattern"],
        },
    ),
    grep,
)
