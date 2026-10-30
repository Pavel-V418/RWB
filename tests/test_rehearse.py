"""scripts/rehearse.py: поиск ТЗ, таблица этап × ТЗ, топ ошибок шлюзов и исправления — на
синтетических каталогах прогонов (без LLM, Docker и подпроцессов)."""

import importlib.util
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import pytest

from specforge.core.contracts import (
    CaseProfile,
    Diagnostic,
    GateResult,
    StageResult,
    StageStatus,
    Usage,
)
from specforge.core.events import Event, EventType
from specforge.gates.runner import diagnostics_digest
from specforge.orchestrator.nodes import PIPELINE_FILE, PipelineSummary
from specforge.repair.fix_memory import signature_of

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def rh() -> ModuleType:
    spec = importlib.util.spec_from_file_location("rehearse", ROOT / "scripts" / "rehearse.py")
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["rehearse"] = module  # dataclasses ищут модуль по имени
    spec.loader.exec_module(module)
    return module


def _d(
    tool: str, code: str | None, message: str, file: str = "src/a.ts", line: int = 3
) -> Diagnostic:
    return Diagnostic(tool=tool, code=code, message=message, file=file, line=line)


MISSING_MODULE = _d("tsc", "TS2307", "Cannot find module '@app/api' or its corresponding type")
MISSING_MODULE_2 = _d("tsc", "TS2307", "Cannot find module 'rxjs/x' or its corresponding type")
GENERIC = _d("generic", None, "something exploded at step 4", file="build.log", line=0)
NO_MEMBER = _d("msbuild", "CS1061", "'Order' does not contain a definition for 'Total'", "B.cs", 9)
TEST_FAIL = _d("junit", "AssertionError", "expected 2 but was 3", "T.kt", 5)


def write_run(
    runs_dir: Path,
    run_id: str,
    stages: dict[str, StageResult],
    events: Sequence[Event] = (),
) -> None:
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = PipelineSummary(
        run_id=run_id,
        profile=CaseProfile(),
        counts={},
        usage=Usage(),
        active_s=0.0,
        stages=stages,
        clock={a: [1000.0 + i * 10, 1000.0 + i * 10 + 7] for i, a in enumerate(stages)},
    )
    (run_dir / PIPELINE_FILE).write_text(summary.model_dump_json(), encoding="utf-8")
    (run_dir / "events.jsonl").write_text(
        "".join(e.model_dump_json() + "\n" for e in events), encoding="utf-8"
    )


def stage(
    agent: str,
    status: StageStatus,
    *,
    attempts: int = 1,
    tokens: int = 1000,
    gate: GateResult | None = None,
) -> StageResult:
    return StageResult(
        agent=agent,
        status=status,
        attempts=attempts,
        usage=Usage(prompt_tokens=tokens, completion_tokens=0, calls=1),
        duration_s=7.0,
        gate=gate,
        summary="готово",
    )


def red(*diags: Diagnostic) -> GateResult:
    return GateResult(passed=False, diagnostics=list(diags), total_errors=len(diags))


def gate_event(run_id: str, agent: str, kind: str, gate: GateResult, attempt: int = 0) -> Event:
    data: dict[str, object] = {"diagnostics": diagnostics_digest(gate)}
    if kind == EventType.repair_attempt:
        data["attempt"] = attempt
    return Event(kind=kind, run_id=run_id, agent=agent, data=data)  # type: ignore[arg-type]


def test_discover_cases(rh: ModuleType, tmp_path: Path) -> None:
    ex = tmp_path / "examples"
    (ex / "case1" / "data").mkdir(parents=True)
    (ex / "case1" / "tz.md").write_text("ТЗ", encoding="utf-8")
    (ex / "case1" / "data" / "notes.md").write_text("данные", encoding="utf-8")
    (ex / "case2").mkdir()
    (ex / "case2" / "tz.docx").write_bytes(b"x")
    (ex / "synthetic.md").write_text("ТЗ", encoding="utf-8")
    (ex / "README.md").write_text("о примерах", encoding="utf-8")
    (ex / "_draft.md").write_text("черновик", encoding="utf-8")
    (ex / "data.json").write_text("{}", encoding="utf-8")

    cases = rh.discover_cases(ex)

    assert [(c.name, c.path.relative_to(ex).as_posix()) for c in cases] == [
        ("case1-tz", "case1/tz.md"),
        ("case2-tz", "case2/tz.docx"),
        ("synthetic", "synthetic.md"),
    ]


def test_table_top_errors_and_remedies(rh: ModuleType, tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"

    def runner(case: object, run_id: str, rd: Path, extra: Sequence[str]) -> int:
        assert rd == runs_dir
        assert list(extra) == ["--web"]
        if run_id.endswith("-a"):
            # web_coder: TS2307 до ремонта, после 1-го ремонта ещё есть, после 2-го — зелёный;
            # backend_coder: generic, ремонт не помог (degraded)
            first, second = red(MISSING_MODULE, GENERIC), red(MISSING_MODULE_2)
            write_run(
                rd,
                run_id,
                {
                    "analyst": stage("analyst", StageStatus.passed, tokens=2500),
                    "web_coder": stage(
                        "web_coder",
                        StageStatus.passed,
                        attempts=3,
                        tokens=12_000,
                        gate=GateResult(passed=True),
                    ),
                    "backend_coder": stage(
                        "backend_coder", StageStatus.degraded, attempts=2, gate=red(GENERIC)
                    ),
                },
                [
                    gate_event(run_id, "web_coder", EventType.gate_failed, first),
                    gate_event(run_id, "web_coder", EventType.repair_attempt, first, 1),
                    gate_event(run_id, "web_coder", EventType.repair_attempt, second, 2),
                    gate_event(run_id, "backend_coder", EventType.gate_failed, red(GENERIC)),
                    gate_event(run_id, "backend_coder", EventType.repair_attempt, red(GENERIC), 1),
                ],
            )
            return 0
        # второе ТЗ: TS2307 у web_coder ещё раз; события старого формата (без diagnostics) —
        # итоговый красный шлюз берётся из pipeline.json
        write_run(
            rd,
            run_id,
            {
                "analyst": stage("analyst", StageStatus.passed),
                "web_coder": stage(
                    "web_coder", StageStatus.failed, gate=red(MISSING_MODULE, NO_MEMBER)
                ),
                "mobile_coder": stage("mobile_coder", StageStatus.skipped),
            },
            [Event(kind=EventType.gate_failed, run_id=run_id, agent="web_coder", data={})],
        )
        return 1

    cases = [rh.Case(name="a", path=tmp_path / "a.md"), rh.Case(name="b", path=tmp_path / "b.md")]
    runs = rh.run_cases(cases, runs_dir, runner=runner, extra=["--web"], stamp="t")
    text, data, errors = rh.rehearse(runs, runs_dir, tmp_path / "rehearsal.md")

    assert (tmp_path / "rehearsal.md").read_text(encoding="utf-8") == text
    assert [r.run.run_id for r in data] == ["rehearse-t-a", "rehearse-t-b"]
    # таблица: строки — этапы в порядке появления, ячейка — статус · попытки · токены · время
    assert "| a | `rehearse-t-a` | 0 | 0с | 2 / 1 / 0 / 0 | 15.5k |" in text
    assert "| Этап | a | b |" in text
    assert "| `web_coder` | ✔ passed · 3 · 12.0k · 7с | ✘ failed · 1 · 1.0k · 7с |" in text
    assert "| `mobile_coder` | — | ⏭ skipped |" in text
    assert "| `backend_coder` | ⚠ degraded · 2 · 1.0k · 7с | — |" in text

    # TS2307: 3 запуска шлюза (2 в a, 1 в b) — первое место, оба ТЗ; шаблон нормализован
    top = errors[0]
    assert (top.signature.tool, top.signature.code, top.hits) == ("tsc", "TS2307", 3)
    assert top.cases == {"a", "b"}
    assert top.unfixed == {("b", "web_coder")}
    assert top.signature.pattern == "cannot find module <name> or its corresponding type"
    assert [x.case for x in top.examples] == ["a", "b"]

    generic = next(e for e in errors if e.signature.tool == "generic")
    assert generic.hits == 3
    assert generic.unfixed == {("a", "backend_coder")}

    assert "## Топ-10 ошибок шлюзов" in text
    assert "### 1. `tsc` TS2307: cannot find module <name>" in text
    assert "a · `web_coder` · `src/a.ts:3` — Cannot find module '@app/api'" in text
    # правила: TS2307 → агент команды с именем агента; generic → парсер платформы
    assert "**Исправление (агент команды → `web_coder` (agents/web_coder/prompts/)):**" in text
    assert "**Исправление (платформа → парсер):**" in text
    assert "**Исправление (агент команды → `web_coder`" in text  # CS1061 — контракт соседа


def test_fallback_heuristics(rh: ModuleType) -> None:
    def stat(d: Diagnostic, stages: set[tuple[str, str]], unfixed: set[tuple[str, str]]) -> object:
        s = rh.ErrorStat(signature=signature_of(d), hits=len(stages))
        s.stages, s.unfixed = stages, unfixed
        for _, agent in stages:
            s.agents[agent] += 1
        return s

    odd = _d("tsc", "TS9999", "weird thing")
    unfixed = rh.remedy_for(stat(odd, {("a", "x"), ("b", "x")}, {("a", "x")}), [])
    assert (unfixed.level, unfixed.where) == ("platform", "промпт ремонтника")
    shared = rh.remedy_for(stat(odd, {("a", "x"), ("a", "y")}, set()), [])
    assert (shared.level, shared.where) == ("platform", "промпт ремонтника")
    own = rh.remedy_for(stat(odd, {("a", "x")}, set()), [])
    assert own.level == "agent"
    assert "`x`" in own.where


def test_rules_config_is_valid(rh: ModuleType) -> None:
    rules = rh.load_rules()
    assert len(rules) >= 10
    first = {
        d.tool: next(r for r in rules if r.matches(signature_of(d)))
        for d in (GENERIC, NO_MEMBER, TEST_FAIL, _d("eslint", "no-unused-vars", "x is unused"))
    }
    assert first["generic"].where == "парсер"
    assert first["eslint"].where == "инструмент"
    assert first["msbuild"].level == first["junit"].level == "agent"


def test_failed_run_and_from_runs_cli(
    rh: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs_dir = tmp_path / "runs"

    def boom(case: object, run_id: str, rd: Path, extra: Sequence[str]) -> int:
        raise RuntimeError("docker умер")

    runs = rh.run_cases([rh.Case(name="x", path=tmp_path / "x.md")], runs_dir, runner=boom)
    assert runs[0].error == "RuntimeError: docker умер"
    text, data, errors = rh.rehearse(runs, runs_dir, tmp_path / "r.md")
    assert data[0].metrics is None
    assert errors == []
    assert "RuntimeError: docker умер" in text
    assert "Красных шлюзов не было." in text

    # --from-runs: только отчёт по готовым прогонам; есть failed → код 1
    write_run(runs_dir, "old", {"web_coder": stage("web_coder", StageStatus.failed)})
    out = tmp_path / "rehearsal.md"
    code = rh.main(["--from-runs", "old", "--runs-dir", str(runs_dir), "--out", str(out)])
    assert code == 1
    assert "| `web_coder` | ✘ failed · 1 · 1.0k · 7с |" in out.read_text("utf-8")
    assert rh.main(["--examples", str(tmp_path / "нет"), "--out", str(out)]) == 2


def test_digest_in_events_roundtrips() -> None:
    gate = red(*[_d("tsc", "TS2307", "m" * 500, line=i) for i in range(15)])
    digest = diagnostics_digest(gate)
    assert len(digest) == 10
    back = Diagnostic.model_validate(json.loads(json.dumps(digest[0])))
    assert back.code == "TS2307"
    assert len(back.message) == 300
    assert back.code_window is None
