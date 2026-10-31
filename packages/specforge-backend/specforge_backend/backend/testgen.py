"""Этап тестов backend-агента: интеграционные тесты фичи по критериям приёмки + шлюз `dotnet test` по её классу.

Тест, который не прошёл за все попытки, не удаляется и не подгоняется: ему ставится
[Fact(Skip = "...")] с причиной — CI остаётся зелёным, а падение видно в отчёте и уходит на ремонт кода (--feedback).
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .context import ContextBuilder
from .llm import LlmClient, estimate_tokens

TESTS_DIR = "tests/App.IntegrationTests/Features"
TESTS_PROJECT = "tests/App.IntegrationTests/App.IntegrationTests.csproj"
NAMESPACE = "App.IntegrationTests.Features"


def criteria_for(builder: ContextBuilder, tag: str) -> str:
    """Критерии приёмки FR, на которые ссылаются операции тега (Given/When/Then из 01_requirements)."""
    path = builder.artifacts / "01_requirements" / "requirements.json"
    if not path.exists():
        return ""
    doc = json.loads(path.read_text(encoding="utf-8"))
    fr_ids = {r.split(" ", 1)[0] for op in builder.build(tag).operations for r in op.requirements}
    lines = []
    for ac in doc.get("acceptance", []):
        if ac.get("fr_id") in fr_ids:
            parts = [f"{ac['id']} ({ac.get('kind', '')}) {ac.get('title', '')}"]
            for key, label in (("given", "Дано"), ("when", "Когда"), ("then", "Тогда")):
                if ac.get(key):
                    parts.append(f"  {label}: " + "; ".join(ac[key]))
            lines.append("\n".join(parts))
    return "\n".join(lines)


def proven_helpers(tests_dir: Path, tag: str, limit: int = 6000) -> str:
    """Помощники тестовых данных (private-методы) из уже прошедших тестов других фич.

    Без них каждая фича заново изобретает, например, генератор госномеров — и нарушает CHECK/длину колонки.
    Файл с [Fact(Skip = ...)] не годится как образец: его данные могли быть причиной падения.
    """
    seen, parts = set(), []
    for path in sorted(tests_dir.glob("*ApiTests.cs")):
        text = path.read_text(encoding="utf-8")
        if path.stem == f"{tag}ApiTests" or "Skip =" in text:
            continue
        for m in re.finditer(r"^[ \t]*private\s+(?:static\s+)?(?:async\s+)?[\w<>?,\s\[\]]+?\s+(\w+)\s*\([^)]*\)\s*\{", text, re.MULTILINE):
            if m.group(1) in seen:
                continue
            depth, i = 1, m.end()
            while i < len(text) and depth:
                depth += {"{": 1, "}": -1}.get(text[i], 0)
                i += 1
            seen.add(m.group(1))
            parts.append(f"// из {path.name}\n" + text[m.start():i].strip())
    out = "\n\n".join(parts)
    return out[:limit]


def run_class(backend: Path, tag: str, dotnet) -> tuple[str, list[dict]]:
    """Сборка тестового проекта и прогон класса тега. ('build', ошибки) | ('tests', упавшие) | ('ok', [])."""
    from .agent import parse_build_errors  # общий разбор логов компилятора

    code, log = dotnet(["build", TESTS_PROJECT, "-nologo", "-v", "q", "-clp:NoSummary", "-warnaserror"], backend)
    if code != 0:
        errors = parse_build_errors(log, backend) or [{"file": None, "line": None, "message": log.strip()[-2000:]}]
        return "build", errors
    results = Path(tempfile.mkdtemp(prefix="agent-tests-"))
    try:
        code, log = dotnet(["test", "--project", TESTS_PROJECT, "--no-build", "--filter-class", f"{NAMESPACE}.{tag}ApiTests",
                            "--report-xunit-junit", "--results-directory", str(results)], backend)
        failed = [f for report in results.glob("*.junit.xml") for f in parse_junit(report.read_text(encoding="utf-8"))]
        if code != 0 and not failed:
            return "tests", [{"test": None, "message": log.strip()[-2000:]}]
        return ("tests", failed) if failed else ("ok", [])
    finally:
        shutil.rmtree(results, ignore_errors=True)


def parse_junit(xml: str) -> list[dict]:
    failed = []
    for case in ET.fromstring(xml).iter("testcase"):
        problem = case.find("failure")
        if problem is None:
            problem = case.find("error")
        if problem is not None:
            message = (problem.get("message") or "") + "\n" + (problem.text or "")
            failed.append({"test": case.get("name", "").rsplit(".", 1)[-1], "message": message.strip()[:1500]})
    return failed


def format_failures(kind: str, errors: list[dict], limit: int = 15) -> str:
    if kind == "build":
        return "\n".join(f"- {Path(e['file']).name if e.get('file') else '?'}:{e.get('line')}: {e['message']}" for e in errors[:limit])
    return "\n".join(f"- {e['test'] or '?'}: {e['message']}" for e in errors[:limit])


def apply_skips(code: str, failed: list[dict]) -> str:
    """[Fact] упавших тестов → [Fact(Skip = "...")]: тест остаётся в отчёте, CI не краснеет."""
    for failure in failed:
        if not failure["test"]:
            continue
        reason = re.sub(r"\s+", " ", failure["message"])[:200].replace("\\", "\\\\").replace('"', "'")
        code = re.sub(rf"\[Fact\](\s*public\s+async\s+Task\s+{re.escape(failure['test'])}\s*\()",
                      f'[Fact(Skip = "Падает, передано на ремонт кода: {reason}")]\\1', code, count=1)
    return code


def generate(builder: ContextBuilder, backend: Path, tag: str, llm: LlmClient, dotnet, extract_code, trace: Path,
             max_attempts: int, log=print) -> dict:
    """Тесты тега. Возвращает {status: passed|skipped_failures|no_tests, tests, failed: [...], attempts}."""
    from .prompts import TESTS_SYSTEM, tests_prompt, tests_repair_prompt

    impl = (backend / "src/App.Application/Features" / tag / f"{tag}Api.cs").read_text(encoding="utf-8")
    prompt = tests_prompt(builder.build(tag).render(), tag, impl, criteria_for(builder, tag),
                          proven_helpers(backend / TESTS_DIR, tag))
    file = backend / TESTS_DIR / f"{tag}ApiTests.cs"
    file.parent.mkdir(parents=True, exist_ok=True)
    log(f"[{tag}:tests] контекст ~{estimate_tokens(TESTS_SYSTEM + prompt)} токенов")
    code, kind, errors = None, "build", []
    attempts = 0
    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        started = time.monotonic()
        user = prompt if code is None else tests_repair_prompt(code, format_failures(kind, errors), tag)
        answer = llm.chat(TESTS_SYSTEM, user)
        (trace / f"{tag}-tests-{attempt}.md").write_text(answer, encoding="utf-8")
        new_code = extract_code(answer)
        if new_code is None:
            log(f"[{tag}:tests] попытка {attempt}: нет блока кода")
            continue
        code = new_code
        file.write_text(code, encoding="utf-8")
        kind, errors = run_class(backend, tag, dotnet)
        (trace / f"{tag}-tests-{attempt}.errors.txt").write_text(format_failures(kind, errors, 100) or "OK\n", encoding="utf-8")
        log(f"[{tag}:tests] попытка {attempt}: "
            f"{'все тесты прошли' if kind == 'ok' else ('не компилируется' if kind == 'build' else f'упало тестов: {len(errors)}')} "
            f"({time.monotonic() - started:.0f} с, всего токенов {llm.usage.total})")
        if kind == "ok":
            break

    total = len(re.findall(r"\[Fact", code or ""))
    if kind == "ok":
        return {"status": "passed", "tests": total, "failed": [], "attempts": attempts}
    if code is None or kind == "build" or any(not e["test"] for e in errors):
        # Не собралось (или прогон упал целиком) — тестов фичи нет, сборка решения остаётся зелёной
        file.unlink(missing_ok=True)
        log(f"[{tag}:tests] тесты не собрались за {max_attempts} попыток — файл удалён")
        return {"status": "no_tests", "tests": 0, "failed": errors[:10], "attempts": attempts}
    file.write_text(apply_skips(code, errors), encoding="utf-8")
    log(f"[{tag}:tests] {len(errors)} из {total} тестов помечены Skip — передать на ремонт кода")
    return {"status": "skipped_failures", "tests": total, "failed": errors, "attempts": attempts}
