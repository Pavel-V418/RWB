"""Контракты API: spectral (json и stylish) и asyncapi validate."""

import re

from specforge.core.contracts import Diagnostic, DiagnosticCategory
from specforge.gates.parsers.common import ParseContext, clip, load_json, strip_ansi

# spectral severity: 0 error, 1 warn, 2 info, 3 hint
_SPECTRAL_ERROR = 0
_SPECTRAL_WARNING = 1

#  12:7   error  oas3-schema  "info" property must have required property "version"  info
_STYLISH_RE = re.compile(
    r"^\s+(?P<line>\d+):(?P<col>\d+)\s+(?P<sev>error|warning|information|info|hint)\s+"
    r"(?P<code>\S+)\s+(?P<msg>.*?)(?:\s{2,}(?P<path>\S.*))?\s*$"
)
_FILE_RE = re.compile(r"^(?P<file>\S.*\.(?:ya?ml|json))\s*$")


def parse_spectral(output: str, ctx: ParseContext) -> list[Diagnostic]:
    """`spectral lint -f json`; без -f json — формат stylish."""
    data = load_json(output)
    if not isinstance(data, list):
        return _parse_stylish(output, "spectral", "lint")
    result: list[Diagnostic] = []
    for item in data:
        if not isinstance(item, dict) or item.get("severity") not in (
            _SPECTRAL_ERROR,
            _SPECTRAL_WARNING,
        ):
            continue
        start = (item.get("range") or {}).get("start") or {}
        path = ".".join(str(p) for p in item.get("path") or [])
        message = str(item.get("message", ""))
        result.append(
            Diagnostic(
                tool="spectral",
                category="lint",
                file=str(item.get("source") or "") or None,
                line=start["line"] + 1 if isinstance(start.get("line"), int) else None,
                column=start["character"] + 1 if isinstance(start.get("character"), int) else None,
                severity="error" if item["severity"] == _SPECTRAL_ERROR else "warning",
                code=str(item.get("code") or "spectral"),
                message=clip(f"{message} ({path})" if path else message),
            )
        )
    del ctx
    return result


def parse_asyncapi(output: str, ctx: ParseContext) -> list[Diagnostic]:
    """`asyncapi validate`: нарушения в формате stylish; невалидный документ — ошибка схемы."""
    del ctx
    return _parse_stylish(output, "asyncapi", "compile")


def _parse_stylish(output: str, tool: str, category: DiagnosticCategory) -> list[Diagnostic]:
    result: list[Diagnostic] = []
    current_file: str | None = None
    for line in strip_ansi(output).splitlines():
        file = _FILE_RE.match(line)
        if file and not line.startswith(" "):
            current_file = file["file"]
            continue
        match = _STYLISH_RE.match(line)
        if not match or match["sev"] not in ("error", "warning"):
            continue
        message = match["msg"]
        if match["path"]:
            message += f" ({match['path'].strip()})"
        result.append(
            Diagnostic(
                tool=tool,
                category=category,
                file=current_file,
                line=int(match["line"]),
                column=int(match["col"]),
                severity="error" if match["sev"] == "error" else "warning",
                code=match["code"],
                message=clip(message),
            )
        )
    return result
