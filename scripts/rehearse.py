"""Репетиция: конвейер на всех ТЗ из examples/ → rehearsal.md.

- ТЗ — файлы .md/.txt/.pdf/.docx в examples/ и в его подкаталогах первого уровня
  (examples/case1/tz.md; каталоги данных внутри не обходятся); README.md и «_*» пропускаются;
  каталог data/ рядом с ТЗ кейса передаётся прогону как `--data`;
- каждое ТЗ — отдельный `specforge run --autopilot` (подпроцесс, run_id rehearse-<время>-<ТЗ>),
  вывод — в runs/rehearse-logs/<run_id>.log;
- таблица «этап × ТЗ → статус, попытки, токены, время» — из pipeline.json и метрик прогона;
- топ-10 нормализованных ошибок шлюзов (сигнатура fix_memory: инструмент, код, шаблон сообщения)
  по всем попыткам — из событий gate_failed / repair_attempt (поле diagnostics) и итоговых шлюзов;
  у каждой — примеры и исправление: на уровне платформы (парсер, промпт ремонтника, инструмент,
  профиль) или в каком агенте команды проблема и что поменять (config/rehearse_rules.yaml,
  иначе эвристики). LLM не используется.

    uv run python scripts/rehearse.py [--examples examples] [--out rehearsal.md] [-- --mobile ...]
    uv run python scripts/rehearse.py --from-runs <run_id> [<run_id> ...]   # только отчёт

Аргументы после `--` передаются каждому `specforge run`.
Код выхода: 0 — все прогоны без failed-этапов, 1 — есть failed, 2 — нет ТЗ.
"""

import argparse
import os
import re
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from specforge.core.contracts import Diagnostic, StageStatus  # noqa: E402
from specforge.core.events import Event, EventType  # noqa: E402
from specforge.repair.fix_memory import ErrorSignature, signature_of  # noqa: E402
from specforge.telemetry.metrics import (  # noqa: E402
    RunMetrics,
    compute_metrics,
    load_calls,
    load_events,
    load_summary,
)
from specforge.tools.docs import SUPPORTED_SUFFIXES  # noqa: E402

TOP_N = 10
EXAMPLES_PER_ERROR = 2
RULES_FILE = ROOT / "config" / "rehearse_rules.yaml"
UNFIXED_SHARE = 0.5
"""Ремонт не справился в такой доле этапов → проблема в промпте ремонтника."""
COUNT_ORDER = ("passed", "degraded", "failed", "skipped")
ICONS = {"passed": "✔", "degraded": "⚠", "failed": "✘", "skipped": "⏭"}


class Case(BaseModel):
    name: str
    path: Path
    data: Path | None = None
    """Каталог data/ рядом с ТЗ в подкаталоге кейса → `specforge run --data`."""


class CaseRun(BaseModel):
    case: str
    run_id: str
    exit_code: int | None = None
    wall_s: float = 0.0
    error: str | None = None
    """Прогон не оставил pipeline.json или упал до старта."""


@dataclass
class Example:
    case: str
    agent: str
    where: str
    message: str


@dataclass
class ErrorStat:
    signature: ErrorSignature
    hits: int = 0
    """В скольких запусках шлюза (по всем попыткам) встретилась."""
    stages: set[tuple[str, str]] = field(default_factory=set)
    """(ТЗ, агент)."""
    unfixed: set[tuple[str, str]] = field(default_factory=set)
    """Осталась в итоговом красном шлюзе этапа — ремонт не починил."""
    agents: Counter[str] = field(default_factory=Counter)
    examples: list[Example] = field(default_factory=list)

    @property
    def cases(self) -> set[str]:
        return {case for case, _ in self.stages}


class Remedy(BaseModel):
    level: Literal["platform", "agent"]
    where: str
    fix: str


class Rule(BaseModel):
    match: dict[Literal["tool", "code", "pattern"], str]
    level: Literal["platform", "agent"]
    where: str
    fix: str

    def matches(self, sig: ErrorSignature) -> bool:
        fields = {"tool": sig.tool, "code": sig.code, "pattern": sig.pattern}
        return all(re.search(rx, fields[key], re.IGNORECASE) for key, rx in self.match.items())


# --- ТЗ и запуск ---------------------------------------------------------------------------------


def discover_cases(examples: Path) -> list[Case]:
    found: list[Path] = []
    if examples.is_dir():
        for item in sorted(examples.iterdir()):
            candidates = sorted(item.iterdir()) if item.is_dir() else [item]
            found += [p for p in candidates if p.is_file() and _is_tz(p)]
    names = Counter(_slug(p.stem) for p in found)
    return [
        Case(
            name=_slug(p.stem) if names[_slug(p.stem)] == 1 else _slug(f"{p.parent.name}-{p.stem}"),
            path=p,
            data=p.parent / "data"
            if p.parent != examples and (p.parent / "data").is_dir()
            else None,
        )
        for p in found
    ]


def _is_tz(path: Path) -> bool:
    return (
        path.suffix.lower() in SUPPORTED_SUFFIXES
        and not path.name.startswith("_")
        and path.stem.lower() != "readme"
    )


def _slug(text: str) -> str:
    return re.sub(r"[^\w-]+", "-", text, flags=re.UNICODE).strip("-").lower() or "tz"


Runner = Callable[[Case, str, Path, Sequence[str]], int]
"""(ТЗ, run_id, каталог запусков, доп. аргументы) → код выхода `specforge run`."""


def subprocess_runner(case: Case, run_id: str, runs_dir: Path, extra: Sequence[str]) -> int:
    exe = Path(sys.executable).parent / ("specforge.exe" if os.name == "nt" else "specforge")
    cmd = [str(exe)] if exe.is_file() else [sys.executable, "-m", "specforge.cli"]
    cmd += ["run", str(case.path), "--run-id", run_id, "--autopilot", *extra]
    if case.data is not None:
        cmd += ["--data", str(case.data)]
    logs = runs_dir / "rehearse-logs"
    logs.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "SPECFORGE_RUNS_DIR": str(runs_dir)}
    with (logs / f"{run_id}.log").open("w", encoding="utf-8") as out:
        return subprocess.run(
            cmd, check=False, stdout=out, stderr=subprocess.STDOUT, env=env, cwd=ROOT
        ).returncode


def run_cases(
    cases: Sequence[Case],
    runs_dir: Path,
    *,
    runner: Runner = subprocess_runner,
    extra: Sequence[str] = (),
    stamp: str | None = None,
) -> list[CaseRun]:
    stamp = stamp or f"{datetime.now(UTC):%Y%m%d-%H%M%S}"
    done: list[CaseRun] = []
    for case in cases:
        run_id = f"rehearse-{stamp}-{case.name}"
        print(f"== {case.name}: {case.path} → {run_id}", flush=True)
        started = time.monotonic()
        try:
            code: int | None = runner(case, run_id, runs_dir, extra)
            error = None
        except Exception as exc:  # один упавший прогон не останавливает репетицию
            code, error = None, f"{type(exc).__name__}: {exc}"
        if error is None and not (runs_dir / run_id / "pipeline.json").is_file():
            error = f"нет pipeline.json (код {code}), см. rehearse-logs/{run_id}.log"
        done.append(
            CaseRun(
                case=case.name,
                run_id=run_id,
                exit_code=code,
                wall_s=time.monotonic() - started,
                error=error,
            )
        )
        print(f"   код {code}, {_duration(done[-1].wall_s)}" + (f" — {error}" if error else ""))
    return done


# --- анализ --------------------------------------------------------------------------------------


Snapshot = tuple[str, list[Diagnostic], bool]
"""Красный шлюз: (агент, диагностики, итоговый ли это шлюз этапа)."""


class RunData(BaseModel):
    run: CaseRun
    metrics: RunMetrics | None = None


def load_run(run: CaseRun, runs_dir: Path) -> tuple[RunData, list[Snapshot]]:
    """Метрики прогона и снимки красных шлюзов: (агент, диагностики, итоговый ли шлюз)."""
    run_dir = runs_dir / run.run_id
    try:
        summary = load_summary(run_dir)
    except (FileNotFoundError, ValueError) as exc:
        failed = run.model_copy(update={"error": run.error or str(exc)})
        return RunData(run=failed), []
    events = load_events(run_dir, run.run_id)
    metrics = compute_metrics(summary, events=events, calls=load_calls(run_dir))

    snapshots = gate_snapshots(events)
    seen = {agent for agent, _, _ in snapshots}
    for agent, result in summary.stages.items():
        gate = result.gate
        if gate is None or gate.passed or not gate.diagnostics:
            continue
        # итоговый шлюз после последнего ремонта в события не попадает; без ремонта и с
        # событиями он совпадает с gate_failed — тогда только помечаем его итоговым
        if result.attempts > 1 or agent not in seen:
            snapshots.append((agent, list(gate.diagnostics), True))
        else:
            last = max(i for i, s in enumerate(snapshots) if s[0] == agent)
            snapshots[last] = (agent, snapshots[last][1], True)
    return RunData(run=run, metrics=metrics), snapshots


def gate_snapshots(events: Sequence[Event]) -> list[Snapshot]:
    """gate_failed — шлюз до ремонта (и после инфраструктурного повтора); repair_attempt с
    attempt ≥ 2 — шлюз после предыдущего ремонта (attempt 1 совпадает с gate_failed)."""
    found: list[Snapshot] = []
    for e in events:
        if not e.agent:
            continue
        first_repair = e.kind == EventType.repair_attempt and e.data.get("attempt") == 1
        if e.kind not in (EventType.gate_failed, EventType.repair_attempt) or first_repair:
            continue
        raw = e.data.get("diagnostics")
        if isinstance(raw, list) and raw:
            diags = [Diagnostic.model_validate(d) for d in raw if isinstance(d, dict)]
            found.append((e.agent, diags, False))
    return found


def collect_errors(
    runs: Sequence[tuple[RunData, list[Snapshot]]],
) -> list[ErrorStat]:
    stats: dict[ErrorSignature, ErrorStat] = {}
    for data, snapshots in runs:
        case = data.run.case
        for agent, diagnostics, final in snapshots:
            in_snapshot: set[ErrorSignature] = set()
            for d in diagnostics:
                if d.severity != "error":
                    continue
                sig = signature_of(d)
                stat = stats.setdefault(sig, ErrorStat(signature=sig))
                if final:
                    stat.unfixed.add((case, agent))
                if sig in in_snapshot:
                    continue
                in_snapshot.add(sig)
                stat.hits += 1
                stat.agents[agent] += 1
                stat.stages.add((case, agent))
                if len(stat.examples) < EXAMPLES_PER_ERROR and not any(
                    x.case == case and x.agent == agent for x in stat.examples
                ):
                    stat.examples.append(_example(case, agent, d))
    return sorted(
        stats.values(),
        key=lambda s: (
            -s.hits,
            -len(s.cases),
            -len(s.stages),
            s.signature.tool,
            s.signature.pattern,
        ),
    )


def _example(case: str, agent: str, d: Diagnostic) -> Example:
    where = d.file or "—"
    if d.file and d.line:
        where += f":{d.line}"
    return Example(case=case, agent=agent, where=where, message=_one_line(d.message, 200))


# --- исправления ---------------------------------------------------------------------------------


def load_rules(path: Path = RULES_FILE) -> list[Rule]:
    if not path.is_file():
        return []
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [Rule.model_validate(r) for r in raw.get("rules", [])]


def remedy_for(stat: ErrorStat, rules: Sequence[Rule]) -> Remedy:
    agents = ", ".join(f"`{a}` (agents/{a}/prompts/)" for a, _ in stat.agents.most_common(3))
    for rule in rules:
        if rule.matches(stat.signature):
            return Remedy(level=rule.level, where=rule.where.format(agents=agents), fix=rule.fix)
    sig = stat.signature
    if not sig.code and not sig.pattern:
        return Remedy(
            level="platform",
            where="парсер",
            fix="Пустое сообщение без кода — парсер не извлёк текст ошибки; проверить разбор.",
        )
    unfixed = len(stat.unfixed) / len(stat.stages) if stat.stages else 0.0
    if unfixed >= UNFIXED_SHARE:
        return Remedy(
            level="platform",
            where="промпт ремонтника",
            fix=(
                f"Ремонт не справился в {len(stat.unfixed)} из {len(stat.stages)} этапов: добавить "
                "приём для этой ошибки в specforge/repair/prompts/system.md, проверить, что "
                "code_window доходит до ремонтника; удачное исправление — в fix_memory."
            ),
        )
    if len(stat.agents) >= 2:
        return Remedy(
            level="platform",
            where="промпт ремонтника",
            fix=(
                f"Ошибка у {len(stat.agents)} агентов — типовое исправление в fix_memory и "
                "specforge/repair/prompts/system.md; общее правило — в docs/AGENT_SDK.md."
            ),
        )
    return Remedy(
        level="agent",
        where=agents,
        fix=(
            "Ремонт чинит, но тратит попытки и токены: добавить в системный промпт агента правило "
            "или пример, предотвращающий ошибку при генерации."
        ),
    )


# --- отчёт ---------------------------------------------------------------------------------------


def render(
    runs: Sequence[RunData],
    errors: Sequence[ErrorStat],
    rules: Sequence[Rule],
    *,
    top: int = TOP_N,
) -> str:
    lines = [
        "# Репетиция конвейера",
        "",
        f"{datetime.now(UTC):%Y-%m-%d %H:%M} UTC · ТЗ: {len(runs)}",
        "",
        "## Прогоны",
        "",
        "| ТЗ | run_id | код | время | passed / degraded / failed / skipped | токены |",
        "|---|---|---|---|---|---|",
    ]
    for r in runs:
        m = r.metrics
        counts = r.run.error or "—"
        if m:
            counts = " / ".join(str(m.counts.get(s, 0)) for s in COUNT_ORDER)
        tokens = _tokens(m.total_tokens) if m else "—"
        code = "—" if r.run.exit_code is None else str(r.run.exit_code)
        lines.append(
            f"| {r.run.case} | `{r.run.run_id}` | {code} | {_duration(r.run.wall_s)} "
            f"| {counts} | {tokens} |"
        )

    lines += ["", "## Этап × ТЗ", "", "Ячейка: статус · попытки · токены · время.", ""]
    lines += stage_table(runs)

    lines += ["", f"## Топ-{top} ошибок шлюзов", ""]
    if not errors:
        lines.append("Красных шлюзов не было.")
    for i, stat in enumerate(errors[:top], 1):
        sig = stat.signature
        remedy = remedy_for(stat, rules)
        level = "платформа" if remedy.level == "platform" else "агент команды"
        lines += [
            f"### {i}. `{sig.tool}` {sig.code or '(без кода)'}: {sig.pattern or '—'}",
            "",
            f"- Встречается: {stat.hits} раз(а) в шлюзах, ТЗ {len(stat.cases)}, этапов "
            f"{len(stat.stages)}; ремонт не починил: {len(stat.unfixed)}",
            "- Агенты: " + ", ".join(f"`{a}` ×{n}" for a, n in stat.agents.most_common()),
            "- Примеры:",
            *(f"  - {x.case} · `{x.agent}` · `{x.where}` — {x.message}" for x in stat.examples),
            f"- **Исправление ({level} → {remedy.where}):** {remedy.fix}",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def stage_table(runs: Sequence[RunData]) -> list[str]:
    agents: list[str] = []
    for r in runs:
        for x in r.metrics.stages if r.metrics else []:
            if x.agent not in agents:
                agents.append(x.agent)
    header = "| Этап | " + " | ".join(r.run.case for r in runs) + " |"
    lines = [header, "|---|" + "---|" * len(runs)]
    for agent in agents:
        cells = []
        for r in runs:
            s = next((x for x in r.metrics.stages if x.agent == agent), None) if r.metrics else None
            if s is None:
                cells.append("—")
            elif s.status == StageStatus.skipped:
                cells.append(f"{ICONS['skipped']} skipped")
            else:
                cache = " (кэш)" if s.from_cache else ""
                cells.append(
                    f"{ICONS.get(s.status.value, '')} {s.status.value} · {s.attempts} · "
                    f"{_tokens(s.total_tokens)} · {_duration(s.wall_s)}{cache}"
                )
        lines.append(f"| `{agent}` | " + " | ".join(cells) + " |")
    totals = [
        f"{_tokens(r.metrics.total_tokens)} · {_duration(r.metrics.wall_s)}" if r.metrics else "—"
        for r in runs
    ]
    lines.append("| **итого** | " + " | ".join(totals) + " |")
    return lines


def _tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _duration(seconds: float) -> str:
    minutes, sec = divmod(round(seconds), 60)
    return f"{minutes}м {sec:02d}с" if minutes else f"{sec}с"


def _one_line(text: str, limit: int) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first if len(first) <= limit else first[: limit - 1] + "…"


# --- точка входа ---------------------------------------------------------------------------------


def rehearse(
    runs: Sequence[CaseRun], runs_dir: Path, out: Path, *, rules_file: Path = RULES_FILE
) -> tuple[str, list[RunData], list[ErrorStat]]:
    loaded = [load_run(r, runs_dir) for r in runs]
    errors = collect_errors(loaded)
    data = [d for d, _ in loaded]
    text = render(data, errors, load_rules(rules_file))
    out.write_text(text, encoding="utf-8")
    return text, data, errors


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    extra: list[str] = []
    if "--" in args:
        i = args.index("--")
        args, extra = args[:i], args[i + 1 :]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--examples", type=Path, default=ROOT / "examples")
    parser.add_argument(
        "--runs-dir", type=Path, default=Path(os.environ.get("SPECFORGE_RUNS_DIR", ROOT / "runs"))
    )
    parser.add_argument("--out", type=Path, default=ROOT / "rehearsal.md")
    parser.add_argument("--rules", type=Path, default=RULES_FILE)
    parser.add_argument(
        "--from-runs", nargs="+", metavar="RUN_ID", help="Не запускать, а разобрать эти прогоны."
    )
    ns = parser.parse_args(args)

    if ns.from_runs:
        runs = [CaseRun(case=rid, run_id=rid) for rid in ns.from_runs]
    else:
        cases = discover_cases(ns.examples)
        if not cases:
            print(f"Нет ТЗ в {ns.examples} ({', '.join(SUPPORTED_SUFFIXES)})", file=sys.stderr)
            return 2
        runs = run_cases(cases, ns.runs_dir, extra=extra)

    text, data, _ = rehearse(runs, ns.runs_dir, ns.out, rules_file=ns.rules)
    top = text[text.find("## Топ-") :] if "## Топ-" in text else text
    print()
    print(top)
    print(f"Отчёт: {ns.out}")
    failed = any(
        d.metrics is None or d.metrics.counts.get(StageStatus.failed.value, 0) for d in data
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
