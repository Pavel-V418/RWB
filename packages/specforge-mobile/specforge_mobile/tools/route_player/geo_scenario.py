#!/usr/bin/env python3
"""
Генератор mock-маршрута и эталона событий для e2e.

Универсальный: ничего не знает о домене кейса. Всё доменное (имена событий,
зоны, маршрут, действия пользователя) приходит из scenario.json.

Вход:  scenario.json
Выход: route.geojson        — маршрут, точки и зоны (для карты и отладки)
       points.json          — точки для POST .../positions (формат PositionPoint)
       expected_events.json — оракул e2e: какие события, в каком порядке и когда

Только стандартная библиотека Python 3.10+.

Пример:
    python geo_scenario.py ../../reference/case1/mock_route/scenario.json \
        --out ../../reference/case1/mock_route
"""
from __future__ import annotations

import argparse
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

EARTH_R = 6_371_000.0  # м


# ---------------------------------------------------------------- геометрия
def haversine_m(a: list[float], b: list[float]) -> float:
    """Расстояние между точками [lon, lat] в метрах."""
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(h))


def bearing_deg(a: list[float], b: list[float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def lerp(a: list[float], b: list[float], t: float) -> list[float]:
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]


def point_in_polygon(p: list[float], ring: list[list[float]]) -> bool:
    """Ray casting. ring — замкнутый или незамкнутый контур [[lon, lat], ...]."""
    x, y = p
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                inside = not inside
    return inside


def dist_to_line_m(p: list[float], line: list[list[float]]) -> float:
    """Расстояние от точки до ломаной [[lon, lat], ...] в метрах (локальная плоская проекция)."""
    kx = 111_320 * math.cos(math.radians(p[1]))
    ky = 111_320
    px, py = p[0] * kx, p[1] * ky
    best = math.inf
    for (x1, y1), (x2, y2) in zip(line, line[1:]):
        ax, ay, bx, by = x1 * kx, y1 * ky, x2 * kx, y2 * ky
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
        best = min(best, math.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    return best


def zone_at(p: list[float], zones: list[dict]) -> str | None:
    """Зона точки: внутри полигона или не дальше radiusM от линии (ближайшая из подходящих)."""
    best_id, best_d = None, math.inf
    for z in zones:
        if "polygon" in z:
            if point_in_polygon(p, z["polygon"]):
                return z["id"]
        else:  # "line" — одна ломаная, "lines" — несколько (MultiLineString)
            d = min(dist_to_line_m(p, ln) for ln in z.get("lines") or [z["line"]])
            if d <= z["radiusM"] and d < best_d:
                best_id, best_d = z["id"], d
    return best_id


def load_zones(sc: dict, base_dir: Path) -> list[dict]:
    """
    Зоны из сценария: либо список "zones" (polygon или line+radiusM),
    либо "zonesFrom" — GeoJSON сида с указанием, какие свойства читать.
    """
    zones = list(sc.get("zones", []))
    src = sc.get("zonesFrom")
    if src:
        gj = json.loads((base_dir / src["file"]).read_text(encoding="utf-8"))
        for f in gj["features"]:
            props = f.get("properties") or {}
            zid = props.get(src.get("idProp", "id"), f.get("id"))
            if zid is None:
                continue  # служебные точки сида (например, точки стоянки)
            g = f["geometry"]
            z = {"id": str(zid), "name": props.get(src.get("nameProp", "name"), str(zid))}
            if g["type"] == "Polygon":
                z["polygon"] = g["coordinates"][0]
            elif g["type"] in ("LineString", "MultiLineString"):
                lines = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
                z["line"], z["lines"] = lines[0], lines
                z["radiusM"] = float(props.get(src.get("radiusProp", "radiusM"), src.get("defaultRadiusM", 20)))
            else:
                raise ValueError(f"Зона {zid}: геометрия {g['type']} не поддерживается")
            zones.append(z)
    if not zones:
        raise ValueError("В сценарии нет зон: задай zones или zonesFrom")
    return zones


# ---------------------------------------------------------------- траектория
def build_track(sc: dict) -> tuple[list[dict], list[dict]]:
    """
    Возвращает (samples, leg_spans).
    samples:   [{t, coord, speedKmh, heading}] с шагом sampleSec
    leg_spans: [{index, kind, tStart, tEnd}] — для привязки действий к этапам
    """
    step = float(sc.get("sampleSec", 1))
    pos = list(sc["start"])
    heading = 0.0
    t = 0.0
    samples = [{"t": 0.0, "coord": pos[:], "speedKmh": 0.0, "heading": heading}]
    spans = []

    for i, leg in enumerate(sc["legs"]):
        t0 = t
        if "dwellSec" in leg:
            n = int(round(leg["dwellSec"] / step))
            for _ in range(n):
                t += step
                samples.append({"t": t, "coord": pos[:], "speedKmh": 0.0, "heading": heading})
            spans.append({"index": i, "kind": "dwell", "tStart": t0, "tEnd": t})
            continue

        # "via" — промежуточные точки этапа (повороты по улицам), "to" — конечная
        waypoints = list(leg.get("via", [])) + [leg["to"]]
        speed_ms = leg["speedKmh"] / 3.6
        for wi, target in enumerate(waypoints):
            last = wi == len(waypoints) - 1
            dist = haversine_m(pos, target)
            dur = dist / speed_ms if speed_ms > 0 else 0
            n = max(1, int(math.ceil(dur / step)))
            heading = bearing_deg(pos, target)
            start = pos[:]
            for k in range(1, n + 1):
                t += step
                frac = min(1.0, (k * step) / dur) if dur else 1.0
                stopped = last and frac >= 1
                samples.append({"t": t, "coord": lerp(start, target, frac),
                                "speedKmh": 0.0 if stopped else leg["speedKmh"], "heading": heading})
            pos = target[:]
        spans.append({"index": i, "kind": "drive", "tStart": t0, "tEnd": t})
    return samples, spans


def apply_jitter(samples: list[dict], meters: float, seed: int, zones: list[dict]) -> None:
    """Шум GPS, который не меняет принадлежность точки зоне (иначе «дребезг» на границе)."""
    if meters <= 0:
        return
    rnd = random.Random(seed)
    for s in samples:
        base = s["coord"]
        z0 = zone_at(base, zones)
        for _ in range(5):
            dlat = rnd.uniform(-meters, meters) / 111_320
            dlon = rnd.uniform(-meters, meters) / (111_320 * math.cos(math.radians(base[1])))
            cand = [base[0] + dlon, base[1] + dlat]
            if zone_at(cand, zones) == z0:
                s["coord"] = cand
                break


# ---------------------------------------------------------------- события
def derive_events(sc: dict, samples: list[dict], spans: list[dict], zones: list[dict]) -> list[dict]:
    ev_names = sc["eventTypes"]          # логическое имя -> реальный type события кейса
    tol = sc.get("toleranceSec", {})
    geo_tol = tol.get("geo", 5)
    timer_tol = tol.get("timer", 30)
    action_tol = tol.get("action", 5)

    events: list[dict] = []

    def add(kind: str, t: float, tolerance: float, required: bool = True, **data):
        events.append({
            "logical": kind,
            "type": ev_names[kind],
            "atSec": round(t, 1),
            "toleranceSec": tolerance,
            "required": required,
            "data": data,
        })

    # 1) гео-события из траектории. Правила backend — в sc["zoneRules"] (по умолчанию — простые):
    #   confirmPoints       — состояние меняется после стольких подряд согласных точек (гистерезис);
    #   changeAfterExitSec  — въезд в зону B не позже чем через столько секунд после выезда из A ≠ B
    #                         даёт zoneChanged вместо zoneEntered (прямой переход A→B — всегда zoneChanged);
    #   exitBeforeChange    — true: выезд из A всё равно даёт zoneExited (затем zoneChanged);
    #                         false: выезд в окне «поглощается» сменой зоны;
    #   closeSessionOnExit  — причина закрытия активной сессии при выезде (null — не закрывать).
    rules = sc.get("zoneRules", {})
    confirm = max(1, int(rules.get("confirmPoints", 1)))
    window = float(rules.get("changeAfterExitSec", sc.get("zoneChangeWindowSec", 0)))
    exit_first = bool(rules.get("exitBeforeChange", False))
    state, cand, streak = None, None, 0
    last_exit = None      # (t, zoneId) — последний подтверждённый выезд
    pending_exit = None   # выезд, который ещё может «поглотиться» сменой зоны (exit_first=False)
    exits: list[float] = []
    for s in samples:
        cur = zone_at(s["coord"], zones)
        t = s["t"]
        if pending_exit and t - pending_exit[0] > window:
            add("zoneExited", pending_exit[0], geo_tol, zoneId=pending_exit[1])
            pending_exit = None
        if cur == state:
            cand, streak = None, 0
            continue
        cand, streak = (cand, streak + 1) if cur == cand else (cur, 1)
        if streak < confirm:
            continue
        if state is not None and cur is not None:
            add("zoneChanged", t, geo_tol, fromZoneId=state, toZoneId=cur, requiresPaymentCheck=True)
        elif state is not None:                                   # выезд наружу (закрывает сессию)
            exits.append(t)
            last_exit = (t, state)
            if exit_first or window <= 0:
                add("zoneExited", t, geo_tol, zoneId=state)
            else:
                pending_exit = (t, state)
        else:                                                     # въезд снаружи
            recent = last_exit if last_exit and t - last_exit[0] <= window else None
            if recent and recent[1] != cur:
                add("zoneChanged", t, geo_tol, fromZoneId=recent[1], toZoneId=cur, requiresPaymentCheck=True)
            elif not (pending_exit and pending_exit[1] == cur):   # вернулся в ту же зону — выезда не было
                add("zoneEntered", t, geo_tol, zoneId=cur)
            pending_exit = None
        state, cand, streak = cur, None, 0
    if pending_exit:
        add("zoneExited", pending_exit[0], geo_tol, zoneId=pending_exit[1])

    # 2) действия пользователя и таймеры
    warn = sc.get("timer", {}).get("warnBeforeSec", 600)
    paid_until = None
    closed_at = None
    for a in sorted(sc.get("actions", []), key=lambda x: (x["leg"], x["offsetSec"])):
        span = spans[a["leg"]]
        t = span["tStart"] + a["offsetSec"]
        if t > span["tEnd"]:
            raise ValueError(f"Действие {a['type']} выходит за пределы этапа {a['leg']}")
        a["_t"] = t
        zone = zone_at(_coord_at(samples, t), zones)
        if a["type"] == "startSession":
            if zone is None:
                raise ValueError("startSession вне зоны — проверь сценарий")
            paid_until = t + a["paidMinutes"] * 60
            add("sessionStarted", t, action_tol, zoneId=zone, paidUntilSec=paid_until)
        elif a["type"] == "extendSession":
            if paid_until is None:
                raise ValueError("extendSession до startSession")
            paid_until += a["addMinutes"] * 60
            add("sessionExtended", t, action_tol, paidUntilSec=paid_until, addMinutes=a["addMinutes"])
        elif a["type"] == "closeSession":
            closed_at = t
            add("sessionClosed", t, action_tol, reason="user")

    close_reason = rules.get("closeSessionOnExit")
    if paid_until is not None and close_reason:
        started = min(a["_t"] for a in sc.get("actions", []) if a["type"] == "startSession")
        t_exit = next((t for t in exits if t > started), None)
        if t_exit is not None and (closed_at is None or t_exit < closed_at) and t_exit < paid_until:
            closed_at = t_exit
            add("sessionClosed", t_exit, geo_tol, reason=close_reason)

    if paid_until is not None:
        t_warn = paid_until - warn
        if closed_at is None or t_warn < closed_at:
            add("sessionExpiring", t_warn, timer_tol, paidUntilSec=paid_until, minutesLeft=warn // 60)
        end_of_track = samples[-1]["t"]
        if (closed_at is None or paid_until < closed_at) and paid_until <= end_of_track:
            add("sessionExpired", paid_until, timer_tol, required=False, paidUntilSec=paid_until)

    # необязательные события, которые зависят от правил backend (не проверяем строго)
    for opt in sc.get("optionalEvents", []):
        if not any(e["logical"] == opt for e in events):
            events.append({"logical": opt, "type": ev_names[opt], "atSec": None,
                           "toleranceSec": None, "required": False,
                           "data": {"note": "зависит от бизнес-правил backend, порядок не проверяется"}})

    events.sort(key=lambda e: (e["atSec"] is None, e["atSec"] or 0))
    return events


def _coord_at(samples: list[dict], t: float) -> list[float]:
    best = min(samples, key=lambda s: abs(s["t"] - t))
    return best["coord"]


# ---------------------------------------------------------------- выход
def iso(base: datetime, sec: float) -> str:
    return (base + timedelta(seconds=sec)).isoformat().replace("+00:00", "Z")


def to_points(sc: dict, samples: list[dict], base: datetime) -> list[dict]:
    fm = sc.get("pointFieldMap", {})
    f = lambda k: fm.get(k, k)
    out = []
    for s in samples:
        out.append({
            f("lat"): round(s["coord"][1], 7),
            f("lon"): round(s["coord"][0], 7),
            f("recordedAt"): iso(base, s["t"]),
            f("speedKmh"): round(s["speedKmh"], 1),
            f("heading"): round(s["heading"], 1),
            f("accuracyM"): sc.get("accuracyM", 5),
            "_tSec": s["t"],  # служебное поле плеера, в API не отправляется
        })
    return out


def to_geojson(sc: dict, samples: list[dict], events: list[dict], zones: list[dict]) -> dict:
    feats = [{
        "type": "Feature",
        "properties": {"kind": "route", "vehicleId": sc["vehicleId"],
                       "timestamps": [s["t"] for s in samples]},
        "geometry": {"type": "LineString", "coordinates": [s["coord"] for s in samples]},
    }]
    for z in zones:
        props = {"kind": "zone", "id": z["id"], "name": z["name"]}
        if "polygon" in z:
            ring = z["polygon"] + ([z["polygon"][0]] if z["polygon"][0] != z["polygon"][-1] else [])
            geom = {"type": "Polygon", "coordinates": [ring]}
        else:
            props["radiusM"] = z["radiusM"]
            geom = {"type": "LineString", "coordinates": z["line"]}
        feats.append({"type": "Feature", "properties": props, "geometry": geom})
    for e in events:
        if e["atSec"] is None:
            continue
        feats.append({"type": "Feature",
                      "properties": {"kind": "expectedEvent", "type": e["type"], "atSec": e["atSec"]},
                      "geometry": {"type": "Point", "coordinates": _coord_at(samples, e["atSec"])}})
    return {"type": "FeatureCollection", "features": feats}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario", type=Path)
    ap.add_argument("--out", type=Path, default=None, help="папка вывода (по умолчанию рядом со сценарием)")
    args = ap.parse_args()

    sc = json.loads(args.scenario.read_text(encoding="utf-8"))
    out = args.out or args.scenario.parent
    out.mkdir(parents=True, exist_ok=True)

    zones = load_zones(sc, args.scenario.parent)
    samples, spans = build_track(sc)
    events = derive_events(sc, samples, spans, zones)   # события считаем по «чистой» траектории
    apply_jitter(samples, sc.get("jitterM", 0), sc.get("seed", 42), zones)
    base = datetime.fromisoformat(sc.get("baseTime", "2026-01-01T09:00:00+00:00")).astimezone(timezone.utc)

    expected = {
        "scenario": sc.get("name"),
        "vehicleId": sc["vehicleId"],
        "durationSec": samples[-1]["t"],
        "sampleSec": sc.get("sampleSec", 1),
        "orderCheck": [e["type"] for e in events if e["required"]],
        "events": events,
        "note": "atSec — секунды сценария от первой точки. Реальное время = atSec / TIME_SCALE.",
    }
    (out / "points.json").write_text(json.dumps(to_points(sc, samples, base), ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "route.geojson").write_text(json.dumps(to_geojson(sc, samples, events, zones), ensure_ascii=False), encoding="utf-8")
    (out / "expected_events.json").write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"точек: {len(samples)}, длительность: {samples[-1]['t']:.0f} с ({samples[-1]['t']/60:.1f} мин)")
    for e in events:
        at = "—" if e["atSec"] is None else f"{e['atSec']:7.1f} с"
        print(f"  {at}  {'*' if e['required'] else ' '} {e['type']}  {e['data']}")


if __name__ == "__main__":
    main()
