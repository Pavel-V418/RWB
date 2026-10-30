"""Демо-оркестратор для веб-консоли: реализует contracts/console/console-api.yaml и имитирует прогон.

  python -m console.demo_server [--dist console/app/dist/app/browser] [--port 8765] [--speed 1.0] [--artifacts DIR]

Прогоны помечены simulated: true — консоль показывает «Демо-режим»: токены и время здесь — профиль, а не замер.
--artifacts DIR — папка артефактов настоящего прогона (01_requirements/…, 03_ux/…): список и содержимое файлов
берутся оттуда; без неё показываются только имена файлов, а просмотр отвечает 404 с пояснением.

Нужен, пока бэкенд оркестратора (участник 1) не готов, и как эталон его контракта:
те же пути, модели, SSE-события (`event:` = тип, `data:` = RunEvent, `id:` = seq), режим approve.

Режим approve (этап анализа требований): у этапа есть summary и questions (из 01_requirements/requirements.json —
openQuestions / assumptions, иначе общие вопросы). approve с неотвеченным блокирующим вопросом → 409;
reject без комментария → 422; reject с комментарием перезапускает этап: attempts + 1, running → gate → awaiting_approval.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import random
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

BASE = "/console-api/v1"
STAGES = [
    ("01_requirements", "Анализ требований", [], "полнота · INVEST · критик", ["01_requirements/requirements.json", "01_requirements/stories.md", "01_requirements/acceptance.feature"]),
    ("02_system", "Системный анализ", ["01_requirements"], "spectral · asyncapi · DDL→PostGIS", ["02_system/system-analysis.md", "02_system/openapi.yaml", "02_system/asyncapi.yaml", "02_system/schema.sql"]),
    ("03_ux", "Проектирование интерфейса", ["02_system"], "схема экранов · сверка с OpenAPI · покрытие FR", ["03_ux/screen-spec.web.json", "03_ux/screen-spec.android.json"]),
    ("04_backend", "Backend", ["02_system"], "dotnet build · xUnit", ["04_backend/src/App.Api/Program.cs", "04_backend/tests/App.Tests.csproj"]),
    ("05_web", "Web-админка", ["03_ux"], "kit-lint · ts-check · ng build · jest", ["05_web/admin/src/app/generated/routes.ts", "05_web/fe-report.json"]),
    ("06_android", "Android", ["03_ux"], "gradle assembleDebug · JUnit", ["06_android/app/build/outputs/apk/debug/app-debug.apk"]),
    ("07_tests", "Тесты и безопасность", ["04_backend", "05_web", "06_android"], "e2e 9/9 · Semgrep · Trivy · ZAP", ["07_tests/test-report.md", "07_tests/security-report.md", "07_tests/ui/ui-review.md"]),
    ("08_infra", "Сборка и развёртывание", ["07_tests"], "docker compose up · healthcheck", ["08_infra/docker-compose.yml", "08_infra/.gitlab-ci.yml"]),
    ("09_docs", "Документация", ["08_infra"], "MkDocs build · трассируемость ≥ 95%", ["09_docs/index.md", "09_docs/traceability.md", "09_docs/user-guide.md"]),
]
GENERIC_QUESTIONS = [  # без привязки к предметной области: подходят для любого ТЗ
    {"id": "q-roles", "text": "Все ли роли пользователей перечислены в требованиях?",
     "assumption": "Других ролей, кроме названных в требованиях, нет", "blocking": True},
    {"id": "q-integrations", "text": "Какие внешние системы нужно подключить сразу, а какие можно заменить заглушкой?",
     "assumption": "Все внешние интеграции на первом этапе — заглушки с тем же контрактом", "blocking": False},
    {"id": "q-retention", "text": "Сколько хранить историю и журнал событий?",
     "assumption": "История хранится без ограничения срока", "blocking": False},
]
PROFILE = {  # (токены вход, выход, вызовы, длительность, попыток, ремонт)
    "01_requirements": (42000, 9000, 4, 50, 1, False), "02_system": (61000, 18000, 6, 70, 1, False),
    "03_ux": (38000, 14000, 13, 45, 2, True), "04_backend": (120000, 36000, 22, 120, 2, True),
    "05_web": (6000, 2500, 2, 90, 1, False), "06_android": (70000, 21000, 14, 150, 2, True),
    "07_tests": (55000, 12000, 9, 110, 1, False), "08_infra": (8000, 2000, 2, 60, 1, False), "09_docs": (30000, 15000, 6, 40, 1, False),
}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Store:
    def __init__(self, speed: float, artifacts_dir: Path | None = None):
        self.artifacts_dir = artifacts_dir.resolve() if artifacts_dir else None
        self.runs: dict[str, dict] = {}
        self.events: dict[str, list[dict]] = {}
        self.approvals: dict[str, threading.Event] = {}
        self.decisions: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.speed = speed

    def create(self, title: str, mode: str) -> dict:
        rid = uuid.uuid4().hex[:12]
        stages = [{"id": i, "title": t, "status": "pending", "dependsOn": d, "attempts": 0, "tokensIn": 0, "tokensOut": 0, "tokensCached": 0,
                   "startedAt": None, "finishedAt": None, "gate": {"name": g, "passed": False, "errors": []}, "artifacts": []} for i, t, d, g, _ in STAGES]
        run = {"id": rid, "title": title or "Прогон", "mode": mode, "status": "running", "createdAt": now(), "finishedAt": None, "stages": stages,
               "simulated": True}
        with self.lock:
            self.runs[rid], self.events[rid] = run, []
        threading.Thread(target=self.simulate, args=(rid,), daemon=True).start()
        return run

    def emit(self, rid: str, typ: str, stage: dict | None = None, message: str = "") -> None:
        with self.lock:
            ev = {"seq": len(self.events[rid]) + 1, "type": typ, "at": now(), "message": message}
            if stage:
                ev["stageId"], ev["stage"] = stage["id"], json.loads(json.dumps(stage))
            self.events[rid].append(ev)

    def sleep(self, sec: float) -> None:
        time.sleep(max(0.05, sec / self.speed))

    def simulate(self, rid: str) -> None:
        run = self.runs[rid]
        done: set[str] = set()
        by = {s["id"]: s for s in run["stages"]}
        while len(done) < len(by) and run["status"] == "running" or run["status"] == "awaiting_approval":
            ready = [s for s in run["stages"] if s["id"] not in done and s["status"] == "pending" and all(d in done for d in s["dependsOn"])]
            threads = [threading.Thread(target=self.run_stage, args=(rid, s)) for s in ready]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            if run["status"] == "cancelled":
                break
            for s in ready:
                if s["status"] in ("passed", "skipped"):
                    done.add(s["id"])
                elif s["status"] == "failed":
                    run["status"] = "failed"
            if run["status"] == "failed" or not ready:
                break
        if run["status"] == "running":
            run["status"] = "passed"
            run["repoUrl"], run["appUrl"] = f"http://gitlab.local/specforge/{rid}", "http://localhost:8080"
        run["finishedAt"] = now()
        self.emit(rid, "run.finished", message=run["status"])

    def run_stage(self, rid: str, s: dict) -> None:
        run = self.runs[rid]
        tin, tout, calls, dur, attempts, repair = PROFILE[s["id"]]
        s["status"], s["startedAt"], s["attempts"] = "running", now(), 1
        self.emit(rid, "stage.started", s, f"{s['title']}: старт")
        self.sleep(dur / 25)
        s["tokensIn"], s["tokensOut"], s["tokensCached"] = tin // 2, tout // 2, int(tin * 0.3)
        s["status"] = "gate"
        self.emit(rid, "stage.gate", s, f"{s['title']}: шлюз «{s['gate']['name']}»")
        self.sleep(dur / 40)
        if repair:
            s["status"] = "repairing"
            s["gate"]["errors"] = [{"file": "src/app/slots/x.slot.ts" if s["id"] == "05_web" else "src/App.Domain/Service.cs", "line": random.randint(10, 90),
                                    "message": "TS2322: Type 'string | undefined' is not assignable to type 'string'." if s["id"] == "05_web" else "CS0103: The name 'item' does not exist in the current context",
                                    "severity": "error"}]
            self.emit(rid, "stage.repair", s, f"{s['title']}: исправление по ошибке шлюза")
            self.sleep(dur / 30)
            s["attempts"] = attempts
            s["gate"]["errors"] = []
        s["tokensIn"], s["tokensOut"], s["tokensCached"] = tin, tout, int(tin * 0.62)
        s["gate"]["passed"] = True
        s["artifacts"] = self.stage_files(s["id"])
        if run["mode"] == "approve" and s["id"] == "01_requirements":
            if not self.await_decision(rid, s):
                return
        s["status"], s["finishedAt"] = "passed", now()
        self.emit(rid, "stage.passed", s, f"{s['title']}: шлюз пройден")

    def requirements(self) -> dict:
        f = self.file("01_requirements/requirements.json")
        try:
            return json.loads(f.read_text(encoding="utf-8")) if f else {}
        except (OSError, ValueError):
            return {}

    def questions(self) -> list[dict]:
        """Вопросы этапа анализа: openQuestions и assumptions из requirements.json, иначе общие."""
        req = self.requirements()
        out: list[dict] = []
        for i, q in enumerate(req.get("openQuestions") or [], 1):
            if isinstance(q, str):
                q = {"text": q}
            text = q.get("text") or q.get("question")
            if not text:
                continue
            item = {"id": str(q.get("id") or f"oq-{i}"), "text": str(text), "blocking": bool(q.get("blocking", False))}
            if q.get("assumption"):
                item["assumption"] = str(q["assumption"])
            out.append(item)
        for i, a in enumerate(req.get("assumptions") or [], 1):
            text = a if isinstance(a, str) else (a.get("text") or a.get("assumption"))
            if text:
                aid = str(a.get("id")) if isinstance(a, dict) and a.get("id") else f"as-{i}"
                out.append({"id": aid, "text": "Подтвердите допущение или поправьте его", "assumption": str(text), "blocking": False})
        return out or json.loads(json.dumps(GENERIC_QUESTIONS))

    def summary(self, s: dict, remarks: list[str]) -> str:
        n = len(self.requirements().get("functional") or [])
        base = f"{s['title']}: выделено функциональных требований — {n}." if n else f"{s['title']}: этап выполнен (демо)."
        return base + "".join(f" Доработано по замечанию: «{r}»." for r in remarks)

    def decide(self, rid: str, body: dict) -> tuple[int, dict]:
        """Решение человека. → (HTTP-код, тело): 200 — принято, 409/422 — ошибка (RFC 7807)."""
        run = self.runs[rid]
        with self.lock:
            if run["status"] != "awaiting_approval":
                return 409, {"title": "Нечего подтверждать", "status": 409, "detail": "Прогон не ждёт решения"}
            stage = next((s for s in run["stages"] if s["status"] == "awaiting_approval"), None)
            if not stage or body.get("stageId") != stage["id"]:
                return 409, {"title": "Этап не ждёт решения", "status": 409, "detail": f"Решение ждёт этап {stage['id'] if stage else '—'}"}
            answers = {str(k): str(v).strip() for k, v in (body.get("answers") or {}).items() if str(v).strip()}
            for q in stage.get("questions") or []:
                if answers.get(q["id"]):
                    q["answer"] = answers[q["id"]]
            decision, comment = body.get("decision"), str(body.get("comment") or "").strip()
            if decision == "reject" and not comment:
                return 422, {"title": "Нужен комментарий", "status": 422, "detail": "Напишите, что исправить: комментарий уйдёт агенту",
                             "errors": {"comment": ["Обязателен при отклонении"]}}
            if decision == "approve":
                missing = [q["id"] for q in stage.get("questions") or [] if q.get("blocking") and not q.get("answer")]
                if missing:
                    return 409, {"title": "Есть неотвеченные вопросы", "status": 409,
                                 "detail": "Ответьте на блокирующие вопросы: " + ", ".join(missing), "errors": {"answers": missing}}
            if decision not in ("approve", "reject"):
                return 422, {"title": "Неизвестное решение", "status": 422, "errors": {"decision": ["approve или reject"]}}
            self.decisions[rid] = {"decision": decision, "comment": comment}
            run["status"] = "running"  # до перезапуска/продолжения повторное решение получит 409
        self.approvals[rid].set()
        return 200, run

    def await_decision(self, rid: str, s: dict) -> bool:
        """Ждать решения человека; reject — перезапуск этапа с замечанием. → False, если прогон отменён."""
        run = self.runs[rid]
        tin, tout, _, dur, _, _ = PROFILE[s["id"]]
        remarks: list[str] = []
        s["questions"] = self.questions()
        while True:
            ev = self.approvals.setdefault(rid, threading.Event())  # до смены статуса: decide() сразу найдёт событие
            s["summary"] = self.summary(s, remarks)
            s["status"] = "awaiting_approval"
            run["status"] = "awaiting_approval"
            self.emit(rid, "run.awaiting_approval", s, "Этап готов — нужно решение")
            ev.wait()
            ev.clear()
            if run["status"] == "cancelled":
                return False
            d = self.decisions.pop(rid, {"decision": "approve"})
            if d["decision"] == "approve":
                return True
            # reject: этап реально перезапускается с замечанием на входе
            remarks.append(d["comment"])
            s["attempts"] += 1
            s["status"], s["gate"]["passed"] = "running", False
            s["gate"]["errors"] = [{"message": f"Замечание оператора: {d['comment']}", "severity": "warning"}]
            self.emit(rid, "stage.started", s, f"{s['title']}: перезапуск по замечанию (попытка {s['attempts']})")
            self.sleep(dur / 25)
            s["tokensIn"], s["tokensOut"] = s["tokensIn"] + tin // 3, s["tokensOut"] + tout // 3
            s["status"] = "gate"
            self.emit(rid, "stage.gate", s, f"{s['title']}: шлюз «{s['gate']['name']}»")
            self.sleep(dur / 40)
            s["gate"]["passed"], s["gate"]["errors"] = True, []

    def metrics(self, rid: str) -> dict:
        run = self.runs[rid]
        stages = [{"stageId": s["id"], "tokensIn": s["tokensIn"], "tokensOut": s["tokensOut"], "tokensCached": s["tokensCached"],
                   "llmCalls": PROFILE[s["id"]][2] if s["status"] == "passed" else 0, "attempts": s["attempts"],
                   "durationSec": PROFILE[s["id"]][3] if s["status"] == "passed" else 0} for s in run["stages"]]
        tot = {k: sum(x[k] for x in stages) for k in ("tokensIn", "tokensOut", "tokensCached", "llmCalls", "attempts", "durationSec")}
        tot["deterministicShare"] = 0.58
        return {"totals": tot, "stages": stages}

    def stage_files(self, stage_id: str) -> list[str]:
        """Файлы этапа: из --artifacts (настоящие), иначе имена из описания этапа."""
        if self.artifacts_dir and (self.artifacts_dir / stage_id).is_dir():
            files = sorted(f for f in (self.artifacts_dir / stage_id).rglob("*") if f.is_file() and "node_modules" not in f.parts)
            return [f.relative_to(self.artifacts_dir).as_posix() for f in files][:200]
        return list(next(a for i, _, _, _, a in STAGES if i == stage_id))

    def file(self, path: str) -> Path | None:
        if not self.artifacts_dir or not re.match(r"^[0-9]{2}_[a-z_]+/.+$", path) or ".." in path.split("/"):
            return None
        f = (self.artifacts_dir / path).resolve()
        return f if f.is_file() and self.artifacts_dir in f.parents else None

    def artifacts(self, rid: str) -> list[dict]:
        out = []
        for s in self.runs[rid]["stages"]:
            for a in s["artifacts"]:
                ext = a.rsplit(".", 1)[-1].lower()
                kind = {"md": "markdown", "json": "json", "yaml": "yaml", "yml": "yaml", "feature": "markdown",
                        "png": "image", "jpg": "image", "jpeg": "image", "svg": "image"}.get(ext, "code")
                f = self.file(a)
                out.append({"path": a, "stageId": s["id"], "size": f.stat().st_size if f else 0, "kind": kind})
        return out


class Handler(SimpleHTTPRequestHandler):
    store: Store

    def log_message(self, *a):
        pass

    def send_json(self, code: int, body) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json" if code < 400 else "application/problem+json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def not_found(self):
        self.send_json(404, {"title": "Не найдено", "status": 404})

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        st = self.store
        if not path.startswith(BASE):
            target = Path(self.translate_path(path))
            if not target.exists() or target.is_dir() and not (target / "index.html").exists():
                self.path = "/index.html"
            return super().do_GET()
        p = path[len(BASE):]
        if p == "/runs":
            items = sorted(st.runs.values(), key=lambda r: r["createdAt"], reverse=True)
            return self.send_json(200, {"items": items, "total": len(items), "page": 1, "size": 20})
        m = re.match(r"^/runs/([^/]+)(/.*)?$", p)
        if not m or m.group(1) not in st.runs:
            return self.not_found()
        rid, sub = m.group(1), m.group(2) or ""
        if sub == "":
            return self.send_json(200, st.runs[rid])
        if sub == "/metrics":
            return self.send_json(200, st.metrics(rid))
        if sub == "/artifacts":
            return self.send_json(200, st.artifacts(rid))
        if sub == "/artifacts/content":
            path = (parse_qs(urlsplit(self.path).query).get("path") or [""])[0]
            f = st.file(path)
            if not f:
                detail = ("Демо-режим без папки артефактов: запустите demo_server с --artifacts <папка прогона>"
                          if not st.artifacts_dir else f"Файл {path} не найден")
                return self.send_json(404, {"title": "Файл недоступен", "status": 404, "detail": detail})
            data = f.read_bytes()
            ctype = mimetypes.guess_type(f.name)[0] or "text/plain"
            if ctype.startswith("text/") or f.suffix.lower() in (".json", ".yaml", ".yml", ".md", ".feature", ".ts", ".cs", ".kt", ".sql"):
                ctype = "text/plain; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return None
        if sub == "/events":
            return self.sse(rid)
        return self.not_found()

    def sse(self, rid: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        sent = int(self.headers.get("Last-Event-ID") or 0)
        try:
            while True:
                evs = self.store.events[rid][sent:]
                for ev in evs:
                    self.wfile.write(f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
                    sent = ev["seq"]
                    if ev["type"] == "run.finished":
                        self.wfile.flush()
                        return
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                time.sleep(0.4)
        except (BrokenPipeError, ConnectionResetError):
            return

    def body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        st = self.store
        p = path[len(BASE):] if path.startswith(BASE) else None
        if p == "/runs":
            raw, ctype = self.body(), self.headers.get("Content-Type", "")
            fields: dict[str, str] = {}
            if "multipart/form-data" in ctype:
                boundary = ctype.split("boundary=", 1)[1].encode()
                for part in raw.split(b"--" + boundary):
                    m = re.search(rb'name="([^"]+)"(?:; filename="([^"]*)")?\r\n(?:[^\r\n]*\r\n)*\r\n(.*)\r\n$', part, re.S)
                    if m:
                        fields[m.group(1).decode()] = m.group(2).decode() if m.group(2) else m.group(3).decode("utf-8", "replace")
            elif raw:
                fields = json.loads(raw)
            if not fields.get("file") and len(fields.get("text", "").strip()) < 20:
                return self.send_json(400, {"title": "Нет требований", "status": 400, "detail": "Загрузите файл или введите текст требований", "errors": {"text": ["Не короче 20 символов"]}})
            run = st.create(fields.get("title") or fields.get("file") or "Прогон", fields.get("mode", "autopilot"))
            return self.send_json(201, run)
        m = re.match(r"^/runs/([^/]+)/(approve|cancel)$", p or "")
        if not m or m.group(1) not in st.runs:
            return self.not_found()
        rid, action = m.groups()
        run = st.runs[rid]
        if action == "cancel":
            run["status"] = "cancelled"
            if rid in st.approvals:
                st.approvals[rid].set()
            return self.send_json(200, run)
        try:
            req = json.loads(self.body() or b"{}")
        except ValueError:
            return self.send_json(400, {"title": "Неверный JSON", "status": 400})
        return self.send_json(*st.decide(rid, req if isinstance(req, dict) else {}))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", type=Path, default=Path(__file__).resolve().parent / "app" / "dist" / "app" / "browser")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--speed", type=float, default=1.0, help="ускорение имитации")
    ap.add_argument("--seed", action="store_true", help="сразу создать демонстрационный прогон")
    ap.add_argument("--artifacts", type=Path, help="папка артефактов настоящего прогона (для просмотра файлов)")
    a = ap.parse_args()
    store = Store(a.speed, a.artifacts)
    if a.seed:
        store.create("Демо-прогон", "autopilot")
    handler = partial(type("H", (Handler,), {"store": store}), directory=str(a.dist))
    httpd = ThreadingHTTPServer(("127.0.0.1", a.port), handler)
    print(f"Консоль: http://127.0.0.1:{a.port}  (API {BASE}, статика {a.dist})  · остановить: Ctrl+C")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Остановлено.")
        httpd.server_close()


if __name__ == "__main__":
    main()
