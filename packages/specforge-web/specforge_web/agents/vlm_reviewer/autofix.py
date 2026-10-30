"""Дефекты UI-ревью → исправления без участия человека.

1. Правки спеки экранов (детерминированно, 0 токенов) — генератор пересоберёт страницы:
   - горизонтальная прокрутка на мобильном у списка → hideBelow для второстепенных колонок;
   - то же у таблиц в карточке объекта.
2. Замечания к слотам (VLM-дефекты экранов со слотами) → обратная связь frontend-агенту.
Дефекты, которые не исправляются ни так, ни так, остаются в отчёте (обычно это ошибки генератора).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path


def patch_spec(spec: dict, defects: list[dict]) -> tuple[dict, list[str]]:
    spec = copy.deepcopy(spec)
    by_id = {s["id"]: s for s in spec["screens"]}
    applied: list[str] = []
    overflow = {d["screen"] for d in defects if "прокрутка" in d.get("issue", "") and d.get("viewport") == "mobile"}
    for sid in overflow:
        s = by_id.get(sid)
        if not s:
            continue
        tables = []
        if s.get("list"):
            tables.append(s["list"]["columns"])
        for sec in (s.get("detail") or {}).get("sections", []):
            if sec.get("columns"):
                tables.append(sec["columns"])
        for cols in tables:
            changed = 0
            for i, c in enumerate(cols):
                if i >= 2 and not c.get("hideBelow") and c.get("format") not in ("link", "status"):
                    c["hideBelow"] = "md" if i < 4 else "lg"
                    changed += 1
            if changed:
                applied.append(f"{sid}: скрыты {changed} колонки на узких экранах")
    return spec, applied


def slot_feedback(defects: list[dict], slots: list[dict]) -> dict[str, list[str]]:
    """screen → замечания VLM для слотов этого экрана."""
    screens_with_slots = {s["screen"] for s in slots}
    fb: dict[str, list[str]] = {}
    for d in defects:
        if d.get("source") == "vlm" and d["screen"] in screens_with_slots and d["severity"] in ("critical", "major"):
            fb.setdefault(d["screen"], []).append(f"[{d['viewport']}/{d['state']}] {d['element']}: {d['issue']} → {d['fix']}")
    return fb


def apply(input_dir: Path, review_dir: Path) -> dict:
    spec_path = input_dir / "03_ux" / "screen-spec.web.json"
    defects = json.loads((review_dir / "defects.json").read_text(encoding="utf-8"))
    spec, applied = patch_spec(json.loads(spec_path.read_text(encoding="utf-8")), defects)
    if applied:
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"specPatches": applied}
