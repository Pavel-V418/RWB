"""TypeScript: tsc, Angular CLI (webpack- и esbuild-формат) и eslint --format json."""

import re

from specforge.core.contracts import Diagnostic
from specforge.gates.parsers.common import ParseContext, clip, load_json, strip_ansi

# src/app/a.ts:12:5 - error TS2345: ...   (tsc --pretty, ng build на webpack; может быть «Error: »)
_TSC_PRETTY_RE = re.compile(
    r"^(?:Error:\s*)?(?P<file>[^\s:][^:]*?):(?P<line>\d+):(?P<col>\d+)\s+-\s+"
    r"(?P<sev>error|warning)\s+(?P<code>(?:TS|NG)\d+):\s*(?P<msg>.*)$"
)
# src/app/a.ts(12,5): error TS2345: ...   (tsc --pretty false)
_TSC_PLAIN_RE = re.compile(
    r"^(?P<file>[^\s(][^(]*?)\((?P<line>\d+),(?P<col>\d+)\):\s*"
    r"(?P<sev>error|warning)\s+(?P<code>TS\d+):\s*(?P<msg>.*)$"
)
# ✘ [ERROR] TS2345: Argument ... [plugin angular-compiler]   (Angular application builder / esbuild)
_ESBUILD_HEAD_RE = re.compile(
    r"^\s*[✘▲X]?\s*\[(?P<sev>ERROR|WARNING)\]\s+(?:(?P<code>(?:TS|NG)\d+):\s*)?(?P<msg>.*?)"
    r"(?:\s+\[plugin [^\]]+\])?\s*$"
)
#     src/app/app.component.ts:12:22:
_ESBUILD_LOC_RE = re.compile(r"^\s+(?P<file>[^\s:][^:]*?):(?P<line>\d+):(?P<col>\d+):\s*$")


def parse_tsc(output: str, ctx: ParseContext) -> list[Diagnostic]:
    del ctx
    lines = strip_ansi(output).splitlines()
    result: list[Diagnostic] = []
    for i, line in enumerate(lines):
        match = _TSC_PRETTY_RE.match(line) or _TSC_PLAIN_RE.match(line)
        if match:
            result.append(
                _diagnostic(
                    match["file"],
                    match["line"],
                    match["col"],
                    match["sev"],
                    match["code"],
                    match["msg"],
                )
            )
            continue
        head = _ESBUILD_HEAD_RE.match(line)
        if head:
            file = row = col = None
            for follow in lines[i + 1 : i + 6]:  # место ошибки — в нескольких строках ниже
                loc = _ESBUILD_LOC_RE.match(follow)
                if loc:
                    file, row, col = loc["file"], loc["line"], loc["col"]
                    break
            result.append(_diagnostic(file, row, col, head["sev"], head["code"], head["msg"]))
    return result


def parse_eslint(output: str, ctx: ParseContext) -> list[Diagnostic]:
    """`eslint --format json`: [{filePath, messages: [{ruleId, severity, message, line, ...}]}]."""
    del ctx
    data = load_json(output)
    if not isinstance(data, list):
        return []
    result: list[Diagnostic] = []
    for report in data:
        if not isinstance(report, dict):
            continue
        for message in report.get("messages", []):
            if not isinstance(message, dict):
                continue
            result.append(
                Diagnostic(
                    tool="eslint",
                    category="lint",
                    file=str(report.get("filePath") or "") or None,
                    line=_int(message.get("line")),
                    column=_int(message.get("column")),
                    severity="error" if message.get("severity") == 2 else "warning",
                    code=str(message.get("ruleId") or "eslint"),
                    message=clip(str(message.get("message", ""))),
                )
            )
    return result


def _diagnostic(
    file: str | None,
    line: str | None,
    col: str | None,
    severity: str,
    code: str | None,
    message: str,
) -> Diagnostic:
    return Diagnostic(
        tool="tsc",
        category="compile",
        file=file.strip() if file else None,
        line=int(line) if line else None,
        column=int(col) if col else None,
        severity="error" if severity.lower() == "error" else "warning",
        code=code,
        message=clip(message),
    )


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
