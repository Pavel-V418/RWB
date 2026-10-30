import copy
import json
from pathlib import Path

import pytest
import yaml

from specforge_web.agents.ux_designer.gate import run_gate

ROOT = Path(__file__).resolve().parents[1]
FX = ROOT / "fixtures" / "case1"
SPEC = json.loads((FX / "03_ux" / "screen-spec.web.json").read_text(encoding="utf-8"))
SPEC_ANDROID = json.loads((FX / "03_ux" / "screen-spec.android.json").read_text(encoding="utf-8"))
API = yaml.safe_load((FX / "02_system" / "openapi.yaml").read_text(encoding="utf-8"))
REQ = json.loads((FX / "01_requirements" / "requirements.json").read_text(encoding="utf-8"))


def screen(spec, sid):
    return next(s for s in spec["screens"] if s["id"] == sid)


def codes(res):
    return {e.code for e in res.errors if e.severity == "error"}


@pytest.mark.parametrize("spec", [SPEC, SPEC_ANDROID], ids=["web", "android"])
def test_reference_specs_pass(spec):
    res = run_gate(spec, API, REQ)
    assert res.passed, [e.message for e in res.errors]
    assert res.metrics["coverage"] == 1.0


def mutate(fn):
    s = copy.deepcopy(SPEC)
    fn(s)
    return run_gate(s, API, REQ)


CASES = {
    "missing_state": (lambda s: screen(s, "vehicle-list")["states"].pop("empty"), "UX_SCHEMA"),
    "kebab_id": (lambda s: screen(s, "vehicle-list").update(id="VehicleList"), "UX_SCHEMA"),
    "danger_without_confirm": (lambda s: screen(s, "session-detail")["actions"][1].pop("confirm"), "UX_SCHEMA"),
    "list_without_columns": (lambda s: screen(s, "zone-list").pop("list"), "UX_SCHEMA"),
    "unknown_operation": (lambda s: screen(s, "vehicle-list")["data"]["primary"].update(operationId="listCars"), "UX_UNKNOWN_OPERATION"),
    "unknown_field": (lambda s: screen(s, "vehicle-list")["list"]["columns"][0].update(field="plate"), "UX_UNKNOWN_FIELD"),
    "unknown_filter": (lambda s: screen(s, "vehicle-list")["list"]["filters"][0].update(field="color"), "UX_UNKNOWN_PARAM"),
    "unknown_search": (lambda s: screen(s, "zone-list")["list"]["search"].update(param="query"), "UX_UNKNOWN_PARAM"),
    "unknown_param": (lambda s: screen(s, "vehicle-detail")["data"]["secondary"]["sessions"]["params"].update(car="route:id"), "UX_UNKNOWN_PARAM"),
    "route_param": (lambda s: screen(s, "vehicle-detail").update(route="/vehicles/:vehicleId"), "UX_ROUTE_PARAM"),
    "bad_link": (lambda s: screen(s, "vehicle-list")["list"].update(rowLink="car-detail"), "UX_BAD_REF"),
    "bad_nav": (lambda s: s["navigation"].append({"screen": "nowhere", "label": "X"}), "UX_BAD_REF"),
    "bad_source": (lambda s: screen(s, "live-map")["map"]["layers"][1].update(source="cars"), "UX_BAD_REF"),
    "bad_role": (lambda s: screen(s, "zone-list")["roles"].append("guest"), "UX_BAD_REF"),
    "bad_state_action": (lambda s: screen(s, "vehicle-list")["states"]["empty"].update(actionId="create"), "UX_BAD_REF"),
    "duplicate_route": (lambda s: screen(s, "zone-list").update(route="/vehicles"), "UX_DUPLICATE"),
    "uncovered_fr": (lambda s: [sc.update(requirements=[r for r in sc["requirements"] if r != "FR-11"]) for sc in s["screens"]], "UX_FR_UNCOVERED"),
    "unknown_fr": (lambda s: screen(s, "zone-list")["requirements"].append("FR-99"), "UX_FR_UNKNOWN"),
    "form_field": (lambda s: screen(s, "vehicle-create")["form"]["fields"][1].update(name="brand"), "UX_UNKNOWN_FIELD"),
    "map_field": (lambda s: screen(s, "live-map")["map"]["layers"][0].update(geometryField="polygon"), "UX_UNKNOWN_FIELD"),
    "timeline_field": (lambda s: screen(s, "event-log")["timeline"].update(timeField="time"), "UX_UNKNOWN_FIELD"),
    "widget_field": (lambda s: screen(s, "overview")["dashboard"]["widgets"][0].update(valueField="inZones"), "UX_UNKNOWN_FIELD"),
    "slot_component": (lambda s: screen(s, "overview").update(slots=[{"id": "x", "placement": "aside", "task": "Виджет с графиком загрузки зон по часам", "components": ["p-dropdown"]}]), "UX_UNKNOWN_COMPONENT"),
}


@pytest.mark.parametrize("name", CASES.keys())
def test_gate_catches(name):
    fn, code = CASES[name]
    res = mutate(fn)
    assert not res.passed
    assert code in codes(res), [e.message for e in res.errors]


def test_hint_for_unknown_operation():
    res = mutate(CASES["unknown_operation"][0])
    msg = next(e.message for e in res.errors if e.code == "UX_UNKNOWN_OPERATION")
    assert "listVehicles" in msg  # ремонтник получает подсказку


def test_role_mismatch_is_warning():
    def fn(s):
        screen(s, "vehicle-create")["roles"].append("dispatcher")
    res = mutate(fn)
    assert res.passed
    assert any(e.code == "UX_ROLE_MISMATCH" for e in res.errors)


def test_contract_diff_detects_backend_drift(tmp_path):
    import yaml as _y
    from specforge_web.tools import contract_diff
    b = copy.deepcopy(API)
    b["components"]["schemas"]["Vehicle"]["properties"]["plate"] = b["components"]["schemas"]["Vehicle"]["properties"].pop("plateNumber")
    p = tmp_path / "backend.yaml"
    p.write_text(_y.safe_dump(b, allow_unicode=True), encoding="utf-8")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(SPEC, ensure_ascii=False), encoding="utf-8")
    assert contract_diff.main(["--spec", str(spec), "--api", str(p)]) == 1
    assert contract_diff.main(["--spec", str(spec), "--api", str(FX / "02_system" / "openapi.yaml")]) == 0
