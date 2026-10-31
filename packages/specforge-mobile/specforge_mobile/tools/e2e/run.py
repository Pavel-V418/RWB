"""
e2e демо-сценарий на стенде: машина едет по mock-маршруту, водитель начинает и продлевает сессию,
сверяем журнал событий (admin), уведомления водителя, права на журнал и аудит — с эталоном.

    python -m specforge_mobile.tools.e2e.run --base http://localhost:8080 --out artifacts/07_tests ^
        --requirements artifacts/01_requirements/requirements.json

В CI стадия e2e запускает: python3 <файл> --base $STAND_URL (участник 3).
Пароли — только из окружения: CONTRACT_PASSWORD_DRIVER, CONTRACT_PASSWORD_ADMIN (те же, что у контракта).
Выходы: e2e-junit.xml (теги FR/SEC в имени), e2e.json, raw/e2e.sarif (нарушения прав и аудита → security).
Код выхода 1 — есть проваленные шаги.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # корень пакета: reference/ и путь к specforge_mobile
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from specforge_mobile.tools.contract.checks import Client                     # noqa: E402
from specforge_mobile.tools.e2e import oracle                                  # noqa: E402
from specforge_mobile.tools.route_player import geo_scenario as geo            # noqa: E402
from specforge_mobile.tools.route_player.play_route import play                # noqa: E402

DEFAULT_SCENARIO = ROOT / "reference/case1/mock_route/scenario.json"
API = "/api/v1"


class Step:
    def __init__(self, logical: str, title: str, tags: list[str]):
        self.logical, self.title, self.tags = logical, title, tags
        self.status, self.message, self.t0 = "passed", "", time.monotonic()
        self.duration = 0.0

    def done(self, ok: bool | None, message: str = "") -> "Step":
        self.status = "skipped" if ok is None else ("passed" if ok else "failed")
        self.message, self.duration = message, round(time.monotonic() - self.t0, 2)
        return self


def login(c: Client, user: str, env: str) -> str:
    pw = os.environ.get(env)
    if not pw:
        raise SystemExit(f"Нет пароля в окружении: {env}")
    r = c.send("post", f"{API}/auth/tokens", headers={"Content-Type": "application/json"},
               body=json.dumps({"login": user, "password": pw}).encode())
    if r.status != 200:
        raise SystemExit(f"Вход {user}: HTTP {r.status}")
    return r.json()["accessToken"]


def server_skew(c: Client) -> float:
    """Сдвиг часов сервера относительно наших (по заголовку Date), чтобы сравнивать время событий."""
    r = c.send("get", "/swagger/v1/swagger.json")
    d = r.headers.get("date")
    return (parsedate_to_datetime(d) - datetime.now(timezone.utc)).total_seconds() if d else 0.0


def idem() -> dict:
    return {"Idempotency-Key": uuid.uuid4().hex, "Content-Type": "application/json"}


def get_json(c: Client, path: str, query: dict, token: str, accept=(200,)):
    """GET с проверкой статуса: ошибка сервера — это ошибка, а не «пустой список»."""
    r = c.send("get", path, query, token=token)
    if r.status not in accept:
        raise RuntimeError(f"GET {path} {query}: HTTP {r.status} {r.body[:200]!r}")
    return r.json()


def list_all(c: Client, path: str, query: dict, token: str, page_size: int = 100) -> list[dict]:
    items, page = [], 1
    while True:
        data = get_json(c, path, {**query, "page": page, "pageSize": page_size}, token) or {}
        chunk = data.get("items", [])
        items += chunk
        if len(chunk) < page_size or page >= 50:
            return items
        page += 1


def stand_zones(c: Client, token: str, coords: list[list[float]], margin: float = 0.003) -> tuple[list[dict], dict, dict]:
    """
    Зоны стенда вокруг маршрута (GET /zones?bbox, радиусы из карточек): эталон строится по реальным
    данным системы, а не только по демо-зонам сценария. → (zones, uuid по externalZoneId, параметры).
    """
    lons, lats = [p[0] for p in coords], [p[1] for p in coords]
    bbox = f"{min(lons) - margin:.6f},{min(lats) - margin:.6f},{max(lons) + margin:.6f},{max(lats) + margin:.6f}"
    fc = get_json(c, f"{API}/zones", {"bbox": bbox, "limit": 2000}, token) or {}
    zones, uuids, params = [], {}, {}
    probe = coords[::5] + coords[-1:]
    for f in fc.get("features", []):
        g, p = f.get("geometry") or {}, f.get("properties") or {}
        if g.get("type") not in ("LineString", "MultiLineString"):
            continue
        ext = str(p.get("externalZoneId") or f.get("id"))
        lines = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
        # карточку (радиус) грузим только для зон в 150 м от маршрута — в центре города их тысячи
        if min(geo.dist_to_line_m(pt, ln) for pt in probe for ln in lines) > 150:
            continue
        details = get_json(c, f"{API}/zones/{f['id']}", {}, token) or {}
        zones.append({"id": ext, "name": p.get("displayName", ext), "line": lines[0], "lines": lines,
                      "radiusM": float(details.get("detectionRadiusM") or 20)})
        uuids[ext], params[ext] = f["id"], {**p, **details}
    return zones, uuids, params


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True)
    ap.add_argument("--scenario", type=Path, default=Path(os.environ.get("E2E_SCENARIO", DEFAULT_SCENARIO)))
    ap.add_argument("--scale", type=float, default=float(os.environ.get("TIME_SCALE", 10)),
                    help="ускорение времени, как TIME_SCALE backend")
    ap.add_argument("--out", type=Path, default=Path(os.environ.get("E2E_OUT", "artifacts/07_tests")))
    ap.add_argument("--driver", default=os.environ.get("E2E_DRIVER", "driver.demo"))
    ap.add_argument("--admin", default=os.environ.get("E2E_ADMIN", "admin"))
    ap.add_argument("--requirements", type=Path)
    ap.add_argument("--timers", action="store_true", help="ждать таймеры сервера, даже если они не ускорены")
    ap.add_argument("--max-wait", type=float, default=320, help="макс. ожидание «остывания» после прошлого прогона")
    args = ap.parse_args(argv)

    sc = json.loads(args.scenario.read_text(encoding="utf-8"))
    zones = geo.load_zones(sc, args.scenario.parent)
    tags = sc.get("requirementTags", {})
    secs = {}
    if args.requirements and args.requirements.is_file():
        for r in json.loads(args.requirements.read_text(encoding="utf-8")).get("security", []):
            secs.setdefault(r.get("category"), []).append(r["id"])
    vid = sc["vehicleId"]
    c = Client(args.base, timeout=20)
    steps: list[Step] = []
    log = lambda m: print(m, flush=True)  # noqa: E731

    td, ta = login(c, args.driver, "CONTRACT_PASSWORD_DRIVER"), login(c, args.admin, "CONTRACT_PASSWORD_ADMIN")
    skew = server_skew(c)

    # --- зоны стенда вокруг маршрута: геометрии, радиусы, uuid, минимальное время и шаг оплаты
    route0, _ = geo.build_track(sc)
    zones, zone_uuid, zone_params = stand_zones(c, td, [s["coord"] for s in route0])
    story = [z["id"] for z in geo.load_zones(sc, args.scenario.parent)]
    missing = [z for z in story if z not in zone_uuid]
    if missing:
        raise SystemExit(f"Зоны сценария не найдены на стенде: {missing}")
    log(f"зон стенда вокруг маршрута: {len(zones)}; сценарные: "
        + ", ".join(f"{z} (мин. оплата {zone_params[z].get('minTimeMinutes')} мин)" for z in story))

    sc_run, samples, expected, notes = oracle.build(sc, zones, zone_params, extend_dwell=args.timers)
    for n in notes:
        log(f"  поправка: {n}")
    geo.apply_jitter(samples, sc_run.get("jitterM", 0), sc_run.get("seed", 42), zones)
    base_points = geo.to_points(sc_run, samples, datetime(2026, 1, 1, tzinfo=timezone.utc))

    # --- исходное состояние машины: без активной сессии, «остывшая» после прошлого выезда
    st = get_json(c, f"{API}/vehicles/{vid}", {}, td) or {}
    if st.get("activeSession"):
        sid = st["activeSession"]["id"]
        r = c.send("post", f"{API}/sessions/{sid}/closure", headers=idem(), token=td)
        log(f"подготовка: закрыта активная сессия прошлого прогона (HTTP {r.status})")
    window = sc_run.get("zoneRules", {}).get("changeAfterExitSec", 0)
    confirm = sc_run.get("zoneRules", {}).get("confirmPoints", 1)
    if st.get("currentZone"):
        # выводим машину из зоны точками в начале маршрута (там зон нет), иначе маршрут начнётся с выезда
        p0 = samples[0]["coord"]
        if geo.zone_at(p0, zones) is not None:
            log("ВНИМАНИЕ: начало маршрута внутри зоны стенда — первый шаг сценария будет не «въезд»")
        now = datetime.now(timezone.utc) + timedelta(seconds=skew)
        pts = [{"lat": p0[1], "lon": p0[0], "accuracyM": 5,
                "recordedAt": (now + timedelta(seconds=i)).isoformat().replace("+00:00", "Z")} for i in range(confirm + 1)]
        r = c.send("post", f"{API}/vehicles/{vid}/positions", headers={"Content-Type": "application/json"},
                   body=json.dumps({**sc_run.get("requestBody", {}), "points": pts}).encode(), token=td)
        log(f"подготовка: машина числилась в зоне — выведена наружу (HTTP {r.status})")
        time.sleep(confirm + 2)
    if window:
        evs = (get_json(c, f"{API}/events", {"vehicleId": vid, "pageSize": 20}, ta) or {}).get("items", [])
        last_exit = next((e for e in evs if e["type"] == sc["eventTypes"]["zoneExited"]), None)
        if last_exit:
            age = (datetime.now(timezone.utc) + timedelta(seconds=skew) - oracle.parse_ts(last_exit["occurredAt"])).total_seconds()
            wait = window - age + 5
            if wait > 0:
                wait = min(wait, args.max_wait)
                log(f"подготовка: прошлый выезд {age:.0f} с назад — ждём {wait:.0f} с, чтобы въезд не стал «сменой зоны»")
                time.sleep(wait)

    # --- проигрываем маршрут и выполняем действия водителя
    start = datetime.now(timezone.utc) + timedelta(seconds=skew)       # во времени сервера
    points = oracle.restamp(base_points, start, args.scale)
    actions = sorted(sc_run.get("actions", []), key=lambda a: a["_t"])
    state = {"session": None, "accel": None, "results": [], "sentAt": {}}

    def on_tick(t: float, _n: int):
        while actions and actions[0]["_t"] <= t:
            a = actions.pop(0)
            # время события сервера сравниваем с фактическим моментом нашего запроса, а не с планом
            state["sentAt"][{"startSession": "sessionStarted", "extendSession": "sessionExtended"}.get(a["type"], a["type"])] = \
                (datetime.now(timezone.utc) + timedelta(seconds=skew) - start).total_seconds()
            if a["type"] == "startSession":
                zid = geo.zone_at(geo._coord_at(samples, a["_t"]), zones)
                body = {"vehicleId": vid, "zoneId": zone_uuid[zid], "durationMinutes": a["paidMinutes"]}
                r = c.send("post", f"{API}/sessions", headers=idem(), body=json.dumps(body).encode(), token=td)
                data = r.json() or {}
                state["results"].append(("sessionStarted", r.status, data))
                if r.status in (200, 201):
                    state["session"] = data
                    real = (oracle.parse_ts(data["paidUntil"]) - oracle.parse_ts(data["startedAt"])).total_seconds()
                    state["accel"] = (a["paidMinutes"] * 60) / real if real > 0 else None
            elif a["type"] == "extendSession" and state["session"]:
                r = c.send("post", f"{API}/sessions/{state['session']['id']}/extensions", headers=idem(),
                           body=json.dumps({"additionalMinutes": a["addMinutes"]}).encode(), token=td)
                state["results"].append(("sessionExtended", r.status, r.json() or {}))
            log(f"  t={t:6.0f} с сценария: {a['type']} → HTTP {state['results'][-1][1]}")

    total = samples[-1]["t"] / args.scale
    log(f"маршрут: {len(points)} точек, {total:.0f} с при ускорении ×{args.scale:g}")
    step = Step("positions", "машина едет: координаты принимаются", tags.get("positions", []))
    res = play(points, base=args.base, vehicle_id=vid, scale=args.scale, batch=5, token=td,
               extra_body=sc_run.get("requestBody", {}), on_tick=on_tick)
    steps.append(step.done(res["errors"] == 0, f"ошибок пачек: {res['errors']}"))

    for logical, status, data in state["results"]:
        ok = status in (200, 201)
        msg = f"HTTP {status}" + ("" if ok else f": {json.dumps(data, ensure_ascii=False)[:200]}")
        if logical == "sessionExtended" and ok and state["session"]:
            ok = oracle.parse_ts(data["paidUntil"]) > oracle.parse_ts(state["session"]["paidUntil"])
            msg = msg if ok else "paidUntil после продления не увеличился"
        steps.append(Step(logical, {"sessionStarted": "старт сессии (Idempotency-Key)",
                                    "sessionExtended": "продление переносит paidUntil"}[logical],
                          tags.get(logical, [])).done(ok, msg))

    # --- строгая сверка — только события сценарных зон и сессии; прочие зоны карты — для информации
    def involves_story(e: dict) -> bool:
        ids = {str(e["data"].get(k)) for k in ("zoneId", "fromZoneId", "toZoneId") if e["data"].get(k)}
        return not ids or bool(ids & set(story))
    other = [e for e in expected if not involves_story(e)]
    expected = [e for e in expected if involves_story(e)]
    for e in expected:
        if e["logical"] in state["sentAt"]:
            e["atSec"] = round(state["sentAt"][e["logical"]] * args.scale, 1)
    if other:
        log(f"  событий соседних зон по маршруту: {len(other)} — проверяются только сценарные зоны {story}")

    # --- журнал событий (admin), с ожиданием outbox и таймеров
    accel = state["accel"]
    timers_fast = accel is not None and abs(accel - args.scale) / args.scale < 0.3
    skip = set() if (timers_fast or args.timers) else {"sessionExpiring", "sessionExpired"}
    if skip and state["session"]:
        log(f"  таймеры сервера ускорены в {accel:.1f}× (не ×{args.scale:g}) — проверка предупреждения пропущена "
            f"(запусти с --timers, чтобы дождаться)")
    last_due = max((e["atSec"] for e in expected if e["atSec"] is not None and e["logical"] not in skip), default=0)
    deadline = time.monotonic() + max(0.0, last_due / args.scale - (datetime.now(timezone.utc) + timedelta(seconds=skew) - start).total_seconds()) + 8
    while True:
        evs = list_all(c, f"{API}/events", {"vehicleId": vid, "from": start.isoformat().replace("+00:00", "Z")}, ta)
        observed = sorted(evs, key=lambda e: oracle.parse_ts(e["occurredAt"]))
        matched = oracle.match(expected, observed, start, args.scale, zone_uuid, skip_logical=skip)
        if all(m["found"] for m in matched if m["ok"] is not None) or time.monotonic() > deadline:
            break
        time.sleep(1)

    titles = {"zoneEntered": "въезд в зону", "zoneExited": "выезд из зоны", "zoneChanged": "смена зоны",
              "sessionStarted": "событие старта сессии", "sessionExtended": "событие продления",
              "sessionExpiring": "предупреждение за 10 мин", "sessionExpired": "окончание оплаты",
              "sessionClosed": "сессия закрыта при выезде"}
    for m in matched:
        e = m["expected"]
        name = f"журнал: {titles.get(e['logical'], e['logical'])} ({e['type']} на {e['atSec'] / args.scale:.0f} с)"
        steps.append(Step(e["logical"], name, tags.get(e["logical"], [])).done(m["ok"], m["why"]))

    # --- журнал целиком, права и аудит
    types = [e["type"] for e in observed]
    steps.append(Step("journal", f"журнал в админке: событий за прогон {len(observed)}", tags.get("journal", []))
                 .done(len(observed) > 0, "журнал пуст"))
    r = c.send("get", f"{API}/events", {"vehicleId": vid}, token=td)
    steps.append(Step("journal", "водителю журнал недоступен (403)", tags.get("journal", []) + secs.get("authz", []))
                 .done(r.status == 403, f"водитель получил {r.status}"))
    audited = [e for e in observed if e["type"] in (sc["eventTypes"]["sessionStarted"], sc["eventTypes"]["sessionClosed"])]
    who = lambda e: any(k in json.dumps(e.get("payload", {})).lower() for k in ("driverid", "userid", "actor", "performedby", "initiatedby"))  # noqa: E731
    steps.append(Step("audit", "аудит: в событиях сессии есть пользователь и время", secs.get("audit", []) + tags.get("journal", []))
                 .done(bool(audited) and all(who(e) and e.get("occurredAt") for e in audited),
                       "нет событий сессии" if not audited else
                       "в payload нет пользователя: " + ", ".join(sorted({k for e in audited for k in e.get('payload', {})}))))

    # --- уведомления водителя
    notes_api = list_all(c, f"{API}/notifications", {"vehicleId": vid}, td)
    fresh = [n for n in notes_api if oracle.parse_ts(n["createdAt"]) >= start - timedelta(seconds=2)]
    kinds = {n.get("kind") for n in fresh}
    want = {sc["notificationKinds"][m["expected"]["logical"]] for m in matched
            if m["ok"] is not None and m["expected"]["logical"] in sc.get("notificationKinds", {})}
    missing = sorted(want - kinds)
    steps.append(Step("notifications", f"уведомления водителю: {', '.join(sorted(kinds)) or 'нет'}",
                      tags.get("notifications", [])).done(not missing, f"нет уведомлений: {', '.join(missing)}"))

    write_outputs(args.out, steps, secs, {"base": args.base, "start": start.isoformat(), "scale": args.scale,
                                          "serverTimerAccel": accel, "notes": notes, "observedTypes": types})
    failed = [s for s in steps if s.status == "failed"]
    log(f"\ne2e: шагов {len(steps)}, провалено {len(failed)}, пропущено {sum(s.status == 'skipped' for s in steps)}")
    for s in steps:
        mark = {"passed": "✓", "failed": "✗", "skipped": "–"}[s.status]
        log(f"  {mark} {' '.join(s.tags)} {s.title}" + (f" — {s.message}" if s.message and s.status != "passed" else ""))
    return 1 if failed else 0


def write_outputs(out: Path, steps: list[Step], secs: dict, meta: dict) -> None:
    from xml.sax.saxutils import escape, quoteattr
    out.mkdir(parents=True, exist_ok=True)
    cases = []
    for s in steps:
        name = f"{' '.join(s.tags) + ' ' if s.tags else ''}{s.title}"
        inner = (f"<failure message={quoteattr(s.message)}>{escape(s.message)}</failure>" if s.status == "failed" else
                 f"<skipped message={quoteattr(s.message)}/>" if s.status == "skipped" else "")
        cases.append(f'  <testcase classname="e2e.{s.logical}" name={quoteattr(name)} time="{s.duration}">{inner}</testcase>')
    failed = sum(s.status == "failed" for s in steps)
    (out / "e2e-junit.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<testsuite name="e2e" tests="{len(steps)}" failures="{failed}" '
        f'skipped="{sum(s.status == "skipped" for s in steps)}">\n' + "\n".join(cases) + "\n</testsuite>\n",
        encoding="utf-8")
    (out / "e2e.json").write_text(json.dumps({**meta, "steps": [s.__dict__ for s in steps]}, ensure_ascii=False,
                                             indent=1, default=str), encoding="utf-8")
    # нарушения прав и аудита — находки для security-контура (там e2e — основная проверка audit)
    rules = {"audit": ("CWE-778", "5.0"), "journal": ("CWE-285", "7.5")}
    results = [{"ruleId": f"e2e.{s.logical}", "level": "error", "message": {"text": f"{s.title} — {s.message}"},
                "locations": [{"physicalLocation": {"artifactLocation": {"uri": "e2e"}}}],
                "properties": {"req": [t for t in s.tags if t.startswith("SEC-")]}}
               for s in steps if s.status == "failed" and s.logical in rules and any(t.startswith("SEC-") for t in s.tags)]
    raw = out / "raw"
    raw.mkdir(exist_ok=True)
    (raw / "e2e.sarif").write_text(json.dumps({"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "e2e", "rules": [
        {"id": f"e2e.{k}", "properties": {"security-severity": v[1], "tags": [v[0]]}} for k, v in rules.items()]}},
        "results": results}]}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
