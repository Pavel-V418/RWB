"""Общий контракт агента SpecForge.

Каждый агент команды — функция
    run(input_dir, output_dir, llm) -> GateResult
Оркестратор (участник 1) встраивает такие функции узлами графа.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

Severity = Literal["error", "warning"]


@dataclass
class Issue:
    code: str
    severity: Severity
    message: str
    target: str | None = None

    def __str__(self) -> str:  # удобно для логов и промптов ремонта
        where = f" [{self.target}]" if self.target else ""
        return f"{self.severity.upper()} {self.code}{where}: {self.message}"


@dataclass
class GateResult:
    stage: str
    passed: bool
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    tokens: dict = field(default_factory=lambda: {"input": 0, "output": 0})
    attempts: int = 0
    llm_calls: int = 0
    duration_s: float = 0.0
    artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["errors"] = [asdict(i) for i in self.errors]
        d["warnings"] = [asdict(i) for i in self.warnings]
        return d


def split_issues(issues: list[Issue]) -> tuple[list[Issue], list[Issue]]:
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    return errors, warnings


def ensure_dir(p: str | Path) -> Path:
    path = Path(p)
    path.mkdir(parents=True, exist_ok=True)
    return path


def reset_outputs(out: Path, patterns: list[str], protect: Path | None = None) -> None:
    """Удаляет результаты прошлого прогона, чтобы при сбое в папке не остались устаревшие артефакты,
    которые оркестратор примет за свежие. answers.yaml и metrics.jsonl не трогаются."""
    if protect is not None:
        prot = protect.resolve()
        o = out.resolve()
        if o == prot or o in prot.parents:  # выход совпадает со входом — ничего не удаляем
            return
    for pat in patterns:
        for p in out.glob(pat):
            if p.is_file() and p.name not in ("answers.yaml", "answers.yml", "metrics.jsonl"):
                p.unlink()


def failed_result(stage: str, out: Path, code: str, exc: Exception, duration_s: float, msum: dict) -> GateResult:
    """GateResult для этапа, который не смог завершиться (например, модель недоступна или ответ невалиден)."""
    import json

    issue = Issue(code, "error", f"{type(exc).__name__}: {exc}"[:1000])
    res = GateResult(stage=stage, passed=False, errors=[issue],
                     tokens={"input": msum.get("input_tokens", 0), "output": msum.get("output_tokens", 0)},
                     attempts=msum.get("llm_calls", 0), llm_calls=msum.get("llm_calls", 0),
                     duration_s=round(duration_s, 2))
    report = {"passed": False, "errors": [asdict(issue)], "warnings": [], "metrics": msum}
    (out / "gate_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "gate_result.json").write_text(json.dumps(res.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return res
