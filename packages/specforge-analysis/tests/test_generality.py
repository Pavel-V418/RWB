"""Конвейер не должен знать о проверочном кейсе: кейс 2 скрыт до оценки."""

import re
from pathlib import Path

import yaml

from specforge_analysis import rehearse, stage_requirements, stage_system
from specforge_analysis.llm import FakeLLM, Metrics
from specforge_analysis.schemas import Requirements

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "specforge_analysis"
CASES = ROOT / "cases"
FIX = ROOT / "golden" / "case1_parking" / "llm"

FORBIDDEN = re.compile(r"\bпарков|\bпаркомат|\bводител|\bавтомоб|7801|parking|parkomat|\bdriver\b|\bvehicle\b|\bzone\b",
                       re.IGNORECASE)
ALLOW = re.compile(r'get\("driver"')  # ключ формата SARIF (tool.driver), не доменное слово


def test_no_case_specific_words_in_pipeline():
    hits = []
    for p in list(PKG.rglob("*.py")) + list(PKG.rglob("*.j2")):
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if FORBIDDEN.search(line) and not ALLOW.search(line):
                hits.append(f"{p.relative_to(ROOT)}:{n}: {line.strip()[:120]}")
    assert not hits, "\n".join(hits)


def test_all_cases_have_expectations_and_load():
    cases = rehearse.find_cases(CASES)
    assert {c.name for c in cases} >= {"case1_parking", "case2_repair_requests", "case3_tool_issuing",
                                       "case4_site_inspections"}
    for c in cases:
        exp = yaml.safe_load((c / "expected.yaml").read_text(encoding="utf-8"))
        assert exp["must_cover"] and exp["profile"]


def test_scoring_on_golden_case(tmp_path):
    stage_requirements.run(CASES / "case1_parking", tmp_path, FakeLLM(FIX, metrics=Metrics()))
    req = Requirements.model_validate_json((tmp_path / "requirements.json").read_text(encoding="utf-8"))
    exp = yaml.safe_load((CASES / "case1_parking" / "expected.yaml").read_text(encoding="utf-8"))
    s = rehearse.score_requirements(req, exp)
    assert s["profile_misses"] == [] and s["fr_missed"] == []


def test_case_without_events_marks_section_not_applicable(tmp_path):
    """Профиль без асинхронных событий: нет asyncapi.yaml и outbox, раздел 10 помечен как неприменимый."""
    s1 = tmp_path / "s1"
    stage_requirements.run(CASES / "case1_parking", s1, FakeLLM(FIX, metrics=Metrics()))
    req_path = s1 / "requirements.json"
    req = Requirements.model_validate_json(req_path.read_text(encoding="utf-8"))
    req.profile.needs_async_events = False
    req_path.write_text(req.model_dump_json(), encoding="utf-8")

    fx = tmp_path / "fx"
    fx.mkdir()
    for name in ("domain", "sa_prose"):
        (fx / f"{name}.json").write_text((FIX / f"{name}.json").read_text(encoding="utf-8"), encoding="utf-8")
    import json
    design = json.loads((FIX / "api_design.json").read_text(encoding="utf-8"))
    design["events"] = []
    design["components"] = [c for c in design["components"] if c["kind"] != "broker"]
    (fx / "api_design.json").write_text(json.dumps(design, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "s2"
    res = stage_system.run(s1, out, FakeLLM(fx, metrics=Metrics()), check_db=False)
    assert res.passed, [str(e) for e in res.errors]
    assert not (out / "asyncapi.yaml").exists()
    assert "outbox_events" not in (out / "schema.sql").read_text(encoding="utf-8")
    doc = (out / "system-analysis.md").read_text(encoding="utf-8")
    assert "#### 3.3.2 Внутренние интеграции\n\nНеприменимо" in doc
