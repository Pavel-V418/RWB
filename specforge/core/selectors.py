"""Селекторы входов: `AgentSpec.inputs` = `<glob>[#<селектор>]` — в промпт идёт только часть файла.

- JSON / YAML — JSONPath (подмножество): `$.paths./orders`, `$.paths['/orders/{id}']`,
  `$.components.schemas.*`, `$.servers[0]`. Результат — те же ключи с сохранением вложенности
  (`{"paths": {"/orders": …}}`), в формате исходного файла.
- Markdown — заголовок: `docs/spec.md#Требования к API` — раздел с этим заголовком (без учёта
  регистра) и все его подразделы, до следующего заголовка того же или более высокого уровня.

Без селектора файл идёт целиком, как раньше. Селектор, который ничего не выбрал, не роняет агента:
вместо содержимого — пояснение (и предупреждение в лог).
"""

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

SEPARATOR = "#"
JSON_SUFFIXES = (".json",)
YAML_SUFFIXES = (".yaml", ".yml")
MARKDOWN_SUFFIXES = (".md", ".markdown")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_TOKEN = re.compile(r"""\.\*|\.([^.\[\]]+)|\[\*\]|\[(\d+)\]|\['([^']*)'\]|\["([^"]*)"\]""")


class SelectorError(ValueError):
    pass


def split_pattern(pattern: str) -> tuple[str, str | None]:
    """`api/openapi.yaml#$.paths./x` → (`api/openapi.yaml`, `$.paths./x`)."""
    glob, sep, selector = pattern.partition(SEPARATOR)
    return glob, (selector.strip() or None) if sep else None


def input_globs(patterns: Sequence[str]) -> list[str]:
    """Только glob-части шаблонов inputs (для поиска файлов и ключа кэша)."""
    return list(dict.fromkeys(split_pattern(p)[0] for p in patterns))


def apply_selector(path: Path, text: str, selector: str) -> str:
    """Выбранная часть файла; SelectorError — селектор неприменим к файлу или ничего не нашёл."""
    suffix = path.suffix.lower()
    if suffix in JSON_SUFFIXES + YAML_SUFFIXES:
        if not selector.startswith("$"):
            raise SelectorError(f"для {path.name} нужен JSONPath ($...), а не {selector!r}")
        data = json.loads(text) if suffix in JSON_SUFFIXES else yaml.safe_load(text)
        picked = jsonpath_select(data, selector)
        if suffix in JSON_SUFFIXES:
            return json.dumps(picked, ensure_ascii=False, indent=2)
        return yaml.safe_dump(picked, allow_unicode=True, sort_keys=False)
    if suffix in MARKDOWN_SUFFIXES:
        return markdown_section(text, selector)
    raise SelectorError(f"селекторы поддерживаются для json/yaml/md, а не {path.name}")


def parse_jsonpath(expr: str) -> list[str | int | None]:
    """`$.a['b'][0].*` → ["a", "b", 0, None]; None — любой ключ / элемент."""
    if not expr.startswith("$"):
        raise SelectorError(f"JSONPath начинается с $: {expr!r}")
    tokens: list[str | int | None] = []
    position = 1
    while position < len(expr):
        match = _TOKEN.match(expr, position)
        if match is None:
            raise SelectorError(f"не разобрать JSONPath {expr!r} с позиции {position}")
        key, index, single, double = match.groups()
        if index is not None:
            tokens.append(int(index))
        elif key is not None:
            tokens.append(key)
        elif single is not None or double is not None:
            tokens.append(single if single is not None else double)
        else:
            tokens.append(None)
        position = match.end()
    return tokens


def jsonpath_select(data: Any, expr: str) -> Any:
    tokens = parse_jsonpath(expr)
    if not tokens:
        return data
    found, picked = _pick(data, tokens)
    if not found:
        raise SelectorError(f"JSONPath {expr!r} ничего не выбрал")
    return picked


def _pick(node: Any, tokens: list[str | int | None]) -> tuple[bool, Any]:
    """Сокращённая копия node только с выбранными ветками (вложенность сохраняется)."""
    if not tokens:
        return True, node
    head, rest = tokens[0], tokens[1:]
    if isinstance(node, dict):
        keys = list(node) if head is None else [head] if head in node else []
        picked = {}
        for key in keys:
            ok, value = _pick(node[key], rest)
            if ok:
                picked[key] = value
        return bool(picked), picked
    if isinstance(node, list):
        if head is None:
            items = [_pick(item, rest) for item in node]
            selected = [value for ok, value in items if ok]
            return bool(selected), selected
        if isinstance(head, int) and -len(node) <= head < len(node):
            ok, value = _pick(node[head], rest)
            return ok, [value] if ok else []
    return False, None


def markdown_section(text: str, heading: str) -> str:
    """Разделы с заголовком `heading` (без учёта регистра) вместе с подразделами."""
    wanted = heading.strip().lstrip("#").strip().casefold()
    lines = text.splitlines()
    sections: list[str] = []
    in_code = False
    level: int | None = None
    current: list[str] = []
    for line in lines:
        if _FENCE.match(line):
            in_code = not in_code
        match = None if in_code else _HEADING.match(line)
        if match:
            depth = len(match.group(1))
            if level is not None and depth <= level:
                sections.append("\n".join(current).rstrip())
                level, current = None, []
            if level is None and match.group(2).strip().casefold() == wanted:
                level = depth
        if level is not None:
            current.append(line)
    if level is not None:
        sections.append("\n".join(current).rstrip())
    if not sections:
        raise SelectorError(f"нет раздела с заголовком {heading!r}")
    return "\n\n".join(sections) + "\n"
