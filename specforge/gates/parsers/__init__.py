"""Парсеры вывода инструментов проверки в Diagnostic.

Парсер для команды шлюза берётся из AgentSpec.gate_parsers[команда], иначе — по первому
совпавшему регулярному выражению из AUTO_RULES, иначе — generic.
"""

import re

from specforge.gates.parsers.api_specs import parse_asyncapi, parse_spectral
from specforge.gates.parsers.common import ParseContext, ParseFn
from specforge.gates.parsers.dotnet import parse_dotnet_test, parse_msbuild, parse_trx
from specforge.gates.parsers.generic import parse_generic
from specforge.gates.parsers.jvm import parse_gradle, parse_junit, parse_kotlinc
from specforge.gates.parsers.postgres import parse_psql
from specforge.gates.parsers.typescript import parse_eslint, parse_tsc

PARSERS: dict[str, ParseFn] = {
    "msbuild": parse_msbuild,
    "trx": parse_trx,
    "dotnet-test": parse_dotnet_test,
    "tsc": parse_tsc,
    "eslint": parse_eslint,
    "kotlinc": parse_kotlinc,
    "junit": parse_junit,
    "gradle": parse_gradle,
    "spectral": parse_spectral,
    "asyncapi": parse_asyncapi,
    "psql": parse_psql,
    "generic": parse_generic,
}

AUTO_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bdotnet\s+test\b"), "dotnet-test"),
    (re.compile(r"\bdotnet\s+(build|publish|msbuild|run)\b|\bmsbuild\b"), "msbuild"),
    (re.compile(r"\beslint\b|\bng\s+lint\b"), "eslint"),
    (re.compile(r"\btsc\b|\bng\s+build\b|\bvue-tsc\b"), "tsc"),
    (re.compile(r"\bgradlew?\b"), "gradle"),
    (re.compile(r"\bkotlinc\b"), "kotlinc"),
    (re.compile(r"\bspectral\b"), "spectral"),
    (re.compile(r"\basyncapi\b"), "asyncapi"),
    (re.compile(r"\bpsql\b"), "psql"),
]


def select_parser(command: str, explicit: dict[str, str] | None = None) -> str:
    """Имя парсера для команды шлюза."""
    if explicit and command in explicit:
        name = explicit[command]
        if name not in PARSERS:
            raise ValueError(f"неизвестный парсер шлюза {name!r}; доступны: {sorted(PARSERS)}")
        return name
    for pattern, name in AUTO_RULES:
        if pattern.search(command):
            return name
    return "generic"


__all__ = ["AUTO_RULES", "PARSERS", "ParseContext", "ParseFn", "select_parser"]
