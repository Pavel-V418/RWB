import csv
import json
from pathlib import Path

import pytest
import yaml

from specforge_analysis import stage_requirements, stage_system
from specforge_analysis.gates import system_gate as gate
from specforge_analysis.gates.pgcheck import check_ddl
from specforge_analysis.llm import FakeLLM, Metrics
from specforge_analysis.render import contracts
from specforge_analysis.schemas import Requirements
from specforge_analysis.schemas_system import ApiDesign, DomainModel

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "cases" / "case1_parking"
FIX = ROOT / "golden" / "case1_parking" / "llm"


@pytest.fixture(scope="module")
def stage1(tmp_path_factory):
    out = tmp_path_factory.mktemp("s1")
    stage_requirements.run(CASE, out, FakeLLM(FIX, metrics=Metrics()))
    return out


@pytest.fixture(scope="module")
def stage2(stage1, tmp_path_factory):
    out = tmp_path_factory.mktemp("s2")
    res = stage_system.run(stage1, out, FakeLLM(FIX, metrics=Metrics()), check_db=False)
    return res, out


def _model(stage1) -> contracts.Model:
    req = Requirements.model_validate_json((stage1 / "requirements.json").read_text(encoding="utf-8"))
    dm = DomainModel.model_validate_json((FIX / "domain.json").read_text(encoding="utf-8"))
    ad = ApiDesign.model_validate_json((FIX / "api_design.json").read_text(encoding="utf-8"))
    return contracts.Model(dm, ad, req)


def test_golden_stage2_passes(stage2):
    res, out = stage2
    assert res.passed, [str(e) for e in res.errors]
    for name in ("openapi.yaml", "asyncapi.yaml", "schema.sql", "system-analysis.md", "traceability.csv"):
        assert (out / name).exists(), name


def test_every_fr_traced_to_api_or_event(stage2):
    _, out = stage2
    rows = list(csv.DictReader((out / "traceability.csv").open(encoding="utf-8")))
    assert rows and all(r["api"] or r["events"] for r in rows)
    assert all(r["status"] == "designed" for r in rows)


def test_sa_document_follows_regulation(stage2):
    from specforge_analysis.render.sa_document import missing_headings
    _, out = stage2
    text = (out / "system-analysis.md").read_text(encoding="utf-8")
    assert missing_headings(text) == []
    assert "```plantuml" in text and "@startuml" in text          # регламент: PlantUML
    assert "| FR-1 |" in text and "BR-" in text                  # FR-n и трассировка BR
    assert "**Постусловия:**" in text and "**Ожидаемый результат:**" in text
    for appendix in ("Приложение А", "Приложение Б", "Приложение В"):
        assert f"## {appendix}" in text


def test_api_method_pages_follow_template(stage2):
    _, out = stage2
    pages = sorted((out / "api-methods").glob("*.md"))
    assert len(pages) == 19
    text = (out / "api-methods" / "startSession.md").read_text(encoding="utf-8")
    for h in ("## Основная информация", "## История изменений", "### Авторизация", "### Заголовки",
              "### Параметры path", "### Параметры query", "### Модель данных тела запроса", "### Пример запроса",
              "### Кому разрешено вызывать метод", "### Успешная обработка запроса", "### Неуспешная обработка запроса",
              "## 4. Описание алгоритма работы метода", "### Взаимодействие с таблицами"):
        assert h in text, h
    assert "`parking_sessions.vehicle_id`" in text and "Idempotency-Key" in text


def test_verb_in_path_is_error(stage1):
    m = _model(stage1)
    op = next(o for o in m.design.operations if o.operation_id == "updateSession")
    op.method, op.path = "post", "/api/v1/sessions/{sessionId}/close"
    issues = gate.check(m, contracts.build_openapi(m), None, None)
    assert any(i.code == "E_PATH_VERB" for i in issues)
    assert gate.path_verbs("/api/v1/zones/{zoneId}/payment-link") == []


def test_openapi_consistent_with_domain(stage2):
    _, out = stage2
    doc = yaml.safe_load((out / "openapi.yaml").read_text(encoding="utf-8"))
    assert doc["openapi"] == "3.1.0"
    assert not gate.check_openapi(doc)
    vehicle = doc["components"]["schemas"]["Vehicle"]["properties"]["plateNumber"]
    assert vehicle["maxLength"] == 9 and vehicle["pattern"].startswith("^[АВЕКМНОРСТУХ]")
    assert "passwordHash" not in doc["components"]["schemas"]["UserAccount"]["properties"]
    start = doc["paths"]["/api/v1/sessions"]["post"]
    assert not any(gate.path_verbs(p) for p in doc["paths"])
    assert {"$ref": "#/components/parameters/IdempotencyKey"} in start["parameters"]
    assert "x-requirements" in start


def test_ddl_has_postgis_and_fks(stage2):
    _, out = stage2
    ddl = (out / "schema.sql").read_text(encoding="utf-8")
    assert "geometry(Polygon, 4326)" in ddl
    assert "REFERENCES parking_zones(id)" in ddl
    assert "USING GIST (boundary)" in ddl
    assert "outbox_events" not in ddl and "idempotency_keys" not in ddl   # они в каркасе backend
    assert "CREATE TABLE outbox_events" in (out / "schema.platform.sql").read_text(encoding="utf-8")


def test_unmapped_fr_is_error(stage1):
    m = _model(stage1)
    m.design.operations = [o for o in m.design.operations if o.operation_id != "getPaymentLink"]
    issues = gate.check(m, contracts.build_openapi(m), contracts.build_asyncapi(m), None)
    assert any(i.code == "E_FR_UNMAPPED" and i.target == "FR-11" for i in issues)


def test_unknown_request_field_is_dropped(stage1):
    m = _model(stage1)
    op = next(o for o in m.design.operations if o.operation_id == "startSession")
    op.request.fields.append("nonexistentField")
    fixes = gate.autofix(m)
    assert "nonexistentField" not in op.request.fields
    assert any(i.code == "W_FIELD_DROPPED" for i in fixes)


def test_bad_regex_is_error(stage1):
    m = _model(stage1)
    m.entities["Vehicle"].fields[1].pattern = "^[А-Я"
    issues = gate.check(m, contracts.build_openapi(m), None, None)
    assert any(i.code == "E_REGEX" for i in issues)


def test_ddl_check_catches_errors():
    res = check_ddl("CREATE TABLE a (id uuid PRIMARY KEY, b uuid REFERENCES missing(id));")
    if not res.ran:
        pytest.skip("PostgreSQL недоступен")
    assert not res.ok and "missing" in res.message


def test_naming():
    assert contracts.table_name("ParkingSession") == "parking_sessions"
    assert contracts.table_name("Category") == "categories"
    assert contracts.snake("plateNumber") == "plate_number"


def test_empty_request_body_is_dropped(stage1):
    from specforge_analysis.schemas_system import RequestDef
    m = _model(stage1)
    op = next(o for o in m.design.operations if o.method == "get")
    op.request = RequestDef(entity=op.response.entity, fields=[])
    gate.autofix(m)
    assert op.request is None
    assert not any(i.code == "E_EMPTY_REQUEST" for i in gate.check(m, contracts.build_openapi(m), None, None))


def test_ui_labels_in_openapi(stage2):
    _, out = stage2
    doc = yaml.safe_load((out / "openapi.yaml").read_text(encoding="utf-8"))
    props = doc["components"]["schemas"]["ParkingSession"]["properties"]
    assert props["status"]["x-enum-labels"]["active"] == "Активна"
    assert doc["components"]["schemas"]["Vehicle"]["properties"]["plateNumber"]["title"] == "Госномер"
    assert any(t.get("description") for t in doc["tags"])


def test_verb_in_path_is_autofixed(stage1):
    m = _model(stage1)
    op = next(o for o in m.design.operations if o.method == "get" and "{" in o.path)
    op.path = op.path + "/export"
    gate.autofix(m)
    assert op.path.endswith("/exports")
    assert not any(i.code == "E_PATH_VERB" for i in gate.check(m, contracts.build_openapi(m), None, None))


def test_backend_conventions(stage2):
    """Договорённости с backend: snake_case коды, errors-объект, 502 для поставщика, честные примеры."""
    _, out = stage2
    doc = yaml.safe_load((out / "openapi.yaml").read_text(encoding="utf-8"))
    assert doc["components"]["schemas"]["Problem"]["properties"]["errors"]["type"] == "object"
    for path in doc["paths"].values():
        for op in path.values():
            assert {"401", "429", "500"} <= set(op["responses"]) and "503" not in op["responses"]
    page = (out / "api-methods" / "startSession.md").read_text(encoding="utf-8")
    assert '"code": "session_already_active"' in page and "errors.app.local/session-already-active" in page
    req = page.split("### Пример запроса")[1].split("```", 2)[1]
    assert '"startMode": "started"' in req and "confirmedPaidUntil" not in req and '"durationMinutes": 60' in req
    assert '"endedAt": null' in page
    sa = (out / "system-analysis.md").read_text(encoding="utf-8")
    assert "`external_provider_error`" in sa and "-already-" not in sa
