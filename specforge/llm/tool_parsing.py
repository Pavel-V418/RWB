"""Разбор текстового вывода модели: блоки размышлений, JSON в ограждениях ```."""

import re

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def strip_think(text: str) -> str:
    """Убрать `<think>…</think>` (Qwen3 без reasoning-парсера в vLLM) и незакрытый хвост."""
    text = _THINK_RE.sub("", text)
    if "</think>" in text:  # открывающий тег съеден шаблоном чата
        text = text.split("</think>", 1)[1]
    return text.strip()


def extract_json(text: str) -> str:
    """Достать JSON-документ из ответа модели. ValueError, если его там нет."""
    text = strip_think(text)
    fence = _FENCE_RE.search(text)
    if fence:
        return fence.group(1).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("в ответе модели нет JSON")
    start = min(starts)
    end = text.rfind("}" if text[start] == "{" else "]")
    if end < start:
        raise ValueError("JSON в ответе модели не закрыт")
    return text[start : end + 1]
