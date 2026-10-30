"""Отчёт о запуске в Markdown: итог этапов, шлюзы, ремонты, предупреждения бюджета."""

from collections.abc import Sequence

from specforge.core.contracts import StageStatus
from specforge.core.events import Event, EventType
from specforge.orchestrator.nodes import PipelineSummary

STATUS_MARK = {
    StageStatus.passed: "✅",
    StageStatus.degraded: "⚠️",
    StageStatus.failed: "❌",
    StageStatus.skipped: "⏭",
}


def render_report(summary: PipelineSummary, events: Sequence[Event] = ()) -> str:
    counts = ", ".join(f"{k}: {v}" for k, v in summary.counts.items() if v)
    lines = [
        f"# Запуск {summary.run_id}",
        "",
        f"Этапы — {counts or 'нет'}. Токены: {summary.usage.total_tokens} "
        f"({summary.usage.calls} вызовов LLM), время этапов: {summary.active_s:.0f} с.",
        "",
        "| Этап | Статус | Попытки | Токены | Время, с | Итог |",
        "|---|---|---|---|---|---|",
    ]
    for name, result in summary.stages.items():
        text = result.summary.replace("\n", " ").replace("|", "\\|")[:160]
        lines.append(
            f"| {name} | {STATUS_MARK[result.status]} {result.status.value} | {result.attempts} "
            f"| {result.usage.total_tokens} | {result.duration_s:.1f} | {text} |"
        )

    failing = [(name, r) for name, r in summary.stages.items() if r.gate and not r.gate.passed]
    if failing:
        lines += ["", "## Красные шлюзы", ""]
        for name, result in failing:
            assert result.gate is not None
            lines.append(f"### {name} — ошибок: {result.gate.total_errors}")
            for d in result.gate.diagnostics[:5]:
                where = ":".join(str(x) for x in (d.file, d.line, d.column) if x)
                lines.append(f"- `{d.tool}` {where} {d.message.splitlines()[0][:200]}")

    notable = {
        EventType.repair_attempt,
        EventType.budget_warning,
        EventType.approval_required,
        "escalation",
        "artifacts_edited",
    }
    picked = [e for e in events if e.kind in notable]
    if picked:
        lines += ["", "## События", ""]
        for e in picked:
            text = e.message or ", ".join(f"{k}={v}" for k, v in e.data.items())
            lines.append(f"- {e.ts:%H:%M:%S} `{e.kind}` {e.agent or ''} {text}".rstrip())
    return "\n".join(lines) + "\n"
