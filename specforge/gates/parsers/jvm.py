"""Kotlin/Gradle: ошибки kotlinc и результаты тестов в JUnit XML."""

import re
import xml.etree.ElementTree as ET

from specforge.core.contracts import Diagnostic
from specforge.gates.parsers.common import (
    ParseContext,
    clip,
    find_source,
    fresh_reports,
    strip_ansi,
)

# e: file:///work/app/src/main/kotlin/Main.kt:12:5 Unresolved reference: foo
_KOTLINC_RE = re.compile(
    r"^(?P<sev>[ew]):\s+(?P<file>(?:file://)?\S+?\.kts?):(?P<line>\d+):(?P<col>\d+)\s+(?P<msg>.*)$"
)
# e: /work/app/src/main/kotlin/Main.kt: (12, 5): Unresolved reference: foo   (Kotlin < 1.9)
_KOTLINC_OLD_RE = re.compile(
    r"^(?P<sev>[ew]):\s+(?P<file>\S+?\.kts?):\s+\((?P<line>\d+),\s*(?P<col>\d+)\):\s+(?P<msg>.*)$"
)
# at app//com.example.OrderTest.total(OrderTest.kt:27)
_FRAME_RE = re.compile(
    r"at\s+(?:\S+//)?(?P<cls>[\w.$]+)\.[\w$<>-]+\((?P<file>[\w$-]+\.(?:kt|kts|java)):(?P<line>\d+)\)"
)
# кадры библиотек и рантайма — не место ошибки в коде проекта
_FRAMEWORK_PREFIXES = (
    "org.junit.",
    "org.opentest4j.",
    "java.",
    "jdk.",
    "kotlin.",
    "kotlinx.",
    "sun.",
    "org.gradle.",
    "io.mockk.",
    "org.mockito.",
)
_STACK_LINES = 6
REPORT_GLOBS = (
    "**/build/test-results/**/*.xml",
    "**/target/surefire-reports/*.xml",
    "**/TEST-*.xml",
)


def parse_kotlinc(output: str, ctx: ParseContext) -> list[Diagnostic]:
    del ctx
    result: list[Diagnostic] = []
    for line in strip_ansi(output).splitlines():
        match = _KOTLINC_RE.match(line) or _KOTLINC_OLD_RE.match(line)
        if match:
            result.append(
                Diagnostic(
                    tool="kotlinc",
                    category="compile",
                    file=match["file"],
                    line=int(match["line"]),
                    column=int(match["col"]),
                    severity="error" if match["sev"] == "e" else "warning",
                    message=clip(match["msg"]),
                )
            )
    return result


def parse_junit(output: str, ctx: ParseContext) -> list[Diagnostic]:
    """Упавшие тесты из JUnit XML (Gradle build/test-results, Maven surefire)."""
    del output
    result: list[Diagnostic] = []
    for path in fresh_reports(ctx, REPORT_GLOBS):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for case in root.iter("testcase"):
            problem = case.find("failure")
            if problem is None:
                problem = case.find("error")
            if problem is None:
                continue
            name = f"{case.get('classname', '?')}.{case.get('name', '?')}"
            stack = problem.text or ""
            file, line = _locate(stack, ctx)
            head = "\n".join(s.strip() for s in stack.strip().splitlines()[1 : _STACK_LINES + 1])
            kind = (problem.get("type") or "").rsplit(".", 1)[-1]
            reason = ": ".join(x for x in (kind, problem.get("message") or "") if x)
            text = f"тест {name} упал: {reason}"
            result.append(
                Diagnostic(
                    tool="junit",
                    category="test",
                    file=file,
                    line=line,
                    code="test",
                    message=clip(f"{text}\n{head}" if head else text),
                )
            )
    return result


def _locate(stack: str, ctx: ParseContext) -> tuple[str | None, int | None]:
    """Первый кадр стека из кода проекта; файл ищется в workspace по имени."""
    frames = [f for f in _FRAME_RE.finditer(stack) if not f["cls"].startswith(_FRAMEWORK_PREFIXES)]
    for frame in frames:
        found = find_source(ctx, frame["file"])
        if found:
            return found, int(frame["line"])
    if frames:
        return frames[0]["file"], int(frames[0]["line"])
    return None, None


def parse_gradle(output: str, ctx: ParseContext) -> list[Diagnostic]:
    """`./gradlew build|test`: компиляция Kotlin + тесты."""
    return parse_kotlinc(output, ctx) + parse_junit(output, ctx)
