"""Сколько тестов выполнила команда шлюза — по отчётам TRX и JUnit XML этого запуска.

Нужен, чтобы «шлюз зелёный» не путался с «тестов не было»: отчёт о тестировании показывает
число выполненных тестов (GateResult.tests_executed).
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from specforge.gates.parsers.common import ParseContext, fresh_reports

TRX_GLOBS = ("**/*.trx",)
JUNIT_GLOBS = (
    "**/build/test-results/**/*.xml",
    "**/target/surefire-reports/*.xml",
    "**/TEST-*.xml",
    "**/junit*.xml",
)


@dataclass
class TestCounts:
    __test__ = False  # не тестовый класс для pytest

    executed: int = 0
    failed: int = 0
    skipped: int = 0

    def add(self, other: "TestCounts") -> "TestCounts":
        return TestCounts(
            self.executed + other.executed,
            self.failed + other.failed,
            self.skipped + other.skipped,
        )


def count_tests(ctx: ParseContext) -> TestCounts | None:
    """Сумма по свежим отчётам; None — отчётов нет."""
    total: TestCounts | None = None
    seen = set()
    for path in [*fresh_reports(ctx, TRX_GLOBS), *fresh_reports(ctx, JUNIT_GLOBS)]:
        if path in seen:
            continue
        seen.add(path)
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            continue
        counts = _trx(root) if path.suffix.lower() == ".trx" else _junit(root)
        if counts is not None:
            total = counts if total is None else total.add(counts)
    return total


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _trx(root: ET.Element) -> TestCounts | None:
    for el in root.iter():
        if _local(el.tag) == "Counters":
            total = int(el.get("total", 0) or 0)
            executed = int(el.get("executed", total) or 0)
            failed = int(el.get("failed", 0) or 0) + int(el.get("error", 0) or 0)
            return TestCounts(executed, failed, max(total - executed, 0))
    return None


def _junit(root: ET.Element) -> TestCounts | None:
    if _local(root.tag) not in ("testsuite", "testsuites"):
        return None
    counts = TestCounts()
    for case in root.iter("testcase"):
        if case.find("skipped") is not None:
            counts.skipped += 1
            continue
        counts.executed += 1
        if case.find("failure") is not None or case.find("error") is not None:
            counts.failed += 1
    return counts
