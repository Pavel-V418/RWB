import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from specforge_web.agents.common.llm import ReplayLLM  # noqa: E402
from specforge_web.agents.vlm_reviewer import review  # noqa: E402

pytest.importorskip("playwright.sync_api")

APP = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{font-family:sans-serif;margin:0} .wide{width:900px;height:20px;background:#ccc}</style></head><body><div id="root"></div>
<script>
const screens = { '/things': 'thing-list', '/': 'overview' };
const id = screens[location.pathname] || 'unknown';
const root = document.getElementById('root');
const page = document.createElement('div'); page.setAttribute('data-testid', id); root.appendChild(page);
const state = (s, text) => { page.innerHTML = `<div data-testid="${id}-state-${s}">${text}</div>`; };
state('loading', 'Загрузка…');
fetch('/api/v1/things').then(async (r) => {
  if (r.status === 403) return state('forbidden', 'Нет доступа');
  if (!r.ok) return state('error', 'Ошибка');
  const d = await r.json();
  if (!d.items.length) return state('empty', 'Пусто');
  page.innerHTML = '<h1>Данные</h1>' + d.items.map((x) => `<div>${x.name}</div>`).join('') +
    (id === 'overview' ? '<div class="wide"></div><button></button>' : '');
});
</script></body></html>"""


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/api/v1/things"):
            body = json.dumps({"items": [{"id": "1", "name": "Первая"}], "total": 1}).encode()
            ctype = "application/json"
        else:
            body, ctype = APP.encode(), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


SPEC = {
    "specVersion": "1.0",
    "app": {"id": "t", "platform": "web", "title": "Тест", "uiKit": "primeng@20"},
    "roles": [{"id": "admin", "title": "A"}],
    "navigation": [],
    "screens": [
        {"id": "overview", "title": "Обзор", "archetype": "dashboard", "route": "/", "roles": ["admin"], "requirements": [],
         "states": {"empty": {"title": "Пусто"}, "error": {"title": "Ошибка"}, "forbidden": {"title": "Нет доступа"}}},
        {"id": "thing-list", "title": "Вещи", "archetype": "list", "route": "/things", "roles": ["admin"], "requirements": [],
         "list": {"columns": [{"field": "name", "label": "Название"}]},
         "states": {"empty": {"title": "Пусто"}, "error": {"title": "Ошибка"}, "forbidden": {"title": "Нет доступа"}}},
        {"id": "thing-detail", "title": "Вещь", "archetype": "detail", "route": "/things/:id", "roles": ["admin"], "requirements": [], "states": {}},
    ],
}


@pytest.fixture()
def artifacts(tmp_path):
    (tmp_path / "03_ux").mkdir()
    (tmp_path / "03_ux" / "screen-spec.web.json").write_text(json.dumps(SPEC, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_dom_level_finds_states_and_layout_defects(server, artifacts):
    res = review.run(artifacts, artifacts / "ui", None, base_url=server, log=lambda *_: None)
    shots = json.loads((artifacts / "ui" / "screenshots" / "shots.json").read_text(encoding="utf-8"))
    assert len(shots) == 2 * 2 * 5  # 2 экрана × 2 вьюпорта × 5 состояний; детальный экран пропущен
    assert all(Path(s["path"]).exists() for s in shots)
    # каждое форсированное состояние реально отрисовано
    assert all(s["dom"]["stateFound"] for s in shots), [s for s in shots if not s["dom"]["stateFound"]]
    defects = json.loads((artifacts / "ui" / "defects.json").read_text(encoding="utf-8"))
    overflow = [d for d in defects if "прокрутка" in d["issue"]]
    assert overflow and all(d["screen"] == "overview" for d in overflow)
    assert any(d["severity"] == "major" and d["viewport"] == "mobile" for d in overflow)
    assert any("без доступного имени" in d["issue"] for d in defects)
    assert not res.passed  # major на мобильном блокирует шлюз
    assert (artifacts / "ui" / "ui-review.md").read_text(encoding="utf-8").startswith("# Отчёт UI-ревью")


def test_vlm_level_on_key_shots_only(server, artifacts):
    seen = []

    def respond(messages, tag):
        content = messages[-1]["content"]
        assert content[1]["image_url"]["url"].startswith("data:image/")
        seen.append(content[0]["text"])
        if "Обзор" in content[0]["text"] and "desktop" in content[0]["text"] and "ready" in content[0]["text"]:
            return {"defects": [{"severity": "minor", "element": "заголовок", "issue": "мелкий отступ", "fix": "добавить отступ"}]}
        return {"defects": []}

    llm = ReplayLLM(respond)
    res = review.run(artifacts, artifacts / "ui", llm, base_url=server, log=lambda *_: None)
    assert len(seen) == 2 * 3  # 2 экрана × (ready desktop, ready mobile, empty desktop)
    defects = json.loads((artifacts / "ui" / "defects.json").read_text(encoding="utf-8"))
    assert any(d["source"] == "vlm" and d["issue"] == "мелкий отступ" for d in defects)
    assert res.llm_calls == 6


def test_external_screenshots_android(tmp_path, artifacts):
    from PIL import Image
    shots = tmp_path / "android"
    shots.mkdir()
    Image.new("RGB", (390, 844), "white").save(shots / "overview--ready--mobile.png")
    spec = dict(SPEC, app=dict(SPEC["app"], platform="android", uiKit="material3"))
    (artifacts / "03_ux" / "screen-spec.android.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    llm = ReplayLLM(lambda m, t: {"defects": [{"severity": "critical", "element": "экран", "issue": "белый экран", "fix": "проверить загрузку"}]})
    res = review.run(artifacts, artifacts / "ui-android", llm, screenshots=shots, platform="android", log=lambda *_: None)
    assert not res.passed and res.metrics["defects"]["critical"] == 1


def test_autofix_hides_columns_on_mobile_overflow():
    from specforge_web.agents.vlm_reviewer.autofix import patch_spec, slot_feedback
    spec = json.loads((ROOT / "fixtures" / "case1" / "03_ux" / "screen-spec.web.json").read_text(encoding="utf-8"))
    for c in next(s for s in spec["screens"] if s["id"] == "zone-list")["list"]["columns"]:
        c.pop("hideBelow", None)
    new, applied = patch_spec(spec, [{"screen": "zone-list", "viewport": "mobile", "issue": "горизонтальная прокрутка страницы"}])
    cols = next(s for s in new["screens"] if s["id"] == "zone-list")["list"]["columns"]
    assert applied and not cols[0].get("hideBelow") and cols[2]["hideBelow"] == "md" and cols[-1]["hideBelow"] == "lg"
    fb = slot_feedback([{"source": "vlm", "screen": "w", "severity": "major", "viewport": "desktop", "state": "ready",
                         "element": "x", "issue": "обрезан текст", "fix": "перенос"}], [{"screen": "w"}])
    assert fb == {"w": ["[desktop/ready] x: обрезан текст → перенос"]}


def test_ui_pipeline_offline_without_build(tmp_path):
    import shutil
    from specforge_web.agents import ui_pipeline
    for d in ("01_requirements", "02_system"):
        shutil.copytree(ROOT / "fixtures" / "synthetic-repair" / d, tmp_path / d)
    rep = ui_pipeline.run(tmp_path, None, build=False, do_review=False, log=lambda *_: None)
    assert rep["passed"] and [s["stage"] for s in rep["stages"]] == ["03_ux", "05_web#1"]
    assert (tmp_path / "05_web" / "admin" / "src" / "app" / "generated" / "routes.ts").exists()


def test_states_only_for_screens_that_load_data():
    from specforge_web.agents.vlm_reviewer.capture import screen_states
    assert screen_states({"archetype": "login"}) == ("ready",)
    assert screen_states({"archetype": "forbidden"}) == ("ready",)
    # форма создания: вместо состояний загрузки — альтернативные сценарии отправки
    assert screen_states({"archetype": "form", "form": {"submit": {}}}) == ("ready", "invalid", "submit-error")
    assert len(screen_states({"archetype": "form", "form": {"load": {"operationId": "x"}}})) == 5
    assert len(screen_states({"archetype": "list"})) == 5


def test_console_noise_from_stand_is_not_a_defect():
    from specforge_web.agents.vlm_reviewer.capture import console_error_ignored as ign
    forced500 = "Failed to load resource: the server responded with a status of 500 (Internal Server Error)"
    assert ign(forced500, "http://x/api/v1/things", "error")
    assert not ign(forced500, "http://x/api/v1/things", "ready")  # в «данных» 500 — настоящий дефект
    assert ign("Failed to load resource: the server responded with a status of 405 ()", "http://x/hubs/events/negotiate", "ready")
    assert ign("Error: Failed to complete negotiation with the server", "", "ready")
    assert not ign("ERROR TypeError: Cannot read properties of undefined", "http://x/main.js", "ready")


FORM_APP = """<!doctype html><html lang="ru"><head><meta charset="utf-8"></head><body>
<div data-testid="thing-create"><form id="f" novalidate>
<input data-testid="thing-create-field-code"><div id="err"></div>
<button type="submit" data-testid="thing-create-submit">Сохранить</button><div id="ferr"></div></form></div>
<script>
const GOOD = %s;
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const v = document.querySelector('[data-testid="thing-create-field-code"]').value;
  const err = document.getElementById('err');
  err.innerHTML = v ? '' : '<small data-testid="thing-create-error-code">Обязательное поле</small>';
  if (!v) return;
  const r = await fetch('/api/v1/things', { method: 'POST', body: JSON.stringify({ code: v }) });
  if (!r.ok && GOOD) document.getElementById('ferr').innerHTML = '<p data-testid="thing-create-form-error">Не удалось сохранить</p>';
});
</script></body></html>"""


def _serve(html: str):
    class H(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.send_response(201)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.mark.parametrize("good", [True, False])
def test_form_alternative_scenarios(tmp_path, good):
    """Пустая отправка → ошибки полей; ошибка сервера → сообщение формы. Плохая форма (молчит при 500) ловится."""
    from specforge_web.agents.vlm_reviewer.capture import capture
    spec = {"app": {"platform": "web"}, "screens": [{
        "id": "thing-create", "title": "Новая вещь", "archetype": "form", "route": "/things/new", "roles": ["admin"], "public": True,
        "requirements": [], "states": {},
        "form": {"fields": [{"name": "code", "label": "Код", "control": "text", "required": True, "validation": {"pattern": "^[A-Z]{2}[0-9]{3}$"}}],
                 "submit": {"operationId": "createThing", "label": "Сохранить"}}}]}
    httpd = _serve(FORM_APP % ("true" if good else "false"))
    try:
        shots = capture(f"http://127.0.0.1:{httpd.server_address[1]}", spec, tmp_path, viewports=("desktop",), log=lambda *_: None)
    finally:
        httpd.shutdown()
    by = {s.state: s.dom["stateFound"] for s in shots}
    assert set(by) == {"ready", "invalid", "submit-error"}
    assert by["invalid"] is True
    assert by["submit-error"] is good


def test_axe_runs_under_csp_and_becomes_defects(tmp_path):
    """axe внедряется через протокол браузера: CSP сайта (script-src 'self') его не блокирует; critical → major."""
    from specforge_web.agents.vlm_reviewer.capture import capture
    from specforge_web.agents.vlm_reviewer.review import dom_defects
    stub = tmp_path / "axe.min.js"
    stub.write_text("window.axe = { run: async () => ({ violations: ["
                    "{ id: 'button-name', impact: 'critical', help: 'Buttons must have discernible text', nodes: [{ target: ['#b'] }] },"
                    "{ id: 'color-contrast', impact: 'serious', help: 'Contrast', nodes: [{ target: ['p'] }, { target: ['span'] }] }] }) };",
                    encoding="utf-8")
    html = '<!doctype html><html lang="ru"><body><div data-testid="home"><button id="b"></button></div></body></html>'

    class H(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    spec = {"app": {"platform": "web"}, "screens": [{"id": "home", "title": "Главная", "archetype": "dashboard", "route": "/",
                                                       "roles": ["admin"], "public": True, "requirements": [], "states": {}}]}
    try:
        shots = capture(f"http://127.0.0.1:{httpd.server_address[1]}", spec, tmp_path / "s", states=("ready",),
                        viewports=("desktop",), log=lambda *_: None, axe_js=stub)
    finally:
        httpd.shutdown()
    a11y = shots[0].dom.get("a11y")
    assert a11y and {v["id"] for v in a11y} == {"button-name", "color-contrast"}, shots[0].dom
    defects = [d for d in dom_defects(shots[0]) if d.get("source") == "axe"]
    sev = {d["issue"].split("(")[1].split(",")[0]: d["severity"] for d in defects}
    assert sev == {"button-name": "major", "color-contrast": "minor"}


def test_axe_missing_is_reported_not_silent(tmp_path, monkeypatch):
    from specforge_web.agents.vlm_reviewer.capture import find_axe
    monkeypatch.delenv("SPECFORGE_AXE_JS", raising=False)
    assert find_axe(tmp_path) is None
    (tmp_path / "admin" / "node_modules" / "axe-core").mkdir(parents=True)
    (tmp_path / "admin" / "node_modules" / "axe-core" / "axe.min.js").write_text("//", encoding="utf-8")
    assert find_axe(tmp_path) == tmp_path / "admin" / "node_modules" / "axe-core" / "axe.min.js"
