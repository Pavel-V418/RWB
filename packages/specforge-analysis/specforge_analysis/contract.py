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
