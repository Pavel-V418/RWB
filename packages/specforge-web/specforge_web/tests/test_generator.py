import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

from specforge_web.generators.angular.generate import SLOT_STUB_MARK, generate  # noqa: E402
from specforge_web.tools import kit_lint, template_check, ts_check  # noqa: E402

CASES = {
    "case1": ROOT / "fixtures" / "case1",
    "synthetic-repair": ROOT / "fixtures" / "synthetic-repair",
}
# «водител» — только с начала слова: «Руководитель», «Производитель» — не доменные слова
DOMAIN_WORDS = re.compile(r"парков|parking|vehicle|автомоб|госномер|(?<![а-яё])водител|паркомат|\bzones?\b|зон[аыу]|ticket|заявк|ремонт(?!ник)", re.I)
ZONE_JS = re.compile(r"zone\.js|ngzone|this\.zone|setup-env/zone", re.I)
# Конкретные координаты (точнее 0,01°) — тоже привязка к кейсу: центр карты задаёт спека или окружение
COORDS = re.compile(r"\[\s*-?\d{1,3}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}\s*\]")


def load(case: Path):
    spec = json.loads((case / "03_ux" / "screen-spec.web.json").read_text(encoding="utf-8"))
    api = yaml.safe_load((case / "02_system" / "openapi.yaml").read_text(encoding="utf-8"))
    return spec, api


@pytest.fixture(scope="module", params=list(CASES))
def project(request, tmp_path_factory):
    spec, api = load(CASES[request.param])
    out = tmp_path_factory.mktemp(request.param)
    summary = generate(spec, api, out)
    return request.param, out, summary, spec


def test_every_screen_has_page_and_spec(project):
    _, out, summary, spec = project
    for s in spec["screens"]:
        d = out / "src" / "app" / "generated" / "pages" / s["id"]
        assert (d / f"{s['id']}.page.ts").exists()
        assert (d / f"{s['id']}.page.html").exists()
        assert (d / f"{s['id']}.page.spec.ts").exists()
    assert len(summary["pages"]) == len(spec["screens"])


def test_kit_lint_clean(project):
    _, out, _, _ = project
    errors = [f for f in kit_lint.lint_dir(out / "src") if f.severity == "error"]
    assert not errors, errors[:5]


def test_templates_consistent(project):
    _, out, _, _ = project
    findings = template_check.check_dir(out / "src")
    assert not findings, findings[:5]


@pytest.mark.skipif(ts_check.find_tsc() is None, reason="tsc недоступен")
def test_types_consistent(project):
    _, out, _, _ = project
    errs = ts_check.run(out)
    assert not errs, errs[:5]


def test_states_present_in_every_data_page(project):
    _, out, _, spec = project
    for s in spec["screens"]:
        if s["archetype"] in ("login", "forbidden"):
            continue
        html = (out / "src" / "app" / "generated" / "pages" / s["id"] / f"{s['id']}.page.html").read_text(encoding="utf-8")
        assert f'testid="{s["id"]}-state"' in html, s["id"]
        assert s["states"]["empty"]["title"] in html or s["states"]["empty"]["title"] == "—"


def test_static_routes_before_param_routes(project):
    _, out, _, _ = project
    routes = (out / "src" / "app" / "generated" / "routes.ts").read_text(encoding="utf-8")
    paths = re.findall(r"path: '([^']*)'", routes.split("GENERATED_ROUTES")[1])
    for i, p in enumerate(paths):
        if ":" in p:
            prefix = p.split(":")[0]
            later_static = [q for q in paths[i + 1:] if q.startswith(prefix) and ":" not in q and q.count("/") == p.count("/")]
            assert not later_static, (p, later_static)


def test_slot_preserved_on_regeneration(tmp_path):
    spec, api = load(CASES["synthetic-repair"])
    generate(spec, api, tmp_path)
    slot = tmp_path / "src" / "app" / "slots" / "workload-chart.slot.ts"
    assert SLOT_STUB_MARK in slot.read_text(encoding="utf-8")
    impl = slot.read_text(encoding="utf-8").replace(SLOT_STUB_MARK, "implemented")
    slot.write_text(impl, encoding="utf-8")
    summary = generate(spec, api, tmp_path)
    assert slot.read_text(encoding="utf-8") == impl
    assert summary["slotsImplemented"] == 1


def test_template_check_catches_errors(tmp_path):
    (tmp_path / "x.component.ts").write_text("export class X {\n  protected readonly a = 1;\n  protected go(): void {}\n}\n", encoding="utf-8")
    (tmp_path / "x.component.html").write_text('<div>{{ a }} {{ b }}<p-button (onClick)="go()" /><span></div>\n@if (a) {\n', encoding="utf-8")
    codes = {f.code for f in template_check.check_dir(tmp_path)}
    assert {"TPL_UNKNOWN_MEMBER", "TPL_UNBALANCED_TAG", "TPL_UNBALANCED_BLOCK"} <= codes


@pytest.mark.skipif(ts_check.find_tsc() is None, reason="tsc недоступен")
def test_ts_check_catches_wrong_api_params(tmp_path):
    spec, api = load(CASES["case1"])
    generate(spec, api, tmp_path)
    page = tmp_path / "src" / "app" / "generated" / "pages" / "zone-detail" / "zone-detail.page.ts"
    page.write_text(page.read_text(encoding="utf-8").replace("this.api.getParkingZone({ id:", "this.api.getParkingZone({ zoneKey:"), encoding="utf-8")
    errs = ts_check.run(tmp_path)
    assert any(e["file"].endswith("zone-detail.page.ts") for e in errs), errs


def test_pipeline_code_is_domain_free():
    hits = []
    for folder in ("scaffolds", "generators", "design-system", "tools", "agents", "contracts"):
        for p in (ROOT / folder).rglob("*"):
            if p.is_file() and p.suffix in (".ts", ".py", ".html", ".scss", ".json", ".md", ".yaml") and "__pycache__" not in p.parts:
                for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                    if DOMAIN_WORDS.search(line) and not ZONE_JS.search(line) or COORDS.search(line):
                        hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()[:80]}")
    assert not hits, hits[:10]


def test_e2e_contains_realtime_check(tmp_path):
    spec, api = load(CASES["case1"])
    generate(spec, api, tmp_path)
    e2e = (tmp_path / "e2e" / "screens.spec.ts").read_text(encoding="utf-8")
    assert '"id": "event-log"' in e2e.split("const LIVE")[1] and "E2E_REALTIME" in e2e
