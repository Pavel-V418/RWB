import copy
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from specforge_web.agents.common.llm import ReplayLLM, extract_json, guided_schema  # noqa: E402
from specforge_web.agents.ux_designer import agent as ux  # noqa: E402

FX = ROOT / "fixtures" / "case1"
REF = json.loads((FX / "03_ux" / "screen-spec.web.json").read_text(encoding="utf-8"))


@pytest.fixture()
def artifacts(tmp_path):
    for d in ("01_requirements", "02_system"):
        shutil.copytree(FX / d, tmp_path / d)
    return tmp_path


def ref_plan():
    nav = {n["screen"]: n for n in REF["navigation"]}
    out = []
    for s in REF["screens"]:
        if s["archetype"] in ("login", "forbidden"):
            continue
        prim = (s.get("data") or {}).get("primary", {}).get("operationId") or (s.get("form") or {}).get("submit", {}).get("operationId")
        out.append({"id": s["id"], "title": s["title"], "archetype": s["archetype"], "route": s["route"], "roles": s["roles"],
                    "requirements": s["requirements"], "primaryOperationId": prim, "inNavigation": s["id"] in nav,
                    "navLabel": nav.get(s["id"], {}).get("label", s["title"]), "navIcon": nav.get(s["id"], {}).get("icon", "pi pi-list")})
    return {"appTitle": REF["app"]["title"], "screens": out, "assumptions": ["демо"]}


def screen_from_msg(messages):
    m = re.search(r"## Экран\n(\{.*?\})\n\n## Все экраны", messages[-1]["content"], re.S)
    return json.loads(m.group(1))["id"]


def ref_responder(break_once: set | None = None):
    broken = set()

    def respond(messages, tag):
        if tag.startswith("ux.plan"):
            return ref_plan()
        if tag == "ux.repair":
            sid = json.loads(messages[-1]["content"].split("## Экран\n", 1)[1])["id"]
            return next(s for s in REF["screens"] if s["id"] == sid)
        sid = screen_from_msg(messages)
        s = copy.deepcopy(next(x for x in REF["screens"] if x["id"] == sid))
        if break_once and sid in break_once and sid not in broken:
            broken.add(sid)
            s["list"]["columns"][0]["field"] = "plate"  # поле, которого нет в API
        return s
    return respond


def test_happy_path(artifacts):
    llm = ReplayLLM(ref_responder())
    res = ux.run(artifacts, artifacts / "03_ux", llm, platforms=["web"], log=lambda *_: None)
    assert res.passed, [e.message for e in res.errors]
    spec = json.loads((artifacts / "03_ux" / "screen-spec.web.json").read_text(encoding="utf-8"))
    assert {s["id"] for s in spec["screens"]} == {s["id"] for s in REF["screens"]}
    assert res.metrics["web"]["mode"] == "llm"
    plan_calls = [c for c in llm.calls if c["tag"].startswith("ux.plan")]
    assert len(plan_calls) == 1 and plan_calls[0]["thinking"] is True
    assert all(not c["thinking"] for c in llm.calls if c["tag"].startswith("ux.screen"))
    assert res.llm_calls == 1 + 11  # план + 11 рабочих экранов (вход и «нет доступа» — без LLM)


def test_repair_fixes_broken_screen(artifacts):
    llm = ReplayLLM(ref_responder(break_once={"vehicle-list"}))
    res = ux.run(artifacts, artifacts / "03_ux", llm, platforms=["web"], log=lambda *_: None)
    assert res.passed
    assert res.metrics["web"]["repairRounds"] == 1
    assert sum(1 for c in llm.calls if c["tag"] == "ux.repair") == 1
    assert res.metrics["web"]["fallbackScreens"] == []


def test_garbage_llm_falls_back_to_baseline(artifacts):
    llm = ReplayLLM(lambda m, t: "извините, не могу")
    res = ux.run(artifacts, artifacts / "03_ux", llm, platforms=["web", "android"], log=lambda *_: None)
    assert res.passed
    assert res.metrics["web"]["mode"] == "baseline-fallback"
    assert (artifacts / "03_ux" / "screen-spec.android.json").exists()


def test_persistently_broken_screen_replaced_by_baseline(artifacts):
    def respond(messages, tag):
        r = ref_responder()(messages, tag)
        if tag != "ux.plan.web" and r.get("id") == "zone-list":
            r = copy.deepcopy(r)
            r["data"]["primary"]["operationId"] = "listZonesNope"
        return r
    res = ux.run(artifacts, artifacts / "03_ux", ReplayLLM(respond), platforms=["web"], log=lambda *_: None)
    assert res.passed
    assert "zone-list" in res.metrics["web"]["fallbackScreens"]


def test_offline_mode(artifacts):
    res = ux.run(artifacts, artifacts / "03_ux", None, platforms=["web"], log=lambda *_: None)
    assert res.passed and res.llm_calls == 0


def test_platforms_from_channels(artifacts):
    res = ux.run(artifacts, artifacts / "03_ux", None, log=lambda *_: None)
    assert set(res.metrics) == {"web", "android"}


def test_extract_json_variants():
    assert extract_json('<think>хм</think>```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Вот ответ: {"a": {"b": "}"}} спасибо') == {"a": {"b": "}"}}


def test_guided_schema_strips_conditionals():
    g = guided_schema(ux.SCREEN_SCHEMA)
    assert '"if":' not in json.dumps(g)
    assert "$defs" in g


def test_glossary_from_plan_makes_fallback_screens_russian(tmp_path):
    """Модель дала план с глоссарием, но экраны не сгенерировала → экраны baseline, но с русскими подписями."""
    fx = ROOT / "fixtures" / "synthetic-booking"
    for d in ("01_requirements", "02_system"):
        shutil.copytree(fx / d, tmp_path / d)
    asked = {}

    def respond(messages, tag):
        if tag.startswith("ux.plan"):
            text = messages[-1]["content"]
            asked["terms"] = text.split("## Термины без подписи (заполни glossary)\n", 1)[1].split("\n")[0] if "Термины без подписи" in text else ""
            return {"appTitle": "Бронирование", "screens": [], "glossary": [
                {"term": "bookings", "label": "Бронирования"}, {"term": "Booking", "label": "Бронирование"},
                {"term": "equipment", "label": "Техника"}, {"term": "equipmentName", "label": "Техника"},
                {"term": "vehicle", "label": "Транспорт"}, {"term": "bad", "label": "no cyrillic"}]}
        raise RuntimeError("модель не ответила")

    res = ux.run(tmp_path, tmp_path / "03_ux", ReplayLLM(respond), platforms=["web"], log=lambda *_: None)
    assert res.passed
    assert "bookings" in asked["terms"] and "equipmentName" in asked["terms"]
    spec = json.loads((tmp_path / "03_ux" / "screen-spec.web.json").read_text(encoding="utf-8"))
    by = {s["id"]: s for s in spec["screens"]}
    assert by["bookings-list"]["title"] == "Бронирования"
    assert by["bookings-list"]["list"]["columns"][0]["label"] == "Техника"
    cat = next(c for c in by["equipment-list"]["list"]["columns"] if c["field"] == "category")
    assert cat["statusMap"]["vehicle"]["label"] == "Транспорт" and cat["statusMap"]["other"]["label"] == "Другое"


def test_baseline_reads_russian_annotations_from_openapi():
    from specforge_web.agents.ux_designer.baseline import baseline_spec
    api = {
        "openapi": "3.1.0", "info": {"title": "t", "version": "1"},
        "tags": [{"name": "Things", "description": "Вещи"}],
        "paths": {"/things": {"get": {"operationId": "listThings", "tags": ["Things"], "responses": {"200": {"description": "OK", "content": {
            "application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/Thing"}}}}}}}},
                  "/things/{id}/archive": {"post": {"operationId": "archiveThing", "summary": "Убрать в архив",
                                                     "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                                                     "responses": {"200": {"description": "OK"}}}}},
        "components": {"schemas": {
            "Thing": {"type": "object", "title": "Вещь", "properties": {
                "id": {"type": "string"}, "serial": {"type": "string", "title": "Заводской номер"},
                "state": {"$ref": "#/components/schemas/State"}, "weightKg": {"type": "number", "description": "Масса, кг"}}},
            "State": {"type": "string", "enum": ["fresh", "used"], "x-enum-labels": {"fresh": "Новая", "used": "Б/у"}}}},
    }
    spec = baseline_spec(api, {"product": "Склад", "functional": []})
    lst = next(s for s in spec["screens"] if s["id"] == "things-list")
    assert lst["title"] == "Вещи"
    labels = {c["field"]: c for c in lst["list"]["columns"]}
    assert labels["serial"]["label"] == "Заводской номер" and labels["weightKg"]["label"] == "Масса, кг"
    assert labels["state"]["statusMap"]["used"]["label"] == "Б/у"
