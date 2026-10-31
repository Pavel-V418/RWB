"""Этап документации: artifacts/* -> artifacts/09_docs (сайт MkDocs).

1 вызов LLM (продуктовое описание, ADR, руководство пользователя); остальное — код.
Разделы, которые зависят от других участников (экраны, запуск, тесты, безопасность), читают их файлы
по договорённым путям; если файлов ещё нет — раздел помечается «Ожидается», шлюз не падает.
Повторный запуск после появления отчётов соседей обновляет документацию.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from .contract import GateResult, ensure_dir, failed_result, split_issues
from .gates import docs_gate
from .llm import LLM, FakeLLM, LLMError
from .prompting import compact, messages
from .render import docs_site
from .render.contracts import Model
from .schemas import Requirements
from .schemas_docs import DocsText
from .schemas_system import ApiDesign, DomainModel, SaProse
from .team_inputs import PATHS, TeamInputs, read_all, read_metrics

STAGE = "09_docs"
REQUIRED_PAGES = [p for _, p in docs_site.NAV]


def _context(req: Requirements, m: Model, prose: SaProse | None, ti: TeamInputs) -> dict:
    return {
        "product": req.meta.product_name, "summary": req.meta.summary,
        "goals": [g.statement for g in req.goals],
        "roles": [{"key": r.key, "name": r.name, "description": r.description} for r in req.roles],
        "functional": [{"id": f.id, "title": f.title, "priority": f.priority} for f in req.functional],
        "scenarios": [{"id": s.id, "title": s.title, "actor": s.actor.removeprefix("ROLE-"), "main_flow": s.main_flow,
                       "alternative_flows": s.alternative_flows, "error_flows": s.error_flows,
                       "requirements": s.requirements} for s in req.scenarios],
        "constraints": [c.statement for c in req.constraints],
        "decisions": [{"id": q.id, "question": q.question, "decision": q.answer, "status": q.status}
                      for q in req.questions],
        "assumptions": [{"id": a.id, "text": a.statement} for a in req.assumptions],
        "components": [{"name": c.name, "kind": c.kind, "tech": c.tech} for c in m.design.components],
        "architecture_notes": prose.architecture_notes if prose else [],
        "business_errors": [{"code": b.code, "message": b.title, "when": b.when} for b in (prose.business_errors if prose else [])],
        "screens": [{"id": s.id, "title": s.title, "platform": s.platform, "roles": s.roles, "fr": s.fr} for s in ti.screens],
    }


def write_texts(llm: LLM, ctx: dict, has_screens: bool) -> DocsText | None:
    if isinstance(llm, FakeLLM) and not llm.has("docs_writer"):
        return None
    return llm.structured("docs_writer", messages("docs_writer", system="docs_system", ctx_json=compact(ctx),
                                                  screens=has_screens),
                          DocsText, thinking=False, max_tokens=12000)


def _load_json(p: Path, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def run(artifacts_root: str | Path, output_dir: str | Path, llm: LLM, *,
        repo_root: str | Path | None = None) -> GateResult:
    t0 = time.monotonic()
    since = len(llm.metrics.calls)
    root, out = Path(artifacts_root), ensure_dir(output_dir)
    r1, r2 = root / "01_requirements", root / "02_system"

    req = Requirements.model_validate_json((r1 / "requirements.json").read_text(encoding="utf-8"))
    domain = DomainModel.model_validate_json((r2 / "domain-model.json").read_text(encoding="utf-8"))
    design = ApiDesign.model_validate_json((r2 / "api-design.json").read_text(encoding="utf-8"))
    prose = SaProse.model_validate_json((r2 / "sa-prose.json").read_text(encoding="utf-8")) \
        if (r2 / "sa-prose.json").exists() else None
    m = Model(domain, design, req)
    ti = read_all(root, repo_root)
    critic = _load_json(r1 / "critic.json", [])
    gate_warnings = []
    for stage_dir in (r1, r2):
        for w in _load_json(stage_dir / "gate_report.json", {}).get("warnings", []):
            gate_warnings.append(w | {"stage": stage_dir.name})

    try:
        texts = write_texts(llm, _context(req, m, prose, ti), bool(ti.screens))
    except LLMError as exc:
        return failed_result(STAGE, out, "E_LLM", exc, time.monotonic() - t0, llm.metrics.summary(since))
    ti.metrics = {}
    read_metrics(root, ti)  # перечитать: в отчёт попадает и вызов этого этапа

    docs = out / "docs"
    if docs.exists():
        shutil.rmtree(docs)
    (docs / "adr").mkdir(parents=True)
    (docs / "contracts").mkdir()
    (docs / "img").mkdir()

    has_events = (r2 / "asyncapi.yaml").exists()
    for name in ("openapi.yaml", "asyncapi.yaml", "schema.sql"):
        if (r2 / name).exists():
            shutil.copy2(r2 / name, docs / "contracts" / name)
    for shot in ti.screenshots:
        shutil.copy2(root / shot, docs / "img" / Path(shot).name)

    rows = docs_site.trace_rows(req, m, ti)
    tm = docs_site.trace_metrics(req, rows, ti)
    adrs = docs_site.adr_pages(texts)
    for fname, _title, body in adrs:
        (docs / "adr" / fname).write_text(body, encoding="utf-8")
    # ADR от команды (участник 3): нумеруются после сгенерированных, текст — как есть
    team_adr = root / PATHS["team_adr"]
    for mp in sorted(team_adr.glob("*.md")) if team_adr.is_dir() else []:
        n = len(adrs) + 1
        text = mp.read_text(encoding="utf-8")
        head = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), mp.stem)
        fname = f"ADR-{n:03d}.md"
        (docs / "adr" / fname).write_text(text, encoding="utf-8")
        adrs.append((fname, head if head.upper().startswith("ADR") else f"ADR-{n:03d}. {head}", text))
    adr_titles = [(f, t) for f, t, _ in adrs]

    # методы API по шаблону: ссылка на Swagger ведёт в contracts/
    (docs / "api-methods").mkdir()
    methods = sorted((r2 / "api-methods").glob("*.md")) if (r2 / "api-methods").exists() else []
    for mp in methods:
        (docs / "api-methods" / mp.name).write_text(
            mp.read_text(encoding="utf-8").replace("](../openapi.yaml)", "](../contracts/openapi.yaml)"), encoding="utf-8")
    (docs / "api-methods" / "index.md").write_text(
        "# Методы API\n\nКаждый метод описан по шаблону API организаторов.\n\n"
        + "\n".join(f"- [{mp.stem}]({mp.name})" for mp in methods) + "\n", encoding="utf-8")
    # паспорта сервисов по шаблону описания сервисов
    (docs / "services").mkdir()
    svc = [c for c in design.components if c.kind in ("backend", "worker", "mock")]
    team_svc = root / PATHS["team_services"]
    for c in svc:
        slug_ = docs_site.service_slug(c.name)
        own = team_svc / f"{slug_}.md"
        text = own.read_text(encoding="utf-8") if own.exists() else docs_site.page_service(c, m, prose, ti)
        (docs / "services" / f"{slug_}.md").write_text(text, encoding="utf-8")
    (docs / "services" / "index.md").write_text(
        "# Паспорта сервисов\n\nПо «Шаблону описания сервисов».\n\n"
        + "\n".join(f"- [{c.name}]({docs_site.service_slug(c.name)}.md)" for c in svc) + "\n", encoding="utf-8")

    # материалы участника 4 (руководство, каталог экранов) — как есть, в раздел «Интерфейс»
    ui_pages: list[tuple[str, str]] = []
    ui_src = root / PATHS["ui_docs"]
    if ui_src.is_dir():
        shutil.copytree(ui_src, docs / "ui")
        for mp in sorted((docs / "ui").rglob("*.md")):
            head = next((ln[2:].strip() for ln in mp.read_text(encoding="utf-8").splitlines()
                         if ln.startswith("# ")), mp.stem)
            ui_pages.append((head, mp.relative_to(docs).as_posix()))

    sa = (r2 / "system-analysis.md").read_text(encoding="utf-8")
    sa = re.sub(r"\]\((openapi\.yaml|asyncapi\.yaml|schema\.sql)\)", r"](contracts/\1)", sa)

    pages = {
        "index.md": docs_site.page_index(req, texts, tm),
        "user-guide.md": docs_site.page_user_guide(req, texts, ti),
        "run.md": docs_site.page_run(m, ti),
        "architecture.md": docs_site.page_architecture(m, prose, adr_titles),
        "adr/index.md": "# Журнал архитектурных решений\n\n" + (
            "\n".join(f"- [{t}]({f})" for f, t in adr_titles) or "Решения появятся после запуска техписателя.") + "\n",
        "api.md": docs_site.page_api(m, has_events),
        "data-model.md": docs_site.page_data_model(m),
        "requirements.md": (r1 / "requirements.md").read_text(encoding="utf-8"),
        "system-analysis.md": sa,
        "traceability.md": docs_site.page_traceability(rows, tm, ti),
        "test-report.md": docs_site.page_tests(req, rows, ti, tm),
        "security-report.md": docs_site.page_security(ti, req),
        "limitations.md": docs_site.page_limitations(req, m, prose, ti, critic, gate_warnings),
        "dev-guide.md": docs_site.page_dev_guide(req, m),
        "efficiency.md": docs_site.page_efficiency(ti),
    }
    for name, content in pages.items():
        (docs / name).write_text(content, encoding="utf-8")
    (out / "mkdocs.yml").write_text(docs_site.mkdocs_yml(req, adr_titles, ui_pages), encoding="utf-8")
    if texts:
        (out / "docs-text.json").write_text(texts.model_dump_json(indent=2), encoding="utf-8")

    pending_count = sum(c.count('!!! warning "Ожидается') for c in pages.values())
    issues = docs_gate.check(docs, REQUIRED_PAGES, tm, pending_count)
    for p in ti.problems:
        issues.append(docs_gate.Issue("W_TEAM_INPUT", "warning", f"не удалось прочитать: {p}"))
    errors, warnings = split_issues(issues)
    msum = llm.metrics.summary(since)
    report = {"passed": not errors, "errors": [i.__dict__ for i in errors], "warnings": [i.__dict__ for i in warnings],
              "traceability": tm, "pending_sections": pending_count,
              "team_inputs": {"screens": len(ti.screens), "screenshots": len(ti.screenshots), "tests": len(ti.tests),
                              "security_findings": len(ti.findings), "run_readme": bool(ti.infra.run_readme),
                              "compose_services": len(ti.infra.services)},
              "metrics": msum}
    (out / "gate_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return GateResult(stage=STAGE, passed=not errors, errors=errors, warnings=warnings,
                      tokens={"input": msum["input_tokens"], "output": msum["output_tokens"]},
                      attempts=msum["llm_calls"], llm_calls=msum["llm_calls"],
                      duration_s=round(time.monotonic() - t0, 2),
                      artifacts=sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()))
