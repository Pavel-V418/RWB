"""Мок платёжного API «Паркоматики» для стенда и e2e (только стандартная библиотека).

POST /api/v1/parkings                 {externalZoneId, plateNumber, vehicleType, durationMinutes, startsAt?} → 201
POST /api/v1/parkings/{id}/extensions {minutes}                                                         → 200
POST /api/v1/parkings/{id}/stop                                                                         → 200
GET  /api/v1/parkings/{id}                                                                              → 200
GET  /health                                                                                            → 200

Ответ парковки: {parkingId, externalZoneId, plateNumber, status, startedAt, paidUntil, amountKopecks}.
Заголовок Idempotency-Key: повтор с тем же ключом возвращает первый ответ (как у настоящих платёжных API).
Сбои для демонстрации устойчивости: FAIL_RATE (0..1) — доля ответов 503, LATENCY_MS — задержка,
заголовок X-Mock-Fail: 1 — гарантированный 503. Цена: PRICE_PER_HOUR_KOPECKS (по умолчанию 36000).
"""

import json
import math
import os
import random
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FAIL_RATE = float(os.environ.get("FAIL_RATE", "0"))
LATENCY_MS = int(os.environ.get("LATENCY_MS", "0"))
PRICE = int(os.environ.get("PRICE_PER_HOUR_KOPECKS", "36000"))
PLATE = re.compile(r"^[АВЕКМНОРСТУХ][0-9]{3}[АВЕКМНОРСТУХ]{2}[0-9]{2,3}$")

parkings: dict[str, dict] = {}
replies: dict[str, tuple[int, dict]] = {}
lock = threading.Lock()


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def amount(minutes: int) -> int:
    return math.ceil(PRICE * minutes / 60)


class Handler(BaseHTTPRequestHandler):
    server_version = "parkomatika-mock/1.0"

    def log_message(self, fmt, *args):  # компактный лог в stdout контейнера
        print(f"{self.command} {self.path} → {fmt % args}", flush=True)

    def reply(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def error(self, status: int, code: str, message: str) -> None:
        self.reply(status, {"code": code, "message": message})

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def chaos(self) -> bool:
        if LATENCY_MS:
            time.sleep(LATENCY_MS / 1000)
        if self.headers.get("X-Mock-Fail") == "1" or random.random() < FAIL_RATE:
            self.error(503, "unavailable", "Провайдер временно недоступен (мок)")
            return True
        return False

    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"status": "ok", "parkings": len(parkings)})
        if self.chaos():
            return
        m = re.fullmatch(r"/api/v1/parkings/([\w-]+)", self.path)
        if m and m.group(1) in parkings:
            return self.reply(200, parkings[m.group(1)])
        self.error(404, "not_found", "Парковка не найдена")

    def do_POST(self):
        if self.chaos():
            return
        key = self.headers.get("Idempotency-Key")
        if key and key in replies:
            return self.reply(*replies[key])
        status, body = self.route(self.body())
        if key and status < 400:
            replies[key] = (status, body)
        self.reply(status, body)

    def route(self, req: dict) -> tuple[int, dict]:
        now = datetime.now(timezone.utc)
        with lock:
            if self.path == "/api/v1/parkings":
                minutes = req.get("durationMinutes")
                if not req.get("externalZoneId") or not PLATE.match(str(req.get("plateNumber", ""))) \
                        or not isinstance(minutes, int) or not 15 <= minutes <= 1440:
                    return 400, {"code": "validation_failed", "message": "externalZoneId, plateNumber (А123ВС178), durationMinutes 15..1440"}
                started = datetime.fromisoformat(req["startsAt"].replace("Z", "+00:00")) if req.get("startsAt") else now
                parking = {
                    "parkingId": str(uuid.uuid4()), "externalZoneId": str(req["externalZoneId"]),
                    "plateNumber": req["plateNumber"], "vehicleType": req.get("vehicleType", "car"), "status": "active",
                    "startedAt": iso(started), "paidUntil": iso(started + timedelta(minutes=minutes)), "amountKopecks": amount(minutes),
                }
                parkings[parking["parkingId"]] = parking
                return 201, parking

            m = re.fullmatch(r"/api/v1/parkings/([\w-]+)/(extensions|stop)", self.path)
            if not m or m.group(1) not in parkings:
                return 404, {"code": "not_found", "message": "Парковка не найдена"}
            parking = parkings[m.group(1)]
            if parking["status"] != "active":
                return 409, {"code": "parking_not_active", "message": "Парковка уже завершена"}
            if m.group(2) == "stop":
                parking["status"] = "stopped"
                return 200, parking
            minutes = req.get("minutes")
            if not isinstance(minutes, int) or not 15 <= minutes <= 1440:
                return 400, {"code": "validation_failed", "message": "minutes 15..1440"}
            paid = datetime.fromisoformat(parking["paidUntil"].replace("Z", "+00:00")) + timedelta(minutes=minutes)
            parking["paidUntil"] = iso(paid)
            parking["amountKopecks"] += amount(minutes)
            return 200, parking


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8090"))
    print(f"parkomatika-mock on :{port} FAIL_RATE={FAIL_RATE} LATENCY_MS={LATENCY_MS}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
