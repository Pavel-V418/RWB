"""Этап 1 целиком: run(input_dir, output_dir, llm) -> GateResult.

Граф вызовов фиксирован: extract -> analyze -> critic -> repair (0–2). Всё остальное — код.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

from .agents import requirements_agents as agents
from .assemble import build_base, build_business, link_backrefs, merge_analysis, set_acceptance
from .contract import GateResult, Issue, ensure_dir, split_issues
from .gates import requirements_gate as gate
from .llm import LLM
from .normalize import SourceIndex, find_input, load_requirements
from .render.requirements_artifacts import report_md, write_all

STAGE = "01_requirements"
MAX_REPAIR = 2
REPAIR_CATEGORIES = {"untestable", "missing_error", "incomplete", "contradiction"}
REPAIR_WARNINGS = {"W_VAGUE_AC", "W_NO_NEGATIVE_AC"}


def _load_answers(input_dir: Path) -> dict[str, str]:
    for name in ("answers.yaml", "answers.yml"):
        p = input_dir / name if input_dir.is_dir() else input_dir.parent / name
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            return {str(k): str(v) for k, v in data.items() if v not in (None, "")}
    return {}


def run(input_dir: str | Path, output_dir: str | Path, llm: LLM, *, mode: str = "auto",
        use_critic: bool = True) -> GateResult:
    t0 = time.monotonic()
    since = len(llm.metrics.calls)
    input_dir, out = Path(input_dir), ensure_dir(output_dir)

    src_path = find_input(input_dir)
    source = load_requirements(src_path)
    (out / "normalized.md").write_text(source, encoding="utf-8")
    index = SourceIndex(source)
    answers = _load_answers(input_dir)

    # 1. извлечение + профиль кейса
    ex = agents.extract(llm, source)
    req = build_base(ex, index, src_path.name, mode)

    # 2. анализ: сценарии, критерии, истории, безопасность, неоднозначности
    an = agents.analyze(llm, source, req)
    merge_analysis(req, an, mode, answers)
    fixes = gate.autofix(req)
    link_backrefs(req)
    issues = gate.check(req)

    # 3. критик
    findings: list[dict] = []
    crit = agents.critique(llm, source, req) if use_critic else None
    if crit:
        findings = [f.model_dump() | {"status": "open"} for f in crit.findings]

    # 4. точечный ремонт критериев приёмки
    for attempt in range(1, MAX_REPAIR + 1):
        targets: dict[str, list[str]] = {}
        for i in issues:
            if i.code == "E_NO_AC" or (attempt == 1 and i.code in REPAIR_WARNINGS):
                fr = agents.fr_of(i.target)
                if fr:
                    targets.setdefault(fr, []).append(f"{i.target}: {i.message}")
        if attempt == 1:
            for f in findings:
                fr = agents.fr_of(f["target"])
                if fr and f["severity"] in ("blocker", "major") and f["category"] in REPAIR_CATEGORIES:
                    targets.setdefault(fr, []).append(f"{f['target']}: {f['issue']} Исправление: {f['fix']}")
        if not targets:
            break
        problems = [p for ps in targets.values() for p in ps]
        patch = agents.repair_acceptance(llm, source, req, sorted(targets), problems, attempt)
        if patch is None:
            break
        # заменяем критерии только тех FR, для которых ремонт действительно вернул новый набор
        patched = {a.fr_id for a in patch.acceptance} & set(targets)
        set_acceptance(req, patch.acceptance, only_fr=patched)
        link_backrefs(req)
        fixes += gate.autofix(req)
        issues = gate.check(req)
        for f in findings:
            if agents.fr_of(f["target"]) in patched and f["status"] == "open":
                f["status"] = "repaired"

    # 5. трассировка бизнес-требований (BR-n) — по строкам исходного текста, без LLM
    uncovered = build_business(req, index)
    if uncovered:
        fixes.append(Issue("W_BR_UNCOVERED", "warning",
                           "пункты бизнес-требований без ссылок из модели: строки "
                           + ", ".join(map(str, uncovered[:30])) + (" …" if len(uncovered) > 30 else "")))

    # 6. артефакты и отчёт
    errors, warnings = split_issues(issues + fixes)
    passed = not errors
    artifacts = write_all(req, out)
    gate_dict = {"passed": passed,
                 "errors": [i.__dict__ for i in errors], "warnings": [i.__dict__ for i in warnings]}
    cov = gate.coverage(req)
    msum = llm.metrics.summary(since)
    (out / "critic.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "gate_report.json").write_text(
        json.dumps(gate_dict | {"coverage": cov, "metrics": msum}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "analysis-report.md").write_text(report_md(req, gate_dict, cov, msum, findings), encoding="utf-8")
    artifacts += ["normalized.md", "critic.json", "gate_report.json", "analysis-report.md"]

    return GateResult(
        stage=STAGE, passed=passed, errors=errors, warnings=warnings,
        tokens={"input": msum["input_tokens"], "output": msum["output_tokens"]},
        attempts=msum["llm_calls"], llm_calls=msum["llm_calls"],
        duration_s=round(time.monotonic() - t0, 2), artifacts=sorted(artifacts),
    )


def needs_answers(result: GateResult) -> bool:
    return any(i.code == "E_Q_OPEN" for i in result.errors)


__all__ = ["run", "needs_answers", "Issue"]
