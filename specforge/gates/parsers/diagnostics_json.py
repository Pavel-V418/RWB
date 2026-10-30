"""Готовый отчёт шлюза в формате Diagnostic — для своих скриптов проверки команды.

Скрипт печатает JSON в stdout или пишет `gate_report.json` (в любом месте workspace) —
список диагностик или объект с ключом `diagnostics` / `errors`:

    [{"tool": "android-gate", "file": "app/src/main/.../Main.kt", "line": 12, "column": 5,
      "severity": "error", "code": "SNAPSHOT", "category": "test", "message": "…"}]

Обязательно только `message`. `col` принимается как `column`; `code_window` игнорируется —
его заполнит платформа. Записи с severity info/hint/note и без сообщения пропускаются.
Парсер выбирается только явно: `gate_parsers={"<команда>": "diagnostics-json"}`.
"""

from typing import Any

from specforge.core.contracts import Diagnostic, DiagnosticCategory, Severity
from specforge.gates.parsers.common import ParseContext, clip, fresh_reports, load_json

REPORT_GLOBS = ("gate_report.json", "**/gate_report.json")
DEFAULT_TOOL = "gate-report"
_SEVERITY: dict[str, Severity] = {
    "error": "error",
    "fatal": "error",
    "failure": "error",
    "warning": "warning",
    "warn": "warning",
}


def parse_diagnostics_json(output: str, ctx: ParseContext) -> list[Diagnostic]:
    """JSON из вывода команды; если его нет — свежие gate_report.json этого запуска."""
    items = _items(load_json(output))
    if items is None:
        found: list[Any] = []
        for path in fresh_reports(ctx, REPORT_GLOBS):
            found += _items(load_json(path.read_text("utf-8", errors="replace"))) or []
        items = found
    return [d for d in (_diagnostic(item) for item in items) if d is not None]


def _items(data: object) -> list[Any] | None:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("diagnostics", "errors"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return None


def _diagnostic(item: object) -> Diagnostic | None:
    if not isinstance(item, dict):
        return None
    message = str(item.get("message") or "").strip()
    severity = _SEVERITY.get(str(item.get("severity") or "error").lower())
    if not message or severity is None:
        return None
    category = item.get("category")
    return Diagnostic(
        tool=str(item.get("tool") or DEFAULT_TOOL),
        file=str(item["file"]) if item.get("file") else None,
        line=_int(item.get("line")),
        column=_int(item.get("column", item.get("col"))),
        severity=severity,
        code=str(item["code"]) if item.get("code") not in (None, "") else None,
        category=_category(category),
        message=clip(message),
    )


def _category(value: object) -> DiagnosticCategory | None:
    if value == "compile":
        return "compile"
    if value == "test":
        return "test"
    if value == "lint":
        return "lint"
    if value == "generic":
        return "generic"
    return None


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value) or None
    return None
