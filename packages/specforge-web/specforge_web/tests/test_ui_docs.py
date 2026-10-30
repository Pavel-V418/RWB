"""Матрица трассируемости интерфейса: unit / smoke / пользовательские сценарии (tools/ui_docs.py)."""
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

from specforge_web.agents.ux_designer.baseline import baseline_spec  # noqa: E402
from specforge_web.generators.angular.generate import generate  # noqa: E402
from specforge_web.tools.ui_docs import generate_docs, traceability  # noqa: E402


def taxi():
    d = ROOT / "fixtures" / "synthetic-taxi"
    req = json.loads((d / "01_requirements" / "requirements.json").read_text(encoding="utf-8"))
    api = yaml.safe_load((d / "02_system" / "openapi.yaml").read_text(encoding="utf-8"))
    return baseline_spec(api, req, "web"), api, req


def built(tmp_path):
    spec, api, req = taxi()
    generate(spec, api, tmp_path / "admin")
    return spec, req, tmp_path / "admin"


def test_screen_open_check_is_smoke_not_e2e(tmp_path):
    spec, req, project = built(tmp_path)
    data, _ = traceability([spec], req, project)
    kinds = {t["kind"] for r in data["requirements"] for t in r["tests"]}
    assert "smoke" in kinds and "e2e" not in kinds


def test_requirements_link_to_user_scenarios(tmp_path):
    spec, req, project = built(tmp_path)
    data, md = traceability([spec], req, project)
    rows = {r["id"]: r for r in data["requirements"]}
    sc = [t for t in rows["FR-01"]["tests"] if t["kind"] == "scenario"]
    assert {t["scenario"] for t in sc} == {"main", "alternative"}
    assert all(t["file"] == "e2e/scenarios.spec.ts" for t in sc)
    assert "сценарий (основной): форма orders-create › создание" in md
    assert "проверено пользовательским сценарием:" in md and "**smoke**" in md


def test_summary_counts_and_access_only_is_not_a_scenario(tmp_path):
    spec, req, project = built(tmp_path)
    data, md = traceability([spec], req, project)
    sm = data["summary"]
    assert set(sm) == {"total", "withUi", "confident", "withScenario"}
    assert sm["total"] == len(data["requirements"]) and 0 < sm["withScenario"] <= sm["withUi"] <= sm["total"]
    rows = {r["id"]: r for r in data["requirements"]}
    # у FR про карту есть только проверка доступа по роли — это не сценарий требования
    assert {t.get("scenario") for t in rows["FR-02"]["tests"] if t["kind"] == "scenario"} == {"access"}
    line = next(x for x in md.splitlines() if x.startswith("| FR-02 "))
    assert "нет сценария — только открытие экрана" in line


def test_screen_without_scenario_is_named(tmp_path):
    spec, req, project = built(tmp_path)
    fake = {"requirements": {"FR-06": [{"case": "форма x › создание", "kind": "main", "screen": "orders-list"}]}}
    _, md = traceability([spec], req, project, scenarios=fake)
    line = next(x for x in md.splitlines() if x.startswith("| FR-06 "))
    tail = line.split("нет сценария — только открытие экрана: ")[1]
    assert "Водители" in tail and "Заказы," not in tail  # у списка заказов сценарий есть


def test_skipped_scenario_is_not_counted(tmp_path):
    spec, req, project = built(tmp_path)
    fake = {"requirements": {"FR-01": [{"case": "форма x › создание", "kind": "main", "screen": "orders-create", "skip": "нет примера"}]}}
    data, md = traceability([spec], req, project, scenarios=fake)
    assert data["summary"]["withScenario"] == 0
    assert "будет пропущен: нет примера" in md


def test_generate_docs_writes_summary(tmp_path):
    spec, req, project = built(tmp_path)
    art = tmp_path / "art"
    (art / "01_requirements").mkdir(parents=True)
    (art / "03_ux").mkdir()
    (art / "01_requirements" / "requirements.json").write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
    (art / "03_ux" / "screen-spec.web.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    generate_docs(art, art / "09_docs" / "ui", project)
    data = json.loads((art / "09_docs" / "ui" / "traceability.json").read_text(encoding="utf-8"))
    assert data["summary"]["withScenario"] >= 1
