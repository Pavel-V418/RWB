"""Примеры значений и тел запросов/ответов для документации методов API (детерминированно, без LLM)."""

from __future__ import annotations

import json
import re

from ..schemas_system import DField

EXAMPLE_UUID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
EXAMPLE_LON, EXAMPLE_LAT = 37.6173, 55.7558


def example_value(f: DField):
    if f.example is not None:
        v = f.example
        if f.type in ("int", "bigint"):
            try:
                return int(float(v))
            except ValueError:
                return v
        if f.type == "decimal":
            try:
                return float(v)
            except ValueError:
                return v
        if f.type == "bool":
            return v.lower() == "true"
        return v
    t = f.type
    if t == "uuid":
        return EXAMPLE_UUID
    if t == "enum":
        return f.default if f.default in f.enum_values else (f.enum_values[0] if f.enum_values else "value")
    if t in ("int", "bigint"):
        return int(f.minimum) if f.minimum is not None else 1
    if t == "decimal":
        return float(f.minimum) + 10.5 if f.minimum is not None else 10.5
    if t == "bool":
        return (f.default or "true").lower() == "true"
    if t == "date":
        return "2026-10-30"
    if t == "datetime":
        return "2026-10-30T14:20:00+03:00"
    if t == "json":
        return {}
    if t == "point":
        return {"type": "Point", "coordinates": [EXAMPLE_LON, EXAMPLE_LAT]}
    if t == "polygon":
        d = 0.002
        ring = [[EXAMPLE_LON - d, EXAMPLE_LAT - d], [EXAMPLE_LON + d, EXAMPLE_LAT - d],
                [EXAMPLE_LON + d, EXAMPLE_LAT + d], [EXAMPLE_LON - d, EXAMPLE_LAT + d], [EXAMPLE_LON - d, EXAMPLE_LAT - d]]
        return {"type": "Polygon", "coordinates": [ring]}
    if t == "linestring":
        return {"type": "LineString", "coordinates": [[EXAMPLE_LON, EXAMPLE_LAT], [EXAMPLE_LON + 0.003, EXAMPLE_LAT + 0.001]]}
    if f.default:
        return f.default
    return "строка"


def example_object(fields: list[DField], *, writable: bool = False) -> dict:
    """Пример тела. Запрос: необязательные поля, описание которых привязано к другому значению
    enum из этого же примера («для режима confirmed»), опускаются. Ответ: необязательные поля без
    явного примера — null; даты-время идут по возрастанию в порядке полей (начало раньше окончания)."""
    fields = [f for f in fields if not f.internal and not (writable and f.read_only)]
    out: dict = {}
    hour = 0
    for f in fields:
        if not writable and not f.required and f.example is None:
            out[f.name] = None
            continue
        v = example_value(f)
        if f.type == "datetime" and f.example is None:
            v = f"2026-10-30T{14 + hour:02d}:20:00+03:00"
            hour += 1
        out[f.name] = v
    if writable:
        chosen = {str(v) for f in fields if f.type == "enum" for v in [out.get(f.name)]}
        others = {e for f in fields if f.type == "enum" for e in f.enum_values} - chosen
        for f in fields:
            if f.required or not f.description:
                continue
            words = set(re.findall(r"[A-Za-z0-9_]+", f.description))
            if words & others and not words & chosen:
                out.pop(f.name, None)
    return out


def to_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def example_matches_pattern(f: DField) -> bool | None:
    if not f.pattern or f.example is None:
        return None
    try:
        return re.fullmatch(f.pattern.lstrip("^").rstrip("$"), f.example) is not None
    except re.error:
        return None
