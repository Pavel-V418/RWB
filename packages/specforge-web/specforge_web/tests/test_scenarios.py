"""E2E-сценарии пользователя из спеки экранов и тестовые значения полей."""
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

from specforge_web.agents.ux_designer.baseline import baseline_spec  # noqa: E402
from specforge_web.generators.angular.generate import generate  # noqa: E402
from specforge_web.generators.angular.openapi_ts import TsGen  # noqa: E402
from specforge_web.generators.angular.samples import field_plan, from_pattern  # noqa: E402
from specforge_web.generators.angular.scenarios import _reqs, build, scenario_trace  # noqa: E402
from specforge_web.tools import ts_check  # noqa: E402


def taxi():
    d = ROOT / "fixtures" / "synthetic-taxi"
    req = json.loads((d / "01_requirements" / "requirements.json").read_text(encoding="utf-8"))
    api = yaml.safe_load((d / "02_system" / "openapi.yaml").read_text(encoding="utf-8"))
    return baseline_spec(api, req, "web"), api


def test_pattern_samples_match_their_pattern():
    for p in [r"^\+7[0-9]{10}$", r"^[A-Z]{2}\d{3}$", r"^(\+7|8)\d{10}$", r"^[^@]+@[^@]+$", r"^Z-[0-9]{5}$", r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$"]:
        v = from_pattern(p)
        assert v is not None and re.search(p, v), p
    assert from_pattern(r"^(?<=x)a$") is None  # вне подмножества — честно «нет значения»


def test_field_plan_respects_limits_and_skips_honestly():
    assert field_plan({"name": "qty", "control": "number", "validation": {"min": 5, "max": 9}})["value"] == 5
    p = field_plan({"name": "code", "control": "text", "required": True, "validation": {"pattern": r"^(?<=a)b$"}})
    assert "skip" in p
    assert field_plan({"name": "note", "control": "textarea", "validation": {"maxLength": 8}})["maxLength"] == 8


def test_taxi_scenarios_cover_create_actions_and_access():
    spec, api = taxi()
    cfg = build(spec, TsGen(api))
    form = next(f for f in cfg["forms"] if f["id"] == "orders-create")
    assert form["method"] == "POST" and form["list"]["id"] == "orders-list" and form["list"]["search"]
    phone = next(f for f in form["fields"] if f["name"] == "passengerPhone")
    assert re.fullmatch(r"\+7[0-9]{10}", phone["value"])
    assert "passengerPhone" in form["patternFields"]
    acts = {a["id"] for d in cfg["actions"] for a in d["actions"]}
    assert {"assign", "cancel", "complete"} <= acts
    assert cfg["guards"] and all(g["denied"] == "driver" for g in cfg["guards"])
    assert cfg["api"]["loginPath"] == "/auth/login"


def test_generated_e2e_has_no_sparse_arrays_and_typechecks(tmp_path):
    spec, api = taxi()
    generate(spec, api, tmp_path)
    for f in ("screens.spec.ts", "scenarios.spec.ts"):
        text = (tmp_path / "e2e" / f).read_text(encoding="utf-8")
        assert not re.search(r"\[\s*,", text), f  # «[ , ]» — дырка в массиве ломает весь файл тестов
    assert (tmp_path / "e2e" / "support.ts").exists()
    if ts_check.find_tsc():
        assert not ts_check.run(tmp_path)


def test_requirements_of_screen_include_slots_without_duplicates():
    sc = {"requirements": ["FR-01", "FR-02"], "slots": [{"requirements": ["FR-02", "FR-07"]}, {}]}
    assert _reqs(sc) == ["FR-01", "FR-02", "FR-07"]
    assert _reqs({}) == []


def test_scenarios_carry_requirements_and_trace_by_kind():
    spec, api = taxi()
    cfg = build(spec, TsGen(api))
    form = next(f for f in cfg["forms"] if f["id"] == "orders-create")
    assert "FR-01" in form["requirements"]
    assert all("requirements" in g for g in cfg["guards"])
    acts = {a["id"]: a for d in cfg["actions"] for a in d["actions"]}
    # действие с глаголом требования закрепляет его за собой, а не все требования экрана
    assert acts["assign"]["requirements"] == ["FR-03"] and acts["cancel"]["requirements"] == ["FR-05"]
    assert acts["complete"]["requirements"] == []
    tr = scenario_trace(cfg)
    kinds = {x["kind"] for x in tr["FR-01"]}
    assert kinds == {"main", "alternative"}
    assert all(x["screen"] == "orders-create" for x in tr["FR-01"])
    assert [x["kind"] for x in tr["FR-03"]] == ["main"] and "Назначить" in tr["FR-03"][0]["case"]
    assert not any("Назначить" in x["case"] for x in tr.get("FR-05", []))
    assert any(x["kind"] == "access" for x in tr["FR-02"])


def test_action_without_own_requirements_falls_back_to_screen():
    spec, api = taxi()
    for s in spec["screens"]:
        for a in s.get("actions", []) or []:
            a.pop("requirements", None)
    cfg = build(spec, TsGen(api))
    detail = next(d for d in cfg["actions"] if d["detail"] == "orders-detail")
    screen = next(s for s in spec["screens"] if s["id"] == "orders-detail")
    assert all(a["requirements"] == _reqs(screen) for a in detail["actions"])


def test_generated_scenarios_are_tagged_and_traced(tmp_path):
    spec, api = taxi()
    generate(spec, api, tmp_path)
    text = (tmp_path / "e2e" / "scenarios.spec.ts").read_text(encoding="utf-8")
    assert "--grep @FR-01" in text
    assert "const tags = (reqs: string[])" in text
    assert "${tags(f.requirements)}" in text and "${tags(a.requirements)}" in text and "${tags(g.requirements)}" in text
    trace = json.loads((tmp_path / "e2e" / "scenarios.trace.json").read_text(encoding="utf-8"))
    assert trace["requirements"]["FR-01"] and "skipped" in trace
    report = json.loads((tmp_path / "generation-report.json").read_text(encoding="utf-8"))
    assert report["filesWritten"] >= 1
