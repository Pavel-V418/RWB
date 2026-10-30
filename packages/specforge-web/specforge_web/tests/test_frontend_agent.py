import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from specforge_web.agents.common.llm import ReplayLLM  # noqa: E402
from specforge_web.agents.frontend import agent as fe  # noqa: E402
from specforge_web.generators.angular.generate import SLOT_STUB_MARK  # noqa: E402
from specforge_web.tools import ts_check  # noqa: E402

FX = ROOT / "fixtures" / "synthetic-repair"
SLOT_FILE = "src/app/slots/workload-chart.slot.ts"

GOOD = {
    "modules": ["CardModule", "ProgressBarModule", "TagModule"],
    "helpers": ["itemsOf"],
    "angular": ["computed"],
    "models": ["Engineer"],
    "members": (
        "protected readonly rows = computed(() =>\n"
        "  [...itemsOf(this.data()['primary'] as Engineer[] | { items: Engineer[] } | null)]\n"
        "    .sort((a, b) => (b.openTickets ?? 0) - (a.openTickets ?? 0)),\n"
        ");\n"
        "protected readonly max = computed(() => Math.max(1, ...this.rows().map((r) => r.openTickets ?? 0)));"
    ),
    "template": (
        '<p-card header="Открытые заявки по инженерам" data-testid="workload-chart">\n'
        "  @if (rows().length === 0) { <span class=\"sf-muted\">Нет данных</span> }\n"
        "  <div class=\"sf-stack\">\n"
        "    @for (r of rows(); track r.id) {\n"
        "      <div class=\"sf-stack sf-stack--sm\">\n"
        "        <div class=\"sf-row sf-row--between\"><span>{{ r.name }}</span><p-tag [value]=\"'' + (r.openTickets ?? 0)\" severity=\"info\" /></div>\n"
        "        <p-progressbar [value]=\"((r.openTickets ?? 0) / max()) * 100\" [showValue]=\"false\" />\n"
        "      </div>\n"
        "    }\n"
        "  </div>\n"
        "</p-card>"
    ),
}


@pytest.fixture()
def artifacts(tmp_path):
    for d in ("01_requirements", "02_system", "03_ux"):
        shutil.copytree(FX / d, tmp_path / d)
    return tmp_path


def test_slot_filled_first_try(artifacts):
    llm = ReplayLLM(lambda m, t: GOOD)
    res = fe.run(artifacts, artifacts / "05_web", llm, log=lambda *_: None)
    assert res.passed, [e.message for e in res.errors if e.severity == "error"]
    slot = (artifacts / "05_web" / "admin" / SLOT_FILE).read_text(encoding="utf-8")
    assert SLOT_STUB_MARK not in slot and "import { Engineer } from '../generated/api/models';" in slot
    assert "import { ProgressBarModule } from 'primeng/progressbar';" in slot
    r = res.metrics["slots"][0]
    assert r["ok"] and r["attempts"] == 1
    assert 0.9 < res.metrics["deterministicShare"] < 1


def test_slot_repaired_after_lint_error(artifacts):
    bad = dict(GOOD, template=GOOD["template"].replace("<p-card", "<p-dropdown></p-dropdown><p-card"))
    answers = iter([bad, GOOD])
    llm = ReplayLLM(lambda m, t: next(answers))
    res = fe.run(artifacts, artifacts / "05_web", llm, log=lambda *_: None)
    assert res.passed
    assert res.metrics["slots"][0]["attempts"] == 2
    assert [c["tag"] for c in llm.calls] == ["fe.slot", "fe.repair"]


@pytest.mark.skipif(ts_check.find_tsc() is None, reason="tsc недоступен")
def test_slot_repaired_after_type_error(artifacts):
    bad = dict(GOOD, members=GOOD["members"].replace("r.openTickets ?? 0)));", "r.openTicketz ?? 0)));"))
    answers = iter([bad, GOOD])
    res = fe.run(artifacts, artifacts / "05_web", ReplayLLM(lambda m, t: next(answers)), log=lambda *_: None)
    assert res.passed and res.metrics["slots"][0]["attempts"] == 2


def test_unfixable_slot_rolls_back_to_stub(artifacts):
    bad = dict(GOOD, template="<button>сырой</button>")
    res = fe.run(artifacts, artifacts / "05_web", ReplayLLM(lambda m, t: bad), log=lambda *_: None)
    assert res.passed  # сборка не ломается
    assert any(e.code == "FE_SLOT_STUB" for e in res.errors)
    slot = (artifacts / "05_web" / "admin" / SLOT_FILE).read_text(encoding="utf-8")
    assert SLOT_STUB_MARK in slot


def test_case1_needs_no_llm(tmp_path):
    case = ROOT / "fixtures" / "case1"
    for d in ("01_requirements", "02_system", "03_ux"):
        shutil.copytree(case / d, tmp_path / d)
    llm = ReplayLLM(lambda m, t: pytest.fail("LLM не должна вызываться"))
    res = fe.run(tmp_path, tmp_path / "05_web", llm, log=lambda *_: None)
    assert res.passed and res.llm_calls == 0 and res.metrics["deterministicShare"] == 1.0
    assert (tmp_path / "05_web" / "admin" / "src" / "assets" / "mock-events.json").read_text(encoding="utf-8").count("mock-") == 9


def test_llm_client_falls_back_to_plain_requests_on_400():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from specforge_web.agents.common.llm import OpenAICompatLLM

    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(set(body))
            if "response_format" in body or "chat_template_kwargs" in body:
                self.send_response(400)
                self.end_headers()
                return
            out = {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(out).encode())

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        llm = OpenAICompatLLM(base_url=f"http://127.0.0.1:{srv.server_port}/v1", model="m")
        assert llm.complete([{"role": "user", "content": "x"}], json_schema={"type": "object"}, thinking=True).text == '{"ok": true}'
        assert llm.plain and "response_format" not in seen[-1]
    finally:
        srv.shutdown()


def _fake_build(project_has_slot_error):
    """Подмена npm_build: «сборка» падает, пока слот реализован LLM (или всегда — генераторная ошибка)."""
    calls = []

    def build(project, log=print):
        calls.append(1)
        slot = (project / SLOT_FILE).read_text(encoding="utf-8")
        if project_has_slot_error == "always" or SLOT_STUB_MARK not in slot:
            file = SLOT_FILE if project_has_slot_error == "blame" else None
            msg = "NG8002: Can't bind to 'valuez' since it isn't a known property of 'p-progressbar'."
            return [fe.GateError(message=msg, file=file, line=3, code="NG8002")], {"run build": 1.0}
        return [], {"run build": 1.0}
    return build, calls


@pytest.mark.parametrize("mode", ["blame", "anon"])
def test_build_failure_rolls_slot_back_and_stays_green(artifacts, monkeypatch, mode):
    build, calls = _fake_build(mode)
    monkeypatch.setattr(fe, "npm_build", build)
    res = fe.run(artifacts, artifacts / "05_web", ReplayLLM(lambda m, t: GOOD), build=True, log=lambda *_: None)
    assert res.passed, [e.message for e in res.errors if e.severity == "error"]
    assert len(calls) == 2 and res.metrics["build"] == "ok"
    assert SLOT_STUB_MARK in (artifacts / "05_web" / "admin" / SLOT_FILE).read_text(encoding="utf-8")
    assert not (artifacts / "05_web" / "admin" / SLOT_FILE.replace(".ts", ".html")).exists()
    slot = res.metrics["slots"][0]
    assert slot["rolledBack"] and not slot["ok"] and "_stub" not in slot
    assert any(e.code == "FE_SLOT_ROLLBACK" and e.severity == "warning" for e in res.errors)
    assert res.metrics["deterministicShare"] == 1.0


def test_build_failure_not_caused_by_slots_stays_red(artifacts, monkeypatch):
    build, calls = _fake_build("always")
    monkeypatch.setattr(fe, "npm_build", build)
    res = fe.run(artifacts, artifacts / "05_web", ReplayLLM(lambda m, t: GOOD), build=True, log=lambda *_: None)
    assert not res.passed and res.metrics["build"] == "failed"
    assert len(calls) == 2  # одна пересборка после отката всех слотов, дальше честная ошибка
