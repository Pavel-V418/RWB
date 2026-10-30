"""Загрузка промптов. Общий префикс (system + исходный текст) одинаков у всех вызовов этапа,
поэтому vLLM с prefix caching считает его один раз."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

PROMPTS_DIR = Path(__file__).parent / "prompts"


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(loader=FileSystemLoader(PROMPTS_DIR), undefined=StrictUndefined,
                       keep_trailing_newline=True, trim_blocks=True, lstrip_blocks=True)


def render(name: str, **ctx) -> str:
    return _env().get_template(f"{name}.j2").render(**ctx)


def messages(task: str, system: str = "system", **ctx) -> list[dict]:
    return [
        {"role": "system", "content": render(system)},
        {"role": "user", "content": render(task, **ctx)},
    ]


def compact(obj) -> str:
    """Компактный JSON без пустых полей — меньше токенов."""

    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if v not in (None, [], {}, "")}
        if isinstance(o, list):
            return [strip(v) for v in o]
        return o

    return json.dumps(strip(obj), ensure_ascii=False, separators=(",", ":"))
