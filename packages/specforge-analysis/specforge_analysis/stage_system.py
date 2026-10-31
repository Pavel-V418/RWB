"""Этап 2 целиком: artifacts/01_requirements -> artifacts/02_system.

Граф вызовов: domain -> api_design -> (repair_api 0–2) -> sa_prose. Контракты, DDL, диаграммы,
SA-документ и трассируемость строит код.
"""

from __future__ import annotations

import csv
import io
import json
import time
from pathlib import Path

import yaml

from .agents import system_agents as agents
from .contract import GateResult, Issue, ensure_dir, failed_result, reset_outputs, split_issues
from .gates import system_gate as gate
from .gates.pgcheck import check_ddl
from .llm import LLM, LLMError
from .prompting import compact
from .render import api_methods, contracts, diagrams, sa_document
from .render.requirements_artifacts import TRACE_COLUMNS
from .schemas import Requirements

STAGE = "02_system"
MAX_REPAIR = 2


class _Dumper(yaml.SafeDumper):
    pass


def _str_repr(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_repr)


def to_yaml(doc: dict) -> str:
    return yaml.dump(doc, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=120)


def _traceability(req_dir: Path, cov: dict) -> str:
    src = req_dir / "traceability.csv"
    rows = list(csv.DictReader(io.StringIO(src.read_text(encoding="utf-8")))) if src.exists() else []
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=TRACE_COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        c = cov.get(r["requirement"], {})
        r["api"] = "; ".join(c.get("api", [])) or ("no_api" if c.get("no_api") else "")
        r["events"] = " ".join(c.get("events", []))
        if r.get("status") == "analysed" and (c.get("api") or c.get("events") or c.get("no_api")):
            r["status"] = "designed"
        w.writerow({k: r.get(k, "") for k in TRACE_COLUMNS})
    return buf.getvalue()


# ошибки шлюза, которые может исправить модель (остальные — следствие этих или требований этапа 1)
REPAIRABLE = {"E_PATH_VERB", "E_PATH_FORMAT", "E_DUP_ROUTE", "E_DUP_OPID", "E_OP_NO_ROLE", "E_SM_ENTITY",
              "E_SM_FIELD", "E_SM_STATES", "E_ENUM_EMPTY", "E_REGEX", "E_DUP_FIELD", "E_EMPTY_REQUEST", "E_DDL",
              "E_OPENAPI_PARAM"}


def _ops_for(design, issues: list[Issue]) -> list:
    """Операции, к которым относятся ошибки (по operationId, «METHOD /path» или пути)."""
    targets = {i.target for i in issues if i.target}
    return [o for o in design.operations
            if o.operation_id in targets or o.path in targets or f"{o.method.upper()} {o.path}" in targets]


def _apply_design_patch(domain, design, patch) -> list[Issue]:
    fixes: list[Issue] = []
    ents = {e.name: i for i, e in enumerate(domain.entities)}
    for e in patch.entities:
        if e.name in ents:
            domain.entities[ents[e.name]] = e
        else:
            domain.entities.append(e)
        fixes.append(Issue("W_DESIGN_REPAIRED", "warning", "сущность исправлена по ошибкам шлюза", e.name))
    sms = {(x.entity, x.field): i for i, x in enumerate(domain.state_machines)}
    for sm in patch.state_machines:
        if (sm.entity, sm.field) in sms:
            domain.state_machines[sms[(sm.entity, sm.field)]] = sm
        else:
            domain.state_machines.append(sm)
        fixes.append(Issue("W_DESIGN_REPAIRED", "warning", "жизненный цикл исправлен", f"{sm.entity}.{sm.field}"))
    if patch.remove_operations:
        drop = set(patch.remove_operations)
        # удаляем по одному экземпляру: при дубле operationId вторая копия заменится исправленной ниже
        kept, removed = [], set()
        for o in design.operations:
            if o.operation_id in drop and o.operation_id not in removed:
                removed.add(o.operation_id)
                continue
            kept.append(o)
        design.operations[:] = kept
        fixes += [Issue("W_DESIGN_REPAIRED", "warning", "дублирующая операция удалена", x) for x in sorted(removed)]
    ops = {o.operation_id: i for i, o in enumerate(design.operations)}
    for o in patch.operations:
        if o.operation_id in ops:
            design.operations[ops[o.operation_id]] = o
        else:
            design.operations.append(o)
        fixes.append(Issue("W_DESIGN_REPAIRED", "warning", "операция исправлена по ошибкам шлюза", o.operation_id))
    return fixes


OUTPUTS = ["*.json", "*.yaml", "*.sql", "*.md", "*.csv", "api-methods/*.md", "diagrams/*"]


def run(input_dir: str | Path, output_dir: str | Path, llm: LLM, *, check_db: bool = True) -> GateResult:
    """Контракт оркестратора: всегда возвращает GateResult, в том числе при сбое модели (E_LLM)."""
    t0 = time.monotonic()
    since = len(llm.metrics.calls)
    out = ensure_dir(output_dir)
    reset_outputs(out, OUTPUTS, protect=Path(input_dir))
    try:
        return _run(Path(input_dir), out, llm, check_db=check_db, t0=t0, since=since)
    except LLMError as exc:
        return failed_result(STAGE, out, "E_LLM", exc, time.monotonic() - t0, llm.metrics.summary(since))


def _run(req_dir: Path, out: Path, llm: LLM, *, check_db: bool, t0: float, since: int) -> GateResult:
    req = Requirements.model_validate_json((req_dir / "requirements.json").read_text(encoding="utf-8"))
    req_json = compact(agents.req_view(req))

    # 1. доменная модель, 2. API и события
    domain = agents.design_domain(llm, req_json)
    design = agents.design_api(llm, req_json, domain)
    m = contracts.Model(domain, design, req)
    fixes = gate.autofix(m)

    # 3. ремонт покрытия требований
    for attempt in range(1, MAX_REPAIR + 1):
        missing = gate.unmapped_fr(m)
        if not missing:
            break
        patch = agents.repair_api(llm, req_json, domain, design, missing, attempt)
        if patch is None:
            break
        known = {o.operation_id for o in design.operations}
        design.operations += [o for o in patch.operations if o.operation_id not in known]
        design.events += [e for e in patch.events if e.name not in {x.name for x in design.events}]
        design.no_api += patch.no_api
        fixes += gate.autofix(m)

    # 4. контракты и проверки; 4а. ремонт оставшихся блокирующих ошибок (0–MAX_REPAIR вызовов)
    for attempt in range(0, MAX_REPAIR + 1):
        openapi = contracts.build_openapi(m)
        asyncapi = contracts.build_asyncapi(m)
        ddl = contracts.build_ddl(m)
        ddl_check = check_ddl(ddl) if check_db else None
        issues = gate.check(m, openapi, asyncapi, ddl_check)
        repairable = [i for i in issues if i.severity == "error" and i.code in REPAIRABLE]
        if not repairable or attempt == MAX_REPAIR:
            break
        patch = agents.repair_design(llm, req_json, domain, _ops_for(design, repairable),
                                     [str(i) for i in repairable], attempt + 1)
        if patch is None:
            break
        fixes += _apply_design_patch(domain, design, patch)
        m = contracts.Model(domain, design, req)
        fixes += gate.autofix(m)
    cov = gate.coverage_map(m)

    # 5. текст документа
    prose = agents.write_prose(llm, req_json, domain, design)
    for b in (prose.business_errors if prose else []):  # код — snake_case, внешний поставщик — 502
        b.code = contracts.error_code(b.code)
        if b.status in (502, 503):
            b.status, b.code = 502, "external_provider_error"

    sa_text = sa_document.render(m, prose, cov, asyncapi is not None)
    for h in sa_document.missing_headings(sa_text):
        issues.append(Issue("E_SA_SECTION", "error", f"нет обязательного раздела регламента: {h}"))

    files: dict[str, str] = {
        "domain-model.json": domain.model_dump_json(indent=2),
        "api-design.json": design.model_dump_json(indent=2),
        "openapi.yaml": to_yaml(openapi),
        "schema.sql": ddl,
        "schema.platform.sql": contracts.build_platform_ddl(m),
        "diagrams/components.mmd": diagrams.components_mermaid(m),
        "diagrams/er.mmd": diagrams.er_mermaid(m),
        "system-analysis.md": sa_text,
        "diagrams/components.puml": diagrams.components_plantuml(m),
        "diagrams/er.puml": diagrams.er_plantuml(m),
        "traceability.csv": _traceability(req_dir, cov),
    }
    if asyncapi:
        files["asyncapi.yaml"] = to_yaml(asyncapi)
    for i, seq in enumerate(design.sequences, 1):
        files[f"diagrams/sequence-{i:02d}.mmd"] = diagrams.sequence_mermaid(seq)
        files[f"diagrams/sequence-{i:02d}.puml"] = diagrams.sequence_plantuml(seq)
    for sm in domain.state_machines:
        files[f"diagrams/state-{contracts.snake(sm.entity)}.mmd"] = diagrams.state_mermaid(sm)
        files[f"diagrams/state-{contracts.snake(sm.entity)}.puml"] = diagrams.state_plantuml(sm)
    files.update(api_methods.render_all(m, business_errors=prose.business_errors if prose else None))
    if prose:
        files["sa-prose.json"] = prose.model_dump_json(indent=2)

    errors, warnings = split_issues(issues + fixes)
    msum = llm.metrics.summary(since)
    report = {
        "passed": not errors,
        "errors": [i.__dict__ for i in errors], "warnings": [i.__dict__ for i in warnings],
        "ddl_check": ddl_check.__dict__ if ddl_check else None,
        "coverage": {
            "functional": len(req.functional),
            "fr_with_api_or_event": sum(1 for v in cov.values() if v["api"] or v["events"]),
            "fr_no_api_justified": sum(1 for v in cov.values() if v["no_api"] and not (v["api"] or v["events"])),
            "operations": len(design.operations), "events": len(design.events),
            "entities": len(domain.entities), "sequences": len(design.sequences),
        },
        "metrics": msum,
    }
    files["gate_report.json"] = json.dumps(report, ensure_ascii=False, indent=2)
    for name, content in files.items():
        p = out / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    return GateResult(
        stage=STAGE, passed=not errors, errors=errors, warnings=warnings,
        tokens={"input": msum["input_tokens"], "output": msum["output_tokens"]},
        attempts=msum["llm_calls"], llm_calls=msum["llm_calls"],
        duration_s=round(time.monotonic() - t0, 2), artifacts=sorted(files),
    )
