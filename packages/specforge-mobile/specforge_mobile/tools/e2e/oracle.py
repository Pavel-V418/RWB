"""
Оракул e2e: эталон событий из сценария (geo_scenario) с поправками под реальный стенд и сравнение
с журналом. Чистые функции — тестируются без сети.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta, timezone

from specforge_mobile.tools.route_player import geo_scenario as geo


def fit_minutes(want: int, min_minutes: int | None, step: int | None) -> int:
    """Длительность, которую примет зона: не меньше минимума и кратна шагу."""
    m = max(want, min_minutes or 0)
    if step and step > 0:
        m = -(-m // step) * step
    return m


def build(sc: dict, zones: list[dict], zone_params: dict[str, dict],
          extend_dwell: bool = True) -> tuple[dict, list[dict], list[dict], list[str]]:
    """
    (сценарий с поправками, samples, ожидаемые события, заметки).
    Поправки: длительности под minTime/step зоны старта; если предупреждение не успевает до выезда и
    extend_dwell — удлиняется стоянка, в которой стартует сессия (имеет смысл, только когда таймеры
    сервера ускорены или прогон готов их ждать).
    """
    sc = copy.deepcopy(sc)
    notes = []
    warn = sc.get("timer", {}).get("warnBeforeSec", 600)
    for _ in range(3):
        samples, spans = geo.build_track(sc)
        start = next((a for a in sc.get("actions", []) if a["type"] == "startSession"), None)
        if start:
            t = spans[start["leg"]]["tStart"] + start["offsetSec"]
            zid = geo.zone_at(geo._coord_at(samples, t), zones)
            p = zone_params.get(str(zid), {})
            for a in sc["actions"]:
                key = "paidMinutes" if a["type"] == "startSession" else "addMinutes" if a["type"] == "extendSession" else None
                if key:
                    fixed = fit_minutes(a[key], p.get("minTimeMinutes") if key == "paidMinutes" else None,
                                        p.get("timeStepMinutes"))
                    if fixed != a[key]:
                        notes.append(f"{a['type']}: {a[key]} → {fixed} мин (правила зоны {zid})")
                        a[key] = fixed
        events = geo.derive_events(sc, samples, spans, zones)
        if not start or not extend_dwell or any(e["logical"] == "sessionExpiring" for e in events):
            return sc, samples, events, notes
        # предупреждение не попало в маршрут — удлиняем стоянку со стартом сессии
        paid = sum(a.get("paidMinutes", 0) + a.get("addMinutes", 0) for a in sc["actions"]) * 60
        leg = sc["legs"][start["leg"]]
        need = start["offsetSec"] + paid - warn + 30
        if "dwellSec" in leg and need > leg["dwellSec"]:
            notes.append(f"стоянка этапа {start['leg']}: {leg['dwellSec']} → {need} с, чтобы предупреждение "
                         f"за {warn // 60} мин успело до выезда")
            leg["dwellSec"] = need
        else:
            break
    return sc, samples, events, notes


def restamp(points: list[dict], start: datetime, scale: float, field: str = "recordedAt") -> list[dict]:
    """recordedAt = старт + t/scale: точки не повторяются между прогонами и не бывают «из будущего»."""
    out = []
    for p in points:
        q = dict(p)
        ts = start + timedelta(seconds=p["_tSec"] / scale)
        q[field] = ts.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        out.append(q)
    return out


def parse_ts(s: str) -> datetime:
    """ISO 8601 с любым числом знаков дробной части (.NET пишет 7) и Z/смещением."""
    s = re.sub(r"(\.\d{6})\d+", r"\1", s.replace("Z", "+00:00"))
    return datetime.fromisoformat(s)


def match(expected: list[dict], observed: list[dict], start: datetime, scale: float, zone_uuid: dict[str, str],
          slack: float = 3.0, skip_logical: set[str] | None = None) -> list[dict]:
    """
    Для каждого ожидаемого события — найденное в журнале (по типу и зоне, по порядку, без повторного
    использования). Возвращает [{expected, found, offsetSec, deltaSec, ok, why}].
    observed — события журнала, по возрастанию occurredAt.
    """
    used: set[int] = set()
    out = []
    furthest, group_cursor, group_at = 0, 0, None
    for e in expected:
        # события с одним ожидаемым временем (выезд и закрытие сессии) сервер может записать в любом порядке
        if e.get("atSec") != group_at:
            group_at, group_cursor = e.get("atSec"), furthest
        cursor = group_cursor
        if not e.get("required", True) or e["atSec"] is None:
            continue
        if skip_logical and e["logical"] in skip_logical:
            out.append({"expected": e, "found": None, "ok": None, "why": "пропущено: таймеры сервера не ускорены"})
            continue
        want_zone = zone_uuid.get(str(e["data"].get("zoneId") or e["data"].get("toZoneId") or ""))
        hit = None
        for i in range(cursor, len(observed)):
            o = observed[i]
            if i in used or o.get("type") != e["type"]:
                continue
            if want_zone and o.get("zoneId") and o["zoneId"] != want_zone:
                continue
            hit = i
            break
        if hit is None:
            out.append({"expected": e, "found": None, "ok": False, "why": "события нет в журнале"})
            continue
        used.add(hit)
        furthest = max(furthest, hit + 1)      # порядок: следующую группу ищем только дальше
        o = observed[hit]
        offset = (parse_ts(o["occurredAt"]) - start).total_seconds()
        expect = e["atSec"] / scale
        tol = (e.get("toleranceSec") or 5) / scale + slack
        delta = offset - expect
        ok = abs(delta) <= tol
        out.append({"expected": e, "found": o, "offsetSec": round(offset, 1), "deltaSec": round(delta, 1),
                    "ok": ok, "why": "" if ok else f"время {offset:.1f} с, ожидали {expect:.1f}±{tol:.1f} с"})
    return out
