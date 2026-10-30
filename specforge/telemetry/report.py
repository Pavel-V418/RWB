"""Отчёты о прогоне.

- `render_report` — краткий Markdown (CLI `status`/`report`, API GET /runs/{id}/report);
- `build_report(run_dir)` — артефакт прогона в runs/<id>/report/: report.md (сводка, таблица
  этапов, метрики, degraded и эскалации), report.json (метрики) и графики: токены по этапам
  стопкой и диаграмма Ганта этапов с параллельностью;
- `register_cli(app)` — команда `report <run_id>`.
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import matplotlib

matplotlib.use("Agg")  # без дисплея: графики только в файлы

import matplotlib.pyplot as plt
import typer
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter
from pydantic import BaseModel

from specforge.core.contracts import StageStatus
from specforge.core.events import Event, EventType
from specforge.llm.usage import UsageTracker
from specforge.orchestrator.nodes import PipelineSummary
from specforge.telemetry.metrics import (
    RunMetrics,
    compute_metrics,
    load_calls,
    load_events,
    load_summary,
)

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


# --- полный отчёт: runs/<id>/report/ ------------------------------------------------------------

REPORT_DIR = "report"
TOKENS_PNG = "tokens_by_stage.png"
GANTT_PNG = "stages_gantt.png"
STATUS_COLOR = {
    StageStatus.passed: "#2e7d32",
    StageStatus.degraded: "#f9a825",
    StageStatus.failed: "#c62828",
    StageStatus.skipped: "#9e9e9e",
}


class ReportFiles(BaseModel):
    markdown: Path
    json_path: Path
    tokens_png: Path
    gantt_png: Path
    metrics: RunMetrics


def build_report(run_dir: Path, *, tracker: UsageTracker | None = None) -> ReportFiles:
    """Отчёт прогона в runs/<id>/report/: report.md, report.json и два PNG.

    Данные — pipeline.json, события и llm_calls.jsonl прогона (см. telemetry.metrics).
    """
    summary = load_summary(run_dir)
    events = load_events(run_dir, summary.run_id)
    metrics = compute_metrics(summary, events=events, calls=load_calls(run_dir), tracker=tracker)
    out = run_dir / REPORT_DIR
    out.mkdir(parents=True, exist_ok=True)
    files = ReportFiles(
        markdown=out / "report.md",
        json_path=out / "report.json",
        tokens_png=out / TOKENS_PNG,
        gantt_png=out / GANTT_PNG,
        metrics=metrics,
    )
    plot_tokens(metrics, files.tokens_png)
    plot_gantt(metrics, files.gantt_png)
    files.json_path.write_text(
        json.dumps(
            {"metrics": metrics.model_dump(mode="json"), "profile": summary.profile.model_dump()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    files.markdown.write_text(render_markdown(summary, metrics), encoding="utf-8")
    return files


def render_markdown(summary: PipelineSummary, m: RunMetrics) -> str:
    counts = m.counts
    flags = [k for k, v in summary.profile.model_dump().items() if v is True]
    flags += [k for k, v in summary.profile.extra.items() if v]
    verdict = (
        "все этапы пройдены"
        if not counts.get("failed") and not counts.get("degraded")
        else f"с замечаниями: failed — {counts.get('failed', 0)}, "
        f"degraded — {counts.get('degraded', 0)}"
    )
    lines = [
        f"# Отчёт о прогоне `{m.run_id}`",
        "",
        "## Сводка",
        "",
        f"- **Итог:** {verdict}.",
        f"- **Этапы:** пройдено {counts.get('passed', 0)}, degraded {counts.get('degraded', 0)}, "
        f"failed {counts.get('failed', 0)}, пропущено {counts.get('skipped', 0)}.",
        f"- **Время прогона:** {_duration(m.wall_s)} (сумма этапов {_duration(m.stage_time_s)}).",
        f"- **Токены:** {_n(m.total_tokens)} — prompt {_n(m.prompt_tokens)}, "
        f"completion {_n(m.completion_tokens)}; из кэша {_n(m.cached_tokens)} "
        f"({m.cache_hit_rate:.0%} prompt).",
        f"- **Вызовы LLM:** {m.llm_calls}, повторы {m.retries}; ремонты {m.repairs}.",
        f"- **Профиль кейса:** {', '.join(flags) or 'флаги не заданы'}.",
        "",
        "## Этапы",
        "",
        "| Этап | Статус | Попытки | Шлюз | Ошибки до → после | Prompt | Completion "
        "| Кэш | Вызовы | Время |",
        "|---|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for s in m.stages:
        name = s.agent + (" ⚙" if s.llm_free else "") + (" ♻" if s.from_cache else "")
        errors = "—" if s.errors_before is None else f"{s.errors_before} → {s.errors_after}"
        lines.append(
            f"| {name} | {STATUS_MARK[s.status]} {s.status.value} | {s.attempts} | {s.gate} "
            f"| {errors} | {_n(s.prompt_tokens)} | {_n(s.completion_tokens)} "
            f"| {s.cache_hit_rate:.0%} | {s.llm_calls} | {_duration(s.wall_s)} |"
        )
    lines += [
        "",
        "⚙ — детерминированный этап (llm_free), ♻ — результат из кэша этапов. "
        "Попытки = 1 + ремонты.",
        "",
        f"![Токены по этапам]({TOKENS_PNG})",
        "",
        f"![Этапы во времени]({GANTT_PNG})",
        "",
        "## Итоговые метрики",
        "",
        "| Метрика | Значение |",
        "|---|---:|",
        f"| Токены prompt / completion / из кэша | {_n(m.prompt_tokens)} / "
        f"{_n(m.completion_tokens)} / {_n(m.cached_tokens)} |",
        f"| Cache hit rate | {m.cache_hit_rate:.1%} |",
        f"| Вызовы LLM / повторы | {m.llm_calls} / {m.retries} |",
        f"| Время прогона / сумма этапов | {_duration(m.wall_s)} / {_duration(m.stage_time_s)} |",
        f"| Попытки / ремонты | {m.attempts} / {m.repairs} |",
        f"| Шлюзы зелёные / красные | {m.gates_passed} / {m.gates_failed} |",
        f"| Ошибки шлюзов до → после ремонта | {m.errors_before} → {m.errors_after} |",
        f"| Доля llm_free-этапов | {m.llm_free_share:.0%} |",
    ]
    degraded = [s for s in m.stages if s.status in (StageStatus.degraded, StageStatus.failed)]
    lines += ["", "## Degraded-этапы и эскалации", ""]
    if not degraded and not m.escalations:
        lines.append("Нет: все запущенные этапы прошли шлюз.")
    for s in degraded:
        lines.append(
            f"- **{s.agent}** — {s.status.value}, попыток {s.attempts}, "
            f"ошибок осталось {s.errors_after}. {_one_line(s.summary)}"
        )
        result = summary.stages[s.agent]
        if result.gate is not None:
            for d in result.gate.diagnostics[:3]:
                where = ":".join(str(x) for x in (d.file, d.line) if x)
                lines.append(f"  - `{d.tool}` {where} {_one_line(d.message)}")
    if m.escalations:
        lines += ["", "Эскалации:", ""]
        lines += [f"- {_one_line(e)}" for e in m.escalations]
    return "\n".join(lines) + "\n"


def plot_tokens(m: RunMetrics, path: Path) -> None:
    """Токены по этапам стопкой: prompt из кэша, prompt без кэша, completion."""
    stages = [s for s in m.stages if s.status != StageStatus.skipped] or m.stages
    names = [s.agent for s in stages]
    cached = [s.cached_tokens for s in stages]
    fresh = [s.prompt_tokens - s.cached_tokens for s in stages]
    completion = [s.completion_tokens for s in stages]
    fig, ax = plt.subplots(figsize=(max(6.0, 0.9 * len(names) + 2), 4.5))
    ax.bar(names, cached, label="prompt из кэша", color="#90caf9")
    ax.bar(names, fresh, bottom=cached, label="prompt без кэша", color="#1e88e5")
    bottom = [c + f for c, f in zip(cached, fresh, strict=True)]
    ax.bar(names, completion, bottom=bottom, label="completion", color="#fb8c00")
    ax.set_title(f"Токены по этапам — прогон {m.run_id}")
    ax.set_ylabel("токены")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _n(int(v))))
    ax.legend()
    ax.tick_params(axis="x", rotation=30)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def plot_gantt(m: RunMetrics, path: Path) -> None:
    """Этапы во времени: параллельные этапы — перекрывающиеся полосы."""
    stages = [s for s in m.stages if s.start_s is not None]
    fig, ax = plt.subplots(figsize=(9, max(2.5, 0.5 * len(stages) + 1.5)))
    for row, s in enumerate(stages):
        assert s.start_s is not None
        width = max(s.wall_s, m.wall_s * 0.004)  # пропущенные этапы видны тонкой чертой
        ax.barh(row, width, left=s.start_s, color=STATUS_COLOR[s.status], edgecolor="black")
        label = f"{s.attempts} поп." if s.attempts > 1 else ""
        if label:
            ax.text(s.start_s + width, row, f" {label}", va="center", fontsize=8)
    ax.set_yticks(range(len(stages)), [s.agent for s in stages])
    ax.invert_yaxis()
    ax.set_xlabel("секунды от начала прогона")
    ax.set_title(f"Этапы во времени — прогон {m.run_id}")
    ax.grid(axis="x", alpha=0.3)
    handles = [
        Patch(color=STATUS_COLOR[st], label=st.value)
        for st in StageStatus
        if any(s.status == st for s in stages)
    ]
    ax.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.18),
        ncol=len(handles) or 1,
        frameon=False,
    )
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _n(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} с"
    minutes, rest = divmod(int(seconds), 60)
    return (
        f"{minutes} мин {rest:02d} с" if minutes < 60 else f"{minutes // 60} ч {minutes % 60} мин"
    )


def _one_line(text: str) -> str:
    return " ".join(text.split())[:200].replace("|", "\\|")


def register_cli(app: typer.Typer) -> None:
    """Команда `report <run_id>`: отчёт в runs/<run_id>/report/."""

    @app.command("report")
    def report_command(
        run_id: Annotated[str, typer.Argument(help="Идентификатор запуска.")],
        runs_dir: Annotated[
            Path, typer.Option(help="Каталог запусков.", envvar="SPECFORGE_RUNS_DIR")
        ] = Path("runs"),
    ) -> None:
        """Отчёт о прогоне: report.md, report.json и графики в runs/<run_id>/report/."""
        try:
            files = build_report(runs_dir / run_id)
        except FileNotFoundError as exc:
            typer.echo(f"Ошибка: {exc}", err=True)
            raise typer.Exit(2) from exc
        typer.echo(f"Отчёт: {files.markdown}")
