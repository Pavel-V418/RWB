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
from .contract import GateResult, Issue, ensure_dir, split_issues
from .gates import system_gate as gate
from .gates.pgcheck import check_ddl
from .llm import LLM
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


def run(input_dir: str | Path, output_dir: str | Path, llm: LLM, *, check_db: bool = True) -> GateResult:
    t0 = time.monotonic()
    since = len(llm.metrics.calls)
    req_dir, out = Path(input_dir), ensure_dir(output_dir)
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

    # 4. контракты и проверки
    openapi = contracts.build_openapi(m)
    asyncapi = contracts.build_asyncapi(m)
    ddl = contracts.build_ddl(m)
    ddl_check = check_ddl(ddl) if check_db else None
    issues = gate.check(m, openapi, asyncapi, ddl_check)
    cov = gate.coverage_map(m)

    # 5. текст документа
    prose = agents.write_prose(llm, req_json, domain, design)
    for b in prose.business_errors:  # код — snake_case, внешний поставщик — 502
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
    if (out / "api-methods").exists():  # удалённые методы не должны оставаться в каталоге
        for old in (out / "api-methods").glob("*.md"):
            old.unlink()
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
