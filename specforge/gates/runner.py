"""Шлюз этапа: команды AgentSpec.gate_commands в песочнице → GateResult с Diagnostic.

- команды выполняются по очереди, до первой упавшей (exit != 0) — дальше нет смысла:
  тесты не запускают на некомпилируемом коде;
- полный вывод — в runs/<run_id>/logs/<agent>.gate.log, в Diagnostic — только разобранное;
- вывод разбирает парсер (AgentSpec.gate_parsers или автоматический выбор по команде);
  если он ничего не нашёл в упавшей команде — generic, затем хвост вывода;
- диагностики дедуплицируются и упорядочиваются: compile → test → lint → generic, ошибки
  раньше предупреждений; в результате не больше MAX_DIAGNOSTICS, total_errors — все ошибки;
- у диагностик с файлом и строкой — code_window: 15 строк кода вокруг ошибки.
"""

import contextlib
import os
import re
import time
from pathlib import Path
from typing import TextIO
from urllib.parse import unquote, urlparse

import structlog
from pydantic import JsonValue

from specforge.core.contracts import AgentSpec, Diagnostic, GateResult
from specforge.gates.infra import classify_failure
from specforge.gates.parsers import PARSERS, ParseContext, select_parser
from specforge.gates.test_counts import TestCounts, count_tests
from specforge.sandbox.base import WORKDIR, ExecResult, Sandbox

log = structlog.get_logger(__name__)

MAX_DIAGNOSTICS = 20
WINDOW_LINES = 15
FALLBACK_TAIL_CHARS = 2000
CATEGORY_RANK = {"compile": 0, "test": 1, "lint": 2, "generic": 3, None: 4}


class GateConfigError(ValueError):
    """Шлюз описан неверно (например, неизвестный парсер в gate_parsers)."""


class GateRunner:
    def __init__(
        self,
        runs_dir: Path = Path("runs"),
        *,
        logs_dir: Path | None = None,
        timeout_s: float | None = None,
        max_diagnostics: int = MAX_DIAGNOSTICS,
    ) -> None:
        self.runs_dir = runs_dir
        self.logs_dir = logs_dir
        self.timeout_s = timeout_s or float(os.environ.get("SPECFORGE_GATE_TIMEOUT_S", "1800"))
        self.max_diagnostics = max_diagnostics

    def run(self, spec: AgentSpec, sandbox: Sandbox, run_id: str) -> GateResult:
        try:
            parsers = [select_parser(c, spec.gate_parsers) for c in spec.gate_commands]
        except ValueError as exc:
            raise GateConfigError(f"{spec.name}: {exc}") from exc
        logs_dir = self.logs_dir or self.runs_dir / run_id / "logs"
        log_path = logs_dir / f"{spec.name}.gate.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        started = time.monotonic()
        diagnostics: list[Diagnostic] = []
        failed_index: int | None = None
        infra: str | None = None
        tests: TestCounts | None = None
        with log_path.open("w", encoding="utf-8") as gate_log:
            for index, (command, parser) in enumerate(
                zip(spec.gate_commands, parsers, strict=True)
            ):
                command_started = time.time()
                result = sandbox.exec(command, timeout_s=self.timeout_s)
                _write_log(gate_log, result, parser)
                ctx = ParseContext(
                    command=command,
                    workspace=sandbox.workspace,
                    exit_code=result.exit_code,
                    started_at=command_started,
                )
                found = self._parse(parser, result, ctx)
                diagnostics += found
                counted = count_tests(ctx)
                if counted is not None:
                    tests = counted if tests is None else tests.add(counted)
                log.info(
                    "gate.command",
                    agent=spec.name,
                    command=command,
                    exit_code=result.exit_code,
                    parser=parser,
                    diagnostics=len(found),
                )
                if result.exit_code != 0:
                    failed_index = index
                    infra = classify_failure(result.exit_code, result.duration_s, result.output)
                    break

        selected, total_errors = prioritize(
            [normalize(d, sandbox.workspace) for d in diagnostics], self.max_diagnostics
        )
        return GateResult(
            passed=failed_index is None,
            failed_command_index=failed_index,
            diagnostics=[with_code_window(d, sandbox.workspace) for d in selected],
            total_errors=total_errors,
            log_path=str(log_path),
            duration_s=time.monotonic() - started,
            infra_error=infra,
            tests_executed=tests.executed if tests is not None else None,
        )

    def _parse(self, parser: str, result: ExecResult, ctx: ParseContext) -> list[Diagnostic]:
        output = result.output
        try:
            found = PARSERS[parser](output, ctx)
        except Exception:  # сломанный парсер не должен ронять шлюз
            log.exception("gate.parser_failed", parser=parser)
            found = []
        if result.exit_code == 0:
            return found
        if not any(d.severity == "error" for d in found) and parser != "generic":
            found += PARSERS["generic"](output, ctx)
        if not any(d.severity == "error" for d in found):
            reason = (
                "превысила таймаут"
                if result.timed_out
                else f"завершилась с кодом {result.exit_code}"
            )
            tail = output[-FALLBACK_TAIL_CHARS:].strip()
            found.append(
                Diagnostic(
                    tool=ctx.command.split(maxsplit=1)[0] if ctx.command.strip() else "gate",
                    category="generic",
                    code=f"exit:{result.exit_code}",
                    message=f"`{ctx.command}` {reason}" + (f"\n…{tail}" if tail else ""),
                )
            )
        return found


def prioritize(diagnostics: list[Diagnostic], limit: int) -> tuple[list[Diagnostic], int]:
    """Дедупликация, порядок compile → test → lint → generic (ошибки раньше предупреждений),
    не больше `limit`. Возвращает (отобранные, число всех уникальных ошибок)."""
    unique: dict[tuple[object, ...], Diagnostic] = {}
    for d in diagnostics:
        key = (d.tool, d.category, d.severity, d.file, d.line, d.column, d.code, d.message)
        unique.setdefault(key, d)
    ordered = sorted(
        unique.values(),
        key=lambda d: (CATEGORY_RANK.get(d.category, 4), d.severity != "error"),
    )  # sorted устойчив: внутри группы — порядок вывода инструмента
    total_errors = sum(d.severity == "error" for d in ordered)
    return ordered[:limit], total_errors


DIGEST_LIMIT = 10
DIGEST_MESSAGE_CHARS = 300


def diagnostics_digest(gate: GateResult, limit: int = DIGEST_LIMIT) -> list[JsonValue]:
    """Компактные диагностики для событий (gate_failed, repair_attempt) — без code_window:
    по ним репетиция (scripts/rehearse.py) считает частые ошибки по всем попыткам."""
    return [
        {
            "tool": d.tool,
            "code": d.code,
            "file": d.file,
            "line": d.line,
            "severity": d.severity,
            "category": d.category,
            "message": d.message[:DIGEST_MESSAGE_CHARS],
        }
        for d in gate.diagnostics[:limit]
    ]


def normalize(diagnostic: Diagnostic, workspace: Path) -> Diagnostic:
    """Путь файла — относительно /work, если он внутри workspace (file://, /work/…, путь хоста)."""
    if not diagnostic.file:
        return diagnostic
    path = diagnostic.file.strip()
    if path.startswith("file://"):
        path = unquote(urlparse(path).path)
        if re.match(r"^/[A-Za-z]:/", path):  # file:///C:/...
            path = path[1:]
    path = path.replace("\\", "/")
    if path == WORKDIR or path.startswith(WORKDIR + "/"):
        path = path[len(WORKDIR) + 1 :]
    elif re.match(r"^[A-Za-z]:/", path):
        with contextlib.suppress(ValueError):  # файл вне workspace — оставляем как есть
            path = Path(path).resolve().relative_to(workspace.resolve()).as_posix()
    elif path.startswith("./"):
        path = path[2:]
    return diagnostic if path == diagnostic.file else diagnostic.model_copy(update={"file": path})


def with_code_window(
    diagnostic: Diagnostic, workspace: Path, size: int = WINDOW_LINES
) -> Diagnostic:
    """15 строк кода вокруг строки ошибки с номерами; строка ошибки помечена `>`."""
    if not diagnostic.file or not diagnostic.line or diagnostic.code_window:
        return diagnostic
    if diagnostic.file.startswith("/") or re.match(r"^[A-Za-z]:", diagnostic.file):
        return diagnostic  # вне /work
    root = workspace.resolve()
    target = (root / diagnostic.file).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return diagnostic
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return diagnostic
    if not lines:
        return diagnostic
    line = min(diagnostic.line, len(lines))
    start = max(1, line - size // 2)
    end = min(len(lines), start + size - 1)
    start = max(1, end - size + 1)
    window = "\n".join(
        f"{'>' if n == line else ' '}{n:>5} | {lines[n - 1]}" for n in range(start, end + 1)
    )
    return diagnostic.model_copy(update={"code_window": window})


def _write_log(out: TextIO, result: ExecResult, parser: str) -> None:
    out.write(
        f"$ {result.command}\n"
        f"exit: {result.exit_code}  duration: {result.duration_s:.2f}s  "
        f"timed_out: {result.timed_out}  parser: {parser}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n\n"
    )
    out.flush()
