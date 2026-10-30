"""Ошибки psql при применении DDL (`psql -v ON_ERROR_STOP=1 -f schema.sql`)."""

import re

from specforge.core.contracts import Diagnostic
from specforge.gates.parsers.common import ParseContext, clip, strip_ansi

# psql:db/001_schema.sql:12: ERROR:  relation "items" already exists
_ERROR_RE = re.compile(
    r"^psql:(?P<file>[^:]+(?::\\[^:]+)?):(?P<line>\d+):\s+(?P<sev>ERROR|FATAL|WARNING):\s+(?P<msg>.*)$"
)
# ERROR:  syntax error at or near "TABL"   (без префикса psql: — команда из -c)
_BARE_RE = re.compile(r"^(?P<sev>ERROR|FATAL):\s+(?P<msg>.*)$")
_DETAIL_RE = re.compile(r"^(LINE \d+:|DETAIL:|HINT:|CONTEXT:|QUERY:|\s+\^)")
_SQLSTATE_RE = re.compile(r"^(?P<code>[0-9A-Z]{5}):\s+")


def parse_psql(output: str, ctx: ParseContext) -> list[Diagnostic]:
    del ctx
    result: list[Diagnostic] = []
    lines = strip_ansi(output).splitlines()
    i = 0
    while i < len(lines):
        match = _ERROR_RE.match(lines[i]) or _BARE_RE.match(lines[i])
        if not match:
            i += 1
            continue
        details: list[str] = []
        i += 1
        while i < len(lines) and _DETAIL_RE.match(lines[i]):
            details.append(lines[i].rstrip())
            i += 1
        message = match["msg"].strip()
        code = None
        sqlstate = _SQLSTATE_RE.match(message)  # \set VERBOSITY verbose даёт «42P07: …»
        if sqlstate:
            code = sqlstate["code"]
            message = message[sqlstate.end() :]
        groups = match.groupdict()
        result.append(
            Diagnostic(
                tool="psql",
                category="compile",
                file=groups.get("file"),
                line=int(groups["line"]) if groups.get("line") else None,
                severity="warning" if match["sev"] == "WARNING" else "error",
                code=code,
                message=clip("\n".join([message, *details])),
            )
        )
    return result
