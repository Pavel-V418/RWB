"""Сравнение прогона с baseline (bench/baseline/report.json): блок «до/после» отчёта.

Сравниваются токены (prompt / completion / cached), cache hit rate, dedup_hits, время и попытки —
в сумме и по этапам, с изменением в процентах. Ухудшение качества по этапу — статус хуже
(passed → degraded → failed), шлюз стал красным или попыток стало больше — выделяется отдельно:
экономия токенов не должна покупаться качеством.
"""

import json
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from specforge.core.contracts import StageStatus
from specforge.paths import PROJECT_ROOT
from specforge.telemetry.metrics import RunMetrics, StageMetrics

BASELINE_DIR = PROJECT_ROOT / "bench" / "baseline"
REPORT_JSON = "report.json"
STATUS_RANK = {
    StageStatus.passed: 0,
    StageStatus.degraded: 1,
    StageStatus.failed: 2,
    StageStatus.skipped: -1,
}
"""Чем больше, тем хуже; skipped в сравнении качества не участвует."""


class Delta(BaseModel):
    before: float
    after: float

    @property
    def change_pct(self) -> float | None:
        if self.before == 0:
            return None if self.after == 0 else float("inf")
        return (self.after - self.before) / self.before * 100


class StageComparison(BaseModel):
    agent: str
    before_status: StageStatus | None = None
    after_status: StageStatus | None = None
    tokens: Delta
    cached_tokens: Delta
    wall_s: Delta
    attempts: Delta
    regressions: list[str] = Field(default_factory=list)


class Comparison(BaseModel):
    baseline_run_id: str
    totals: dict[str, Delta]
    stages: list[StageComparison]

    @property
    def regressions(self) -> list[str]:
        return [f"{s.agent}: {r}" for s in self.stages for r in s.regressions]


TOTAL_FIELDS = {
    "prompt_tokens": "Токены prompt",
    "completion_tokens": "Токены completion",
    "cached_tokens": "Токены из кэша (cached)",
    "cache_hit_rate": "Cache hit rate",
    "dedup_hits": "Ответы из кэша дедупликации",
    "llm_calls": "Вызовы LLM",
    "wall_s": "Время прогона, с",
    "attempts": "Попытки (1 + ремонты)",
}


def load_baseline(directory: Path = BASELINE_DIR) -> RunMetrics | None:
    path = directory / REPORT_JSON
    if not path.is_file():
        return None
    return RunMetrics.model_validate(json.loads(path.read_text(encoding="utf-8"))["metrics"])


def compare(before: RunMetrics, after: RunMetrics) -> Comparison:
    totals = {
        name: Delta(before=float(getattr(before, name)), after=float(getattr(after, name)))
        for name in TOTAL_FIELDS
    }
    old = {s.agent: s for s in before.stages}
    new = {s.agent: s for s in after.stages}
    stages = [_stage(name, old.get(name), new.get(name)) for name in dict.fromkeys([*old, *new])]
    return Comparison(baseline_run_id=before.run_id, totals=totals, stages=stages)


def _stage(agent: str, before: StageMetrics | None, after: StageMetrics | None) -> StageComparison:
    def value(m: StageMetrics | None, field: str) -> float:
        if m is None:
            return 0.0
        return float(m.total_tokens if field == "tokens" else getattr(m, field))

    result = StageComparison(
        agent=agent,
        before_status=before.status if before else None,
        after_status=after.status if after else None,
        tokens=Delta(before=value(before, "tokens"), after=value(after, "tokens")),
        cached_tokens=Delta(
            before=value(before, "cached_tokens"), after=value(after, "cached_tokens")
        ),
        wall_s=Delta(before=value(before, "wall_s"), after=value(after, "wall_s")),
        attempts=Delta(before=value(before, "attempts"), after=value(after, "attempts")),
    )
    if before is None or after is None:
        if before is not None:
            result.regressions.append("этап пропал из прогона")
        return result
    if STATUS_RANK[after.status] > STATUS_RANK[before.status] >= 0:
        result.regressions.append(f"статус {before.status.value} → {after.status.value}")
    if before.gate == "passed" and after.gate == "failed":
        result.regressions.append("шлюз стал красным")
    if after.attempts > before.attempts and after.status != StageStatus.skipped:
        result.regressions.append(f"попыток {before.attempts} → {after.attempts}")
    return result


def render_comparison(c: Comparison) -> str:
    lines = [
        f"## До / после (baseline `{c.baseline_run_id}`)",
        "",
        "| Метрика | До | После | Изменение |",
        "|---|---:|---:|---:|",
    ]
    for name, title in TOTAL_FIELDS.items():
        d = c.totals[name]
        fmt = _pct if name == "cache_hit_rate" else _num
        lines.append(f"| {title} | {fmt(d.before)} | {fmt(d.after)} | {_change(d)} |")
    lines += [
        "",
        "| Этап | Статус до → после | Попытки | Токены | Из кэша | Время, с |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for s in c.stages:
        mark = " ⚠" if s.regressions else ""
        status = f"{_status(s.before_status)} → {_status(s.after_status)}"
        lines.append(
            f"| {s.agent}{mark} | {status} | {_num(s.attempts.before)} → {_num(s.attempts.after)} "
            f"| {_num(s.tokens.before)} → {_num(s.tokens.after)} ({_change(s.tokens)}) "
            f"| {_num(s.cached_tokens.before)} → {_num(s.cached_tokens.after)} "
            f"| {s.wall_s.before:.1f} → {s.wall_s.after:.1f} ({_change(s.wall_s)}) |"
        )
    lines.append("")
    if c.regressions:
        lines += ["### ⚠ Стало хуже по качеству", ""]
        lines += [f"- **{r}**" for r in c.regressions]
    else:
        lines.append("Ухудшений по качеству нет: статусы, шлюзы и число попыток не хуже baseline.")
    return "\n".join(lines) + "\n"


def save_baseline(report_dir: Path, directory: Path = BASELINE_DIR) -> Path:
    """Скопировать runs/<id>/report → bench/baseline/ (целиком, с заменой)."""
    if not (report_dir / REPORT_JSON).is_file():
        raise FileNotFoundError(f"нет {report_dir / REPORT_JSON} — сначала specforge report")
    if directory.exists():
        shutil.rmtree(directory)
    shutil.copytree(report_dir, directory)
    return directory


def _status(status: StageStatus | None) -> str:
    return status.value if status is not None else "—"


def _num(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") if value == int(value) else f"{value:.1f}"


def _pct(value: float) -> str:
    return f"{value:.1%}"


def _change(d: Delta) -> str:
    pct = d.change_pct
    if pct is None:
        return "0%"
    if pct == float("inf"):
        return "новое"
    return f"{pct:+.1f}%"
