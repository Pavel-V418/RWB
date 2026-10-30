"""Привязка FR к экранам по смыслу и честная уверенность (agents/ux_designer/trace.py)."""
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

from specforge_web.agents.ux_designer import trace  # noqa: E402
from specforge_web.agents.ux_designer.baseline import Baseline  # noqa: E402


def load(case):
    d = ROOT / "fixtures" / case
    return (json.loads((d / "01_requirements" / "requirements.json").read_text(encoding="utf-8")),
            yaml.safe_load((d / "02_system" / "openapi.yaml").read_text(encoding="utf-8")))


def test_stem_handles_russian_inflection():
    assert trace.stem("карте") == trace.stem("Карта") == "карт"
    assert trace.stem("заказов") == trace.stem("заказ")
    assert trace.verb_stems("Сотрудник отменяет бронь") & trace.verb_stems("Отменить")


def test_taxi_requirements_land_on_meaningful_screens():
    req, api = load("synthetic-taxi")
    b = Baseline(api, req, "web")
    b.build()
    t = b.trace
    assert t["FR-01"]["screens"] == ["orders-create"]          # «принимает заказ» → форма
    assert t["FR-02"]["screens"] == ["map"]                    # «на карте» → карта, а не карточка заказа
    assert t["FR-03"]["screens"] == ["orders-detail"]          # «назначить» → экран с действием
    assert t["FR-07"]["screens"] == ["events-log"]             # «журнал событий» → лента
    assert t["FR-08"]["screens"] == ["overview"]               # «сводка» → дашборд
    assert t["FR-09"]["screens"] == ["login"]                  # «вход» → экран входа
    assert all(x["confidence"] in ("high", "medium") for x in t.values())


def test_no_match_is_marked_low_not_hidden():
    req, api = load("synthetic-inventory")
    b = Baseline(api, req, "web")
    b.build()
    assert b.trace["FR-03"]["confidence"] == "low"  # «Списывать позицию»: действие в API без русской подписи
    assert "по умолчанию" in b.trace["FR-03"]["reason"]


def test_navigation_cancel_is_not_a_business_action():
    fr = {"id": "FR-1", "title": "Отменить заказ"}
    form = {"id": "f", "archetype": "form", "title": "Новый заказ", "requirements": [],
            "actions": [{"id": "cancel", "label": "Отмена", "navigateTo": "x"}]}
    detail = {"id": "d", "archetype": "detail", "title": "Заказ", "requirements": [],
              "actions": [{"id": "cancel", "label": "Отменить заказ", "operationId": "cancelOrder"}]}
    picked, conf, _ = trace.choose(fr, [form, detail])
    assert [s["id"] for s in picked] == ["d"] and conf == "high"


def test_assess_rates_existing_links_and_reports_uncovered():
    req = {"functional": [{"id": "FR-1", "title": "Сводка за смену", "channels": ["web"]},
                          {"id": "FR-2", "title": "Журнал событий", "channels": ["web"]}]}
    spec = {"app": {"platform": "web"}, "navigation": [],
            "screens": [{"id": "list", "archetype": "list", "title": "Список", "requirements": ["FR-1"]}]}
    t = trace.assess(spec, req)
    assert t["FR-1"]["confidence"] == "low" and t["FR-2"]["confidence"] == "none"
    assert trace.confident_share(t) == 0.0


def test_ux_agent_writes_trace_and_warns_on_weak_links(tmp_path):
    import shutil
    from specforge_web.agents.ux_designer import agent as ux
    for d in ("01_requirements", "02_system"):
        shutil.copytree(ROOT / "fixtures" / "synthetic-inventory" / d, tmp_path / d)
    res = ux.run(tmp_path, tmp_path / "03_ux", None, log=lambda *_: None)
    tr = json.loads((tmp_path / "03_ux" / "trace.web.json").read_text(encoding="utf-8"))
    assert tr["FR-03"]["confidence"] == "low"
    assert any(e.code == "UX_FR_WEAK" and e.severity == "warning" for e in res.errors)
    assert res.metrics["web"]["coverageConfident"] == 0.75


def test_service_screens_never_take_business_requirements():
    fr = {"id": "FR-1", "title": "Бронировать технику на период"}
    forbidden = {"id": "forbidden", "archetype": "forbidden", "title": "Нет доступа", "public": True, "requirements": []}
    login = {"id": "login", "archetype": "login", "title": "Вход", "public": True, "requirements": []}
    assert trace.choose(fr, [forbidden, login])[0] == []
    auth = {"id": "FR-2", "title": "Вход по учётной записи"}
    assert [s["id"] for s in trace.choose(auth, [forbidden, login])[0]] == ["login"]
