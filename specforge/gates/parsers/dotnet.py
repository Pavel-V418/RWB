"""dotnet / MSBuild и результаты тестов (TRX и консольный вывод `dotnet test`)."""

import re
import xml.etree.ElementTree as ET

from specforge.core.contracts import Diagnostic
from specforge.gates.parsers.common import ParseContext, clip, fresh_reports, strip_ansi

# src/Api/Program.cs(12,5): error CS1002: ; expected [/work/src/Api/Api.csproj]
_MSBUILD_RE = re.compile(
    r"^\s*(?P<file>[^\s(][^(]*?)\((?P<line>\d+)(?:,(?P<col>\d+))?(?:,\d+,\d+)?\)\s*:\s*"
    r"(?P<sev>error|warning)\s+(?P<code>[A-Z]+\d+)\s*:\s*(?P<msg>.*?)(?:\s+\[[^\]]+\])?\s*$"
)
# MSBUILD : error MSB1009: Project file does not exist.   /   CSC : error CS2001: ...
_TOOL_ERROR_RE = re.compile(
    r"^\s*(?P<tool>[A-Za-z][\w.-]*)\s*:\s*(?P<sev>error|warning)\s+(?P<code>[A-Z]+\d+)\s*:\s*"
    r"(?P<msg>.*?)(?:\s+\[[^\]]+\])?\s*$"
)
# at Api.Tests.OrderTests.Total() in /work/tests/OrderTests.cs:line 27
_STACK_RE = re.compile(r" in (?P<file>.+?):line (?P<line>\d+)")
_TRX_NS = {"t": "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"}
_STACK_LINES = 6


def parse_msbuild(output: str, ctx: ParseContext) -> list[Diagnostic]:
    del ctx
    result: list[Diagnostic] = []
    for line in strip_ansi(output).splitlines():
        match = _MSBUILD_RE.match(line)
        if match:
            result.append(
                Diagnostic(
                    tool="msbuild",
                    category="compile",
                    file=match["file"].strip(),
                    line=int(match["line"]),
                    column=int(match["col"]) if match["col"] else None,
                    severity="error" if match["sev"] == "error" else "warning",
                    code=match["code"],
                    message=clip(match["msg"]),
                )
            )
            continue
        match = _TOOL_ERROR_RE.match(line)
        if match:
            result.append(
                Diagnostic(
                    tool="msbuild",
                    category="compile",
                    severity="error" if match["sev"] == "error" else "warning",
                    code=match["code"],
                    message=clip(f"{match['tool']}: {match['msg']}"),
                )
            )
    return result


def parse_trx(output: str, ctx: ParseContext) -> list[Diagnostic]:
    """Упавшие тесты из *.trx; если отчётов нет — из консольного вывода `dotnet test`."""
    result: list[Diagnostic] = []
    for path in fresh_reports(ctx, ("**/*.trx",)):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for test in root.iterfind(".//t:UnitTestResult", _TRX_NS):
            if test.get("outcome") != "Failed":
                continue
            message = test.findtext(".//t:ErrorInfo/t:Message", "", _TRX_NS)
            stack = test.findtext(".//t:ErrorInfo/t:StackTrace", "", _TRX_NS)
            result.append(_test_failure(test.get("testName", "?"), message, stack))
    if result:
        return result
    return _parse_console_failures(strip_ansi(output))


def parse_dotnet_test(output: str, ctx: ParseContext) -> list[Diagnostic]:
    """`dotnet test` сначала собирает проект: ошибки компиляции + упавшие тесты."""
    return parse_msbuild(output, ctx) + parse_trx(output, ctx)


def _parse_console_failures(text: str) -> list[Diagnostic]:
    #   Failed Api.Tests.OrderTests.Total [12 ms]
    #   Error Message:
    #    Assert.Equal() Failure ...
    #   Stack Trace:
    #      at ... in /work/tests/OrderTests.cs:line 27
    result: list[Diagnostic] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = re.match(r"^\s*Failed\s+(?P<name>[\w.<>`+\[\](), ]+?)\s+\[[^\]]*\]\s*$", lines[i])
        if not match:
            i += 1
            continue
        message: list[str] = []
        stack: list[str] = []
        section: list[str] | None = None
        i += 1
        while i < len(lines) and not re.match(r"^\s*(Failed|Passed|Skipped)\s+\S", lines[i]):
            stripped = lines[i].strip()
            if stripped == "Error Message:":
                section = message
            elif stripped == "Stack Trace:":
                section = stack
            elif stripped.startswith(("Standard Output Messages:", "Failed!", "Total tests:")):
                section = None
            elif section is not None and stripped:
                section.append(stripped)
            i += 1
        result.append(_test_failure(match["name"], "\n".join(message), "\n".join(stack)))
    return result


def _test_failure(name: str, message: str, stack: str) -> Diagnostic:
    file = line = None
    for frame in stack.splitlines():
        found = _STACK_RE.search(frame)
        if found:
            file, line = found["file"].strip(), int(found["line"])
            break
    stack_head = "\n".join(s.strip() for s in stack.strip().splitlines()[:_STACK_LINES])
    text = f"тест {name} упал: {message.strip()}"
    if stack_head:
        text += f"\n{stack_head}"
    return Diagnostic(
        tool="dotnet-test", category="test", file=file, line=line, code="test", message=clip(text)
    )
