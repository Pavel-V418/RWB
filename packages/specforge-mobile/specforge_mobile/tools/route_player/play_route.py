#!/usr/bin/env python3
"""
Проигрыватель mock-маршрута: шлёт points.json пачками в
POST {base}/api/v1/vehicles/{vehicleId}/positions в ускоренном времени.

Используется тремя способами:
  1) CLI для демо/видео:   python play_route.py points.json --base $STAND_URL --scale 60
  2) из e2e-теста:         from play_route import play;  play(points, ..., on_tick=cb)
  3) сухой прогон:         python play_route.py points.json --dry-run

Авторизация: --token / API_TOKEN, либо API_LOGIN + API_PASSWORD (вход через /auth/tokens).
Учётные данные только из окружения, в репозиторий не коммитить.
Только стандартная библиотека.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

# Контракт участника 3: тело {"source": "mock", "points": [PositionPoint, ...]}.
DEFAULT_PATH = "/api/v1/vehicles/{vehicleId}/positions"
LOGIN_PATH = "/api/v1/auth/tokens"
BODY_KEY = "points"


def _post(url: str, payload: dict, token: str | None, timeout: float = 10.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {e.code} от {url}: {body}") from None


def login(base: str, user: str, password: str) -> str:
    """POST /auth/tokens {login, password} -> accessToken."""
    res = _post(base.rstrip("/") + LOGIN_PATH, {"login": user, "password": password}, None)
    return res["accessToken"]


def play(points: list[dict], *, base: str, vehicle_id: str, scale: float = 60.0,
         batch: int = 5, token: str | None = None, path: str = DEFAULT_PATH,
         dry_run: bool = False, extra_body: dict | None = None, on_tick: Callable[[float, int], None] | None = None) -> dict:
    """
    Шлёт точки так, чтобы сценарное время шло в `scale` раз быстрее реального.
    batch — сколько точек сценария в одной пачке (при sampleSec=1 это секунды сценария).
    on_tick(scenario_t, sent_count) — колбэк после каждой пачки (e2e выполняет здесь действия).
    """
    url = base.rstrip("/") + path.format(vehicleId=vehicle_id)
    t0_real = time.monotonic()
    sent = 0
    errors = 0
    for i in range(0, len(points), batch):
        chunk = points[i:i + batch]
        scen_t = chunk[-1]["_tSec"]
        # ждём, пока реальное время «догонит» сценарное
        delay = scen_t / scale - (time.monotonic() - t0_real)
        if delay > 0:
            time.sleep(delay)
        body = dict(extra_body or {})
        body[BODY_KEY] = [{k: v for k, v in p.items() if not k.startswith("_")} for p in chunk]
        if dry_run:
            pass
        else:
            for attempt in range(3):
                try:
                    _post(url, body, token)
                    break
                except Exception as e:  # сеть/5xx — повтор, потом считаем ошибкой
                    if attempt == 2:
                        errors += 1
                        print(f"[play] ошибка пачки t={scen_t}: {e}", file=sys.stderr)
                    time.sleep(0.2 * (attempt + 1))
        sent += len(chunk)
        if on_tick:
            on_tick(scen_t, sent)
    return {"sent": sent, "errors": errors, "realSec": round(time.monotonic() - t0_real, 2)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("points", type=Path)
    ap.add_argument("--base", default=os.environ.get("STAND_URL", "http://localhost:4010"))
    ap.add_argument("--vehicle", default=None, help="id машины (по умолчанию из expected_events.json рядом)")
    ap.add_argument("--scale", type=float, default=float(os.environ.get("TIME_SCALE", 60)))
    ap.add_argument("--batch", type=int, default=5)
    ap.add_argument("--token", default=os.environ.get("API_TOKEN"),
                    help="готовый токен; иначе вход по API_LOGIN / API_PASSWORD из окружения")
    ap.add_argument("--source", default="mock", help="значение поля source в теле запроса")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    points = json.loads(args.points.read_text(encoding="utf-8"))
    vehicle = args.vehicle
    if not vehicle:
        exp = args.points.parent / "expected_events.json"
        vehicle = json.loads(exp.read_text(encoding="utf-8"))["vehicleId"]

    token = args.token
    if not token and not args.dry_run and os.environ.get("API_LOGIN"):
        token = login(args.base, os.environ["API_LOGIN"], os.environ["API_PASSWORD"])
        print("[play] вход выполнен")

    def tick(t: float, n: int) -> None:
        if n % 100 < args.batch:
            print(f"[play] сценарий {t:6.0f} с, отправлено {n}/{len(points)}")

    res = play(points, base=args.base, vehicle_id=vehicle, scale=args.scale, batch=args.batch,
               token=token, dry_run=args.dry_run, extra_body={"source": args.source}, on_tick=tick)
    print(f"[play] готово: {res}")
    sys.exit(1 if res["errors"] else 0)


if __name__ == "__main__":
    main()
