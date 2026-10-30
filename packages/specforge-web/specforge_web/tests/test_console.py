import json
import sys
import threading
import time
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from specforge_web.console import demo_server  # noqa: E402
from specforge_web.console.build import build  # noqa: E402
from specforge_web.tools import kit_lint, template_check, ts_check  # noqa: E402


def test_console_builds_clean(tmp_path):
    s = build(tmp_path)
    assert s["slotsImplemented"] == s["slotsTotal"] == 4
    api = (tmp_path / "src/app/generated/api/api.service.ts").read_text(encoding="utf-8")
    assert "responseType: 'blob' as const" in api  # содержимое артефакта — не JSON
    assert not [f for f in kit_lint.lint_dir(tmp_path / "src") if f.severity == "error"]
    assert not template_check.check_dir(tmp_path / "src")
    if ts_check.find_tsc():
        assert not ts_check.run(tmp_path)
    env = (tmp_path / "src" / "environments" / "environment.ts").read_text(encoding="utf-8")
    assert "/console-api/v1" in env and "authRequired: false" in (tmp_path / "src/app/generated/app-meta.ts").read_text(encoding="utf-8")
    # в контейнере консоль ходит к оркестратору через свой nginx: /console-api → ORCHESTRATOR_URL, SSE без буфера
    conf = (tmp_path / "docker" / "nginx.conf.template").read_text(encoding="utf-8")
    loc = conf.split("location /console-api/ {", 1)[1].split("\n  }", 1)[0]
    assert "proxy_pass ${ORCHESTRATOR_URL};" in loc and "proxy_buffering off;" in loc and "client_max_body_size" in loc
    assert conf.count("location /console-api/") == 1
    assert "ORCHESTRATOR_URL=" in (tmp_path / "Dockerfile").read_text(encoding="utf-8")


def test_stage_levels():
    from importlib import util  # слот — TS, проверяем алгоритм на Python-эквиваленте через данные демо
    deps = {i: d for i, _, d, _, _ in demo_server.STAGES}
    depth = {}

    def lvl(s):
        if s not in depth:
            depth[s] = 0 if not deps[s] else 1 + max(lvl(d) for d in deps[s])
        return depth[s]
    assert [lvl(s) for s in deps] == [0, 1, 2, 2, 3, 3, 4, 5, 6]


@pytest.fixture()
def server(tmp_path):
    art = tmp_path / "art"
    (art / "01_requirements").mkdir(parents=True)
    (art / "01_requirements" / "requirements.json").write_text('{"product": "Демо"}', encoding="utf-8")
    store = demo_server.Store(speed=60, artifacts_dir=art)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(type("H", (demo_server.Handler,), {"store": store}), directory=str(tmp_path)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}{demo_server.BASE}"
    httpd.shutdown()


def call(base, method, path, body=None):
    r = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=5) as x:
            return x.status, json.loads(x.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_demo_run_with_approval(server):
    assert call(server, "POST", "/runs", {"mode": "autopilot", "text": "мало"})[0] == 400
    code, run = call(server, "POST", "/runs", {"mode": "approve", "text": "Достаточно подробные бизнес-требования к системе"})
    assert code == 201
    for _ in range(50):
        if call(server, "GET", f"/runs/{run['id']}")[1]["status"] == "awaiting_approval":
            break
        time.sleep(0.1)
    st = next(s for s in call(server, "GET", f"/runs/{run['id']}")[1]["stages"] if s["id"] == "01_requirements")
    answers = {q["id"]: "Да" for q in st["questions"] if q.get("blocking")}
    assert call(server, "POST", f"/runs/{run['id']}/approve", {"stageId": "01_requirements", "decision": "approve", "answers": answers})[0] == 200
    for _ in range(100):
        r = call(server, "GET", f"/runs/{run['id']}")[1]
        if r["status"] == "passed":
            break
        time.sleep(0.1)
    assert r["status"] == "passed" and all(s["status"] == "passed" for s in r["stages"])
    m = call(server, "GET", f"/runs/{run['id']}/metrics")[1]
    assert m["totals"]["tokensIn"] > 0 and len(m["stages"]) == 9
    sse = urllib.request.urlopen(server + f"/runs/{run['id']}/events", timeout=5).read().decode()
    assert "event: run.finished" in sse and "event: stage.repair" in sse


def test_demo_run_is_marked_and_serves_real_artifacts(server):
    code, run = call(server, "POST", "/runs", {"mode": "autopilot", "text": "Достаточно подробные бизнес-требования к системе"})
    assert code == 201 and run["simulated"] is True
    for _ in range(100):
        if call(server, "GET", f"/runs/{run['id']}")[1]["status"] == "passed":
            break
        time.sleep(0.1)
    arts = call(server, "GET", f"/runs/{run['id']}/artifacts")[1]
    req = next(a for a in arts if a["path"] == "01_requirements/requirements.json")
    assert req["size"] > 0 and req["kind"] == "json"
    with urllib.request.urlopen(server + f"/runs/{run['id']}/artifacts/content?path=01_requirements/requirements.json", timeout=5) as r:
        assert r.headers["Content-Type"].startswith("text/plain") and "Демо" in r.read().decode()
    for bad in ("../secret.txt", "01_requirements/../../x", "02_system/openapi.yaml"):
        try:
            urllib.request.urlopen(server + f"/runs/{run['id']}/artifacts/content?path={bad}", timeout=5)
            raise AssertionError(bad)
        except urllib.error.HTTPError as e:
            assert e.code == 404


def wait_status(base, rid, status, n=100):
    for _ in range(n):
        r = call(base, "GET", f"/runs/{rid}")[1]
        if r["status"] == status:
            return r
        time.sleep(0.05)
    raise AssertionError(f"нет статуса {status}: {r['status']}")


def stage(run, sid="01_requirements"):
    return next(s for s in run["stages"] if s["id"] == sid)


def test_reject_needs_comment_and_really_reruns_stage(server):
    code, run = call(server, "POST", "/runs", {"mode": "approve", "text": "Достаточно подробные бизнес-требования к системе"})
    r = wait_status(server, run["id"], "awaiting_approval")
    st = stage(r)
    assert st["attempts"] == 1 and st["summary"] and st["artifacts"]
    # без комментария агенту нечего исправлять
    code, body = call(server, "POST", f"/runs/{run['id']}/approve", {"stageId": "01_requirements", "decision": "reject", "comment": "  "})
    assert code == 422 and "comment" in body["errors"]
    code, _ = call(server, "POST", f"/runs/{run['id']}/approve", {"stageId": "01_requirements", "decision": "reject", "comment": "Уточнить роли"})
    assert code == 200
    # повторное решение, пока этап перезапускается, — 409
    assert call(server, "POST", f"/runs/{run['id']}/approve", {"stageId": "01_requirements", "decision": "reject", "comment": "ещё"})[0] == 409
    r = wait_status(server, run["id"], "awaiting_approval")
    st = stage(r)
    assert st["attempts"] == 2 and st["status"] == "awaiting_approval"
    assert "Уточнить роли" in st["summary"]
    assert all(s["status"] == "pending" for s in r["stages"] if s["id"] != "01_requirements")  # дальше не пошёл


def test_approve_blocked_until_blocking_questions_answered(server):
    code, run = call(server, "POST", "/runs", {"mode": "approve", "text": "Достаточно подробные бизнес-требования к системе"})
    st = stage(wait_status(server, run["id"], "awaiting_approval"))
    qs = st["questions"]
    blocking = [q["id"] for q in qs if q.get("blocking")]
    assert qs and blocking and all(q["text"] for q in qs)
    code, body = call(server, "POST", f"/runs/{run['id']}/approve", {"stageId": "01_requirements", "decision": "approve"})
    assert code == 409 and set(body["errors"]["answers"]) == set(blocking)
    assert call(server, "POST", f"/runs/{run['id']}/approve", {"stageId": "02_system", "decision": "approve"})[0] == 409
    answers = {q: "Да, все" for q in blocking}
    code, _ = call(server, "POST", f"/runs/{run['id']}/approve", {"stageId": "01_requirements", "decision": "approve", "answers": answers})
    assert code == 200
    r = wait_status(server, run["id"], "passed")
    assert all(q.get("answer") == "Да, все" for q in stage(r)["questions"] if q["id"] in blocking)
    assert r["appUrl"]


def test_questions_come_from_requirements(tmp_path):
    art = tmp_path / "art"
    (art / "01_requirements").mkdir(parents=True)
    (art / "01_requirements" / "requirements.json").write_text(json.dumps({
        "functional": [{"id": "FR-01", "title": "x"}],
        "openQuestions": [{"id": "Q1", "question": "Кто утверждает?", "assumption": "Руководитель", "blocking": True}, "Нужен ли экспорт?"],
        "assumptions": ["Работа только в рабочие часы"],
    }, ensure_ascii=False), encoding="utf-8")
    store = demo_server.Store(speed=60, artifacts_dir=art)
    qs = store.questions()
    assert qs[0] == {"id": "Q1", "text": "Кто утверждает?", "blocking": True, "assumption": "Руководитель"}
    assert qs[1]["text"] == "Нужен ли экспорт?" and not qs[1]["blocking"]
    assert qs[2]["assumption"] == "Работа только в рабочие часы" and not qs[2]["blocking"]
    assert "1" in store.summary({"title": "Анализ"}, [])
    # без файла — общие вопросы, не про предметную область
    assert demo_server.Store(speed=60).questions() == demo_server.GENERIC_QUESTIONS


def test_console_graph_slot_has_approval_ui():
    html = (ROOT / "console" / "slots" / "run-detail-pipeline-graph.slot.html").read_text(encoding="utf-8")
    ts = (ROOT / "console" / "slots" / "run-detail-pipeline-graph.slot.ts").read_text(encoding="utf-8")
    for tid in ("run-graph-comment", "run-graph-questions", "run-graph-files", "run-graph-open-app", "run-graph-summary"):
        assert f'"{tid}' in html or f"'{tid}" in html, tid
    assert "pTextarea" in html and "unanswered(st).length > 0" in html
    assert "comment" in ts and "answers" in ts and "rejectTried" in ts
