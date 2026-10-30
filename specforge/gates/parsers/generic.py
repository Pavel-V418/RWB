"""Запасной парсер: последние строки вывода со словами error / exception / failed."""

import re

from specforge.core.contracts import Diagnostic
from specforge.gates.parsers.common import ParseContext, clip, strip_ansi

TAIL_LINES = 200
MAX_HITS = 10
_HIT_RE = re.compile(r"\b(error|errors|exception|failed|failure|fatal)\b", re.IGNORECASE)
# «0 errors», «Failed: 0», «failed=0» — это сводки об успехе, а не ошибки
_ZERO_RE = re.compile(
    r"\b0\s+(errors?|failed|failures?)\b|\b(errors?|failed|failures?)\s*[:=]\s*0\b", re.IGNORECASE
)


def parse_generic(output: str, ctx: ParseContext) -> list[Diagnostic]:
    tool = ctx.command.split(maxsplit=1)[0] if ctx.command.strip() else "gate"
    tail = strip_ansi(output).splitlines()[-TAIL_LINES:]
    hits = [line.strip() for line in tail if _HIT_RE.search(line) and not _ZERO_RE.search(line)]
    return [
        Diagnostic(tool=tool, category="generic", code=f"exit:{ctx.exit_code}", message=clip(hit))
        for hit in hits[-MAX_HITS:]
    ]
