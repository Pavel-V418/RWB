"""Репетиция: прогон этапов 1–2 по набору кейсов и итоговая таблица качества и расхода.

Кейс — папка с requirements.(md|txt|pdf|docx) и expected.yaml. Для каждого кейса считается:
шлюзы этапов, совпадение профиля, полнота извлечения FR по ожидаемым смысловым требованиям,
число вопросов, вызовы LLM, токены и время. Падение одного кейса не останавливает остальные.
"""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from . import stage_docs, stage_requirements, stage_system
from .llm import Metrics, make_llm
from .normalize import SUPPORTED
from .schemas import Requirements


@dataclass
class CaseScore:
    case: str
    stage1: str = "—"
    stage2: str = "—"
    docs: str = "—"
    profile_match: str = "—"
    profile_misses: list[str] = field(default_factory=list)
    fr_recall: str = "—"
    fr_missed: list[list[str]] = field(default_factory=list)
    functional: int = 0
    entities: int = 0
    entities_ok: bool | None = None
    questions: int = 0
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    retries: int = 0
    duration_s: float = 0.0
    error: str | None = None


def _norm(s: str) -> str:
    return s.lower().replace("ё", "е")


def score_requirements(req: Requirements, expected: dict) -> dict:
    out: dict = {}
    prof = req.profile.model_dump()
    exp_prof = expected.get("profile", {}) or {}
    misses = [f"{k}: ожидалось {v}, получено {prof.get(k)}" for k, v in exp_prof.items() if prof.get(k) != v]
    out["profile_match"] = f"{len(exp_prof) - len(misses)}/{len(exp_prof)}"
    out["profile_misses"] = misses

    texts = [_norm(f"{f.title} {f.statement}") for f in req.functional]
    groups = expected.get("must_cover", []) or []
    missed = [g for g in groups if not any(all(_norm(stem) in t for stem in g) for t in texts)]
    out["fr_recall"] = f"{len(groups) - len(missed)}/{len(groups)}"
    out["fr_missed"] = missed

    lo, hi = (expected.get("entities_range") or [0, 10 ** 6])
    out["entities"] = len(req.entities)
    out["entities_ok"] = lo <= len(req.entities) <= hi
    out["functional"] = len(req.functional)
    out["questions"] = len(req.questions)
    return out


def find_cases(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "expected.yaml").exists()
                  and any(c.suffix.lower() in SUPPORTED and c.stem.startswith("requirements") for c in p.iterdir()))


def run_case(case: Path, out_root: Path, llm_spec: str | None, check_db: bool) -> CaseScore:
    sc = CaseScore(case=case.name)
    out = out_root / case.name
    metrics = Metrics(sink=out / "metrics.jsonl")
    if metrics.sink.exists():
        metrics.sink.unlink()
    spec = llm_spec
    if spec == "fake":  # у каждого кейса свои фикстуры: golden/<кейс>/llm
        fixtures = case.parent.parent / "golden" / case.name / "llm"
        if not (fixtures / "extract.json").exists():
            sc.stage1 = sc.stage2 = sc.docs = "нужна модель"
            return sc
        spec = f"fake:{fixtures}"
    llm = make_llm(spec, metrics)
    t0 = time.monotonic()
    try:
        r1 = stage_requirements.run(case, out / "01_requirements", llm, mode="auto")
        sc.stage1 = "пройден" if r1.passed else f"ошибок {len(r1.errors)}"
        req = Requirements.model_validate_json((out / "01_requirements" / "requirements.json").read_text(encoding="utf-8"))
        expected = yaml.safe_load((case / "expected.yaml").read_text(encoding="utf-8")) or {}
        for k, v in score_requirements(req, expected).items():
            setattr(sc, k, v)
        if r1.passed:
            r2 = stage_system.run(out / "01_requirements", out / "02_system", llm, check_db=check_db)
            sc.stage2 = "пройден" if r2.passed else f"ошибок {len(r2.errors)}"
            if r2.passed:
                r3 = stage_docs.run(out, out / "09_docs", llm)
                sc.docs = "пройден" if r3.passed else f"ошибок {len(r3.errors)}"
    except Exception as exc:  # noqa: BLE001 — репетиция должна дойти до конца
        sc.error = f"{type(exc).__name__}: {exc}"[:400]
        (out / "error.txt").parent.mkdir(parents=True, exist_ok=True)
        (out / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    s = metrics.summary()
    sc.llm_calls, sc.tokens_in, sc.tokens_out, sc.retries = s["llm_calls"], s["input_tokens"], s["output_tokens"], s["retries"]
    sc.duration_s = round(time.monotonic() - t0, 1)
    return sc


def scorecard_md(scores: list[CaseScore], llm_spec: str | None) -> str:
    o = ["# Репетиция конвейера анализа", "",
         f"Модель: `{llm_spec or 'из LLM_BASE_URL/LLM_MODEL'}`. Этапы: 01_requirements, 02_system, 09_docs.", "",
         "| Кейс | Этап 1 | Этап 2 | Докум. | Профиль | Полнота FR | FR | Сущн. | Вопр. | LLM-вызовов | Повторов | Токены in / out | Время, с |",
         "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for s in scores:
        ent = f"{s.entities}" + ("" if s.entities_ok in (None, True) else " ⚠")
        o.append(f"| {s.case} | {s.stage1} | {s.stage2} | {s.docs} | {s.profile_match} | {s.fr_recall} | {s.functional} | {ent} | "
                 f"{s.questions} | {s.llm_calls} | {s.retries} | {s.tokens_in} / {s.tokens_out} | {s.duration_s} |")
    tot_in = sum(s.tokens_in for s in scores)
    tot_out = sum(s.tokens_out for s in scores)
    o += ["", f"Итого токенов: {tot_in} / {tot_out}; вызовов LLM: {sum(s.llm_calls for s in scores)}.", ""]
    for s in scores:
        notes = []
        if s.error:
            notes.append(f"- ошибка: {s.error}")
        notes += [f"- профиль: {m}" for m in s.profile_misses]
        notes += ["- не найдено требование: " + " + ".join(g) for g in s.fr_missed]
        if notes:
            o += [f"## {s.case}", ""] + notes + [""]
    return "\n".join(o)


def run(cases_root: str | Path, out_root: str | Path, llm_spec: str | None, *, check_db: bool = True,
        only: list[str] | None = None) -> list[CaseScore]:
    cases_root, out_root = Path(cases_root), Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    cases = [c for c in find_cases(cases_root) if not only or c.name in only]
    scores = [run_case(c, out_root, llm_spec, check_db) for c in cases]
    (out_root / "scorecard.md").write_text(scorecard_md(scores, llm_spec), encoding="utf-8")
    (out_root / "scorecard.json").write_text(json.dumps([asdict(s) for s in scores], ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    return scores
