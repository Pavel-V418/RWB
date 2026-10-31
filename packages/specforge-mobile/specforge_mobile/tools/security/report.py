"""
Выходы security-контура (папка тестов артефактов, по договорённости с участником 2):
  security.json  — {"findings": [...]} в формате team_inputs участника 2 (с полем req)
  findings.json  — полные находки + сводка (для повторного скана: baseline)
  report.md      — отчёт для людей: итоговый статус, таблица по SEC-требованиям, блокирующие, исправленные
Сырые SARIF кладутся в raw/ — участник 2 читает только *.sarif верхнего уровня, дублей не будет.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .findings import SEVERITIES, Finding

# (основные, вспомогательные) проверки категории. «Сканер не нашёл» доказывает требование только для
# основных; для authn/authz/rate_limiting нужен функциональный тест (контракт), для audit — e2e.
CHECKS = {
    "secrets": (["gitleaks"], ["semgrep"]),
    "input_validation": (["semgrep", "zap", "contract"], []),
    "authz": (["contract"], ["zap", "semgrep"]),
    "authn": (["contract"], ["zap", "semgrep"]),
    "privacy": (["semgrep"], ["zap"]),
    "rate_limiting": (["contract"], ["zap"]),
    "audit": (["e2e"], []),
    "supply_chain": (["trivy", "trivy-image"], []),
}


def merge_baseline(findings: list[Finding], baseline: Path | None) -> int:
    """Находки прошлого скана, которых больше нет, → status=fixed. Возвращает их число."""
    if not baseline or not baseline.is_file():
        return 0
    prev = json.loads(baseline.read_text(encoding="utf-8")).get("findings", [])
    now = {f.id for f in findings}
    fixed = 0
    for p in prev:
        if p.get("id") not in now and p.get("status") == "open":
            f = Finding(**{k: v for k, v in p.items() if k in Finding.__dataclass_fields__})
            f.status, f.blocking = "fixed", False
            f.verdict_reason = "исправлено: при повторном скане не найдено"
            findings.append(f)
            fixed += 1
    return fixed


def summary(findings: list[Finding]) -> dict:
    open_ = [f for f in findings if f.status == "open"]
    return {
        "total": len(findings),
        "open": len(open_),
        "blocking": sum(f.blocking for f in findings),
        "fixed": sum(f.status == "fixed" for f in findings),
        "false_positive": sum(f.status == "false_positive" for f in findings),
        "bySeverity": {s: sum(f.severity == s for f in open_) for s in SEVERITIES},
        "byTool": dict(Counter(f.tool for f in findings)),
        "status": "FAILED" if any(f.blocking for f in findings) else "PASSED",
    }


def write_outputs(out: Path, findings: list[Finding], sec_reqs: list[dict], tools_run: list[str],
                  extra: dict | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    findings.sort(key=lambda f: (not f.blocking, f.status != "open", SEVERITIES.index(f.severity), f.tool))
    s = summary(findings)
    meta = {"generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tools": tools_run,
            **(extra or {})}
    (out / "findings.json").write_text(json.dumps({"summary": s, "meta": meta,
                                                   "findings": [f.to_dict() for f in findings]},
                                                  ensure_ascii=False, indent=2), encoding="utf-8")
    team = [{"tool": f.tool, "id": f.rule, "rule": f.rule, "title": f.title, "severity": f.severity,
             "status": f.status, "location": f.location, "cve": f.cve, "req": f.req, "category": f.category,
             "fingerprint": f.id, "blocking": f.blocking, "reason": f.verdict_reason, "fix": f.fix}
            for f in findings]
    (out / "security.json").write_text(json.dumps({"summary": s, "findings": team}, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    (out / "report.md").write_text(render_md(findings, s, sec_reqs, tools_run, meta), encoding="utf-8")
    return s


def _req_rows(findings: list[Finding], sec_reqs: list[dict], tools_run: list[str]) -> list[str]:
    ran = {t.split(":")[0] for t in tools_run}
    rows = []
    for r in sec_reqs:
        cat = str(r.get("category", "")).lower()
        mine = [f for f in findings if r["id"] in f.req]
        open_ = [f for f in mine if f.status == "open"]
        primary, aux = CHECKS.get(cat, ([], []))
        main_ran, aux_ran = [c for c in primary if c in ran], [c for c in aux if c in ran]
        checks = main_ran + aux_ran
        if open_:
            state = f"❌ открыто: {len(open_)}" + (f" (блокирует {sum(f.blocking for f in open_)})"
                                                   if any(f.blocking for f in open_) else "")
        elif main_ran:
            state = "✅ нарушений не найдено" + (f", исправлено {sum(f.status == 'fixed' for f in mine)}"
                                                 if any(f.status == "fixed" for f in mine) else "")
        elif aux_ran:
            state = f"⚠️ сканеры чисто, нужна проверка: {', '.join(primary)}"
        else:
            state = "⚪ автоматически не проверено" + (f" (нужно: {', '.join(primary)})" if primary else "")
        rows.append(f"| {r['id']} | {cat} | {str(r.get('statement', ''))[:90]} | {', '.join(checks) or '—'} | {state} |")
    return rows


def render_md(findings: list[Finding], s: dict, sec_reqs: list[dict], tools_run: list[str], meta: dict) -> str:
    icon = "✅" if s["status"] == "PASSED" else "❌"
    lines = [
        "# Отчёт безопасности", "",
        f"**Итоговый статус: {icon} {s['status']}** — блокирующих находок: {s['blocking']}.", "",
        f"Сгенерировано: {meta['generatedAt']}. Сканеры: {', '.join(tools_run) or 'нет'}.", "",
        "| Всего | Открыто | Блокирует | Исправлено | Ложные | Critical | High | Medium | Low |",
        "|---|---|---|---|---|---|---|---|---|",
        f"| {s['total']} | {s['open']} | {s['blocking']} | {s['fixed']} | {s['false_positive']} | "
        f"{s['bySeverity']['critical']} | {s['bySeverity']['high']} | {s['bySeverity']['medium']} | "
        f"{s['bySeverity']['low']} |", "",
        "Политика: блокируют секреты (gitleaks), уязвимости образов HIGH+ (Trivy image) и находки high+, "
        "подтверждённые триажем. Semgrep, ZAP и Trivy fs до триажа только информируют.", "",
    ]
    if sec_reqs:
        lines += ["## По требованиям безопасности", "",
                  "| Требование | Категория | Формулировка | Чем проверено | Результат |", "|---|---|---|---|---|",
                  *_req_rows(findings, sec_reqs, tools_run), ""]
        unmapped = [f for f in findings if f.status == "open" and not f.req]
        if unmapped:
            lines += [f"Открытых находок без требования: {len(unmapped)} "
                      f"(категории: {', '.join(sorted({f.category or 'без категории' for f in unmapped}))}).", ""]

    def table(title: str, items: list[Finding], limit: int = 40) -> list[str]:
        if not items:
            return []
        out = [f"## {title} ({len(items)})", "", "| Критичность | Сканер | Правило | Где | Требование | Вывод | Как исправить |",
               "|---|---|---|---|---|---|---|"]
        for f in items[:limit]:
            out.append(f"| {f.severity} | {f.tool} | {f.cve or f.rule} | `{f.location}` | {', '.join(f.req) or '—'} | "
                       f"{(f.verdict_reason or f.title)[:120]} | {f.fix[:100] or '—'} |".replace("\n", " "))
        if len(items) > limit:
            out.append(f"| … | | ещё {len(items) - limit} — см. findings.json | | | | |")
        return out + [""]

    lines += table("Блокирующие", [f for f in findings if f.blocking])
    lines += table("Открытые (не блокируют)", [f for f in findings if f.status == "open" and not f.blocking])
    lines += table("Исправлено после повторного скана", [f for f in findings if f.status == "fixed"])
    lines += table("Ложные срабатывания и принятые", [f for f in findings if f.status in ("false_positive", "accepted")], 20)
    return "\n".join(lines)
