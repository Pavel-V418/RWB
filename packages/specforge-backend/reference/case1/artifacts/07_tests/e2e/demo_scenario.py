"""E2E-сценарий кейса 1 на живом стенде (демо для жюри и проверка кода backend-агента).

    python 07_tests/e2e/demo_scenario.py [--base http://127.0.0.1:8080] [--timers]

--timers: проверка фоновых задач (FR-9, FR-10). Стенд должен быть запущен с TIME_SCALE (например, 120:
60 минут оплаты проходят за 30 секунд): предупреждение за 10 минут до конца и истечение сессии.

Водитель въезжает в зону у Казанского собора → система определяет зону и шлёт уведомление → водитель оплачивает
парковку (через провайдера «Паркоматика») → продлевает → завершает; администратор видит журнал событий →
водитель снова платит и уезжает к Гостиному двору: выезд закрывает сессию, въезд в соседнюю зону — смена зоны.
Код выхода ≠ 0, если хоть один шаг ответил не так, как ожидается; вывод — готовый текст для --feedback агента.
"""

import argparse
import datetime
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

VEHICLE = "0192f000-0000-7000-8000-00000000de01"  # veh-demo-001 из seed/300_demo.sql
IN_ZONE = (59.933766, 30.324293)                  # зона 514725 «Казанская пл. дом 2»
IN_ZONE_B = (59.933633, 30.333802)                # зона 514660 «Садовая ул. дом 17», ~530 м от 514725
OUTSIDE = (59.9400, 30.3000)

failures: list[str] = []


def call(base, method, path, body=None, token=None, key=None):
    req = urllib.request.Request(base + "/api/v1" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if key:
        req.add_header("Idempotency-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode(errors="replace")[:300]


def step(name, result, expected_status, check=None, show=()):
    status, body = result
    ok = status == expected_status and (check is None or check(body))
    brief = {k: body.get(k) for k in show} if show and isinstance(body, dict) else ""
    print(f"{'✅' if ok else '❌'} {status} {name} {json.dumps(brief, ensure_ascii=False) if brief else ''}")
    if not ok:
        failures.append(f"{name}: ожидался {expected_status}, получен {status}: {json.dumps(body, ensure_ascii=False)[:400]}")
    return body if isinstance(body, dict) else {}


_last_point_at = None


def points(*coords, accuracy=None):
    """Точки с шагом 100 мс, последняя — сейчас, и всегда позже точек прошлых запросов и прошлых прогонов:
    порядок по recordedAt решает, какие две точки подряд подтверждают смену зоны (AC-FR-7-02)."""
    global _last_point_at
    now = datetime.datetime.now(datetime.timezone.utc)
    pts = []
    for i, (lat, lon) in enumerate(coords):
        at = now - datetime.timedelta(milliseconds=100 * (len(coords) - 1 - i))
        if _last_point_at is not None and at <= _last_point_at:
            at = _last_point_at + datetime.timedelta(milliseconds=1)
        _last_point_at = at
        pts.append({"lat": lat, "lon": lon, "recordedAt": at.isoformat()})
    if accuracy is not None:
        for pt in pts:
            pt["accuracyM"] = accuracy
    return pts


def zone_change(base, drv, adm, zone_id):
    """FR-7, FR-8: дрожание GPS, неточные точки, выезд с закрытием сессии, переход в соседнюю зону, журнал."""
    pos = f"/vehicles/{VEHICLE}/positions"
    ext = lambda b, key: (b.get(key) or {}).get("externalZoneId")
    step("смена зоны: дрожание GPS (одна точка вне зоны) → перехода нет", call(base, "POST", pos, {"source": "mock", "points": points(OUTSIDE, IN_ZONE)}, token=drv), 200,
         lambda b: b.get("transition") == "none" and ext(b, "currentZone") == "514725", ("transition",))
    session = step("смена зоны: оплата 60 мин", call(base, "POST", "/sessions", {"vehicleId": VEHICLE, "zoneId": zone_id, "durationMinutes": 60},
                                                   token=drv, key=str(uuid.uuid4())), 201)
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    exit_points = points(OUTSIDE, OUTSIDE)
    # событие перехода датируется recordedAt подтверждающей (второй) точки
    since = datetime.datetime.fromisoformat(exit_points[-1]["recordedAt"]) - datetime.timedelta(milliseconds=1)
    step("смена зоны: выезд (две точки вне зон)", call(base, "POST", pos, {"source": "mock", "points": exit_points}, token=drv), 200,
         lambda b: b.get("transition") == "exited" and b.get("currentZone") is None and ext(b, "previousZone") == "514725", ("transition",))
    step("смена зоны: сессия закрыта при выезде (zone_exit)", call(base, "GET", f"/sessions/{session.get('id')}", token=drv), 200,
         lambda b: b.get("status") == "closed" and b.get("closeReason") == "zone_exit", ("status", "closeReason"))
    step("смена зоны: точки с точностью хуже 100 м не меняют зону", call(base, "POST", pos, {"source": "mock", "points": points(IN_ZONE_B, IN_ZONE_B, accuracy=150)}, token=drv), 200,
         lambda b: b.get("transition") == "none" and b.get("currentZone") is None, ("transition",))
    # мобильное приложение шлёт точки по одной: подтверждение двумя точками работает и между запросами
    step("смена зоны: первая точка в соседней зоне → пока без перехода", call(base, "POST", pos, {"source": "mock", "points": points(IN_ZONE_B)}, token=drv), 200,
         lambda b: b.get("transition") == "none" and b.get("currentZone") is None, ("transition",))
    step("смена зоны: вторая точка, въезд за ≤ 300 с → changed", call(base, "POST", pos, {"source": "mock", "points": points(IN_ZONE_B)}, token=drv), 200,
         lambda b: b.get("transition") == "changed" and ext(b, "currentZone") == "514660" and ext(b, "previousZone") == "514725", ("transition",))
    step("смена зоны: уведомление zone_changed", call(base, "GET", f"/notifications?vehicleId={VEHICLE}&pageSize=20", token=drv), 200,
         lambda b: any(n.get("kind") == "zone_changed" and n.get("createdAt", "") >= started for n in b.get("items", [])))
    seen = set()
    for _ in range(10):  # журнал пишется в той же транзакции, повторы — запас на нагруженный стенд
        items = (call(base, "GET", f"/events?vehicleId={VEHICLE}&from={urllib.parse.quote(since.isoformat())}&pageSize=50", token=adm)[1] or {}).get("items", [])
        seen = {e.get("type") for e in items}
        if {"ZoneExited", "ZoneChanged"} <= seen:
            break
        time.sleep(0.5)
    step("смена зоны: журнал ZoneExited и ZoneChanged", (200, {"types": sorted(t for t in seen if t)}), 200,
         lambda b: {"ZoneExited", "ZoneChanged"} <= set(b["types"]), ("types",))

    # AC-FR-6-02: координата на 10 минут позже времени сервера → 400 по полю recordedAt, пакет не сохраняется
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10)
    step("GPS: точка из будущего → 400 по recordedAt", call(base, "POST", pos, {"source": "mock", "points": [
        {"lat": IN_ZONE_B[0], "lon": IN_ZONE_B[1], "recordedAt": future.isoformat()}]}, token=drv), 400,
         lambda b: any("recordedat" in k.lower() for k in (b.get("errors") or {})), ("code", "errors"))


def timers(base, drv, zone_id, timeout):
    """Оплата на минимальный срок и ожидание: предупреждение за 10 минут (FR-9), затем истечение (FR-10)."""
    session = step("таймеры: оплата 60 мин", call(base, "POST", "/sessions", {"vehicleId": VEHICLE, "zoneId": zone_id, "durationMinutes": 60},
                                                  token=drv, key=str(uuid.uuid4())), 201, show=("paidUntil",))
    sid = session.get("id")
    deadline, warned, expired = time.monotonic() + timeout, False, False
    while time.monotonic() < deadline and not (warned and expired):
        time.sleep(2)
        items = call(base, "GET", f"/notifications?vehicleId={VEHICLE}&pageSize=50", token=drv)[1] or {}
        kinds = {n.get("kind") for n in items.get("items", []) if n.get("createdAt", "") >= session.get("startedAt", "")}
        warned = warned or "session_expiring" in kinds
        expired = expired or (call(base, "GET", f"/sessions/{sid}", token=drv)[1] or {}).get("status") == "expired"
    step("таймеры: предупреждение за 10 минут (session_expiring)", (200 if warned else 408, {"warned": warned}), 200)
    step("таймеры: сессия истекла (status = expired)", (200 if expired else 408, {"expired": expired}), 200)
    if not (warned and expired):
        failures.append(f"Фоновые задачи: за {timeout} с реального времени (стенд с TIME_SCALE) не появилось "
                        "уведомление kind = session_expiring и/или сессия не перешла в status = expired. "
                        "Допустимые значения notifications.kind и parking_sessions.status — в ограничениях БД.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8080")
    p.add_argument("--timers", action="store_true", help="проверить таймеры (нужен TIME_SCALE на стенде)")
    p.add_argument("--timeout", type=int, default=120, help="сколько реальных секунд ждать таймеры")
    args = p.parse_args()
    base = args.base

    drv = step("вход водителя", call(base, "POST", "/auth/tokens", {"login": "driver.demo", "password": "Driver-demo-2026!"}), 200).get("accessToken")
    adm = step("вход администратора", call(base, "POST", "/auth/tokens", {"login": "admin", "password": "Admin-demo-2026!"}), 200).get("accessToken")
    step("неверный пароль → 401", call(base, "POST", "/auth/tokens", {"login": "admin", "password": "wrong-password"}), 401)

    step("зоны на карте (bbox)", call(base, "GET", "/zones?bbox=30.32,59.93,30.34,59.94&limit=5", token=drv), 200,
         lambda b: len(b.get("features", [])) > 0)
    zone = step("зона по координатам", call(base, "GET", f"/zones/by-location?lat={IN_ZONE[0]}&lon={IN_ZONE[1]}", token=drv), 200,
                lambda b: (b.get("zone") or {}).get("externalZoneId") == "514725").get("zone") or {}

    # Если с прошлого прогона осталась активная сессия — закрываем, чтобы сценарий был повторяемым
    active = call(base, "GET", f"/vehicles/{VEHICLE}/active-session", token=drv)
    if active[0] == 200:
        call(base, "POST", f"/sessions/{active[1]['id']}/closure", token=drv, key=str(uuid.uuid4()))
    step("GPS: машина вне зоны", call(base, "POST", f"/vehicles/{VEHICLE}/positions", {"source": "mock", "points": points(OUTSIDE, OUTSIDE)}, token=drv), 200)
    # changed допустим при повторном прогоне: прошлый закончился в соседней зоне 514660 меньше 300 с назад
    step("GPS: въезд в зону", call(base, "POST", f"/vehicles/{VEHICLE}/positions", {"source": "mock", "points": points(IN_ZONE, IN_ZONE)}, token=drv), 200,
         lambda b: b.get("transition") in ("entered", "changed") and (b.get("currentZone") or {}).get("externalZoneId") == "514725", ("transition", "accepted"))

    key = str(uuid.uuid4())
    session = step("оплата парковки 60 мин", call(base, "POST", "/sessions", {"vehicleId": VEHICLE, "zoneId": zone.get("id"), "durationMinutes": 60}, token=drv, key=key), 201,
                   lambda b: b.get("status") == "active" and b.get("externalPaymentId"), ("status", "amountKopecks", "paidUntil"))
    step("повтор с тем же ключом — та же сессия", call(base, "POST", "/sessions", {"vehicleId": VEHICLE, "zoneId": zone.get("id"), "durationMinutes": 60}, token=drv, key=key), 201,
         lambda b: b.get("id") == session.get("id"))
    step("вторая сессия → 409", call(base, "POST", "/sessions", {"vehicleId": VEHICLE, "zoneId": zone.get("id"), "durationMinutes": 60}, token=drv, key=str(uuid.uuid4())), 409)
    sid = session.get("id")
    step("активная сессия", call(base, "GET", f"/vehicles/{VEHICLE}/active-session", token=drv), 200, lambda b: b.get("id") == sid, ("remainingSeconds",))
    step("продление +30 мин", call(base, "POST", f"/sessions/{sid}/extensions", {"additionalMinutes": 30}, token=drv, key=str(uuid.uuid4())), 200,
         lambda b: b.get("amountKopecks") == session.get("amountKopecks", 0) * 3 // 2, ("amountKopecks", "paidUntil"))
    step("завершение", call(base, "POST", f"/sessions/{sid}/closure", token=drv, key=str(uuid.uuid4())), 200, lambda b: b.get("status") == "closed", ("status", "closeReason"))

    step("уведомления водителя", call(base, "GET", f"/notifications?vehicleId={VEHICLE}", token=drv), 200,
         lambda b: any(n.get("kind") == "zone_entered" for n in b.get("items", [])))
    step("журнал событий (admin)", call(base, "GET", "/events?pageSize=20", token=adm), 200,
         lambda b: {"SessionStarted", "SessionClosed"} <= {e.get("type") for e in b.get("items", [])})
    step("журнал событий водителю → 403", call(base, "GET", "/events", token=drv), 403)
    zone_change(base, drv, adm, zone.get("id"))

    if args.timers:
        timers(base, drv, zone.get("id"), args.timeout)

    print(f"\n{'ВСЁ ПРОШЛО' if not failures else f'ПРОВАЛЕНО ШАГОВ: {len(failures)}'}")
    if failures:
        print("\nТекст для --feedback агента:\n" + "\n".join(f"- {f}" for f in failures))
        sys.exit(1)


if __name__ == "__main__":
    main()
