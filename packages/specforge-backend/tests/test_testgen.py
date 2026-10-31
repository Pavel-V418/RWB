"""Офлайн-тесты этапа тестов backend-агента: разбор JUnit и пометка Skip (без LLM и dotnet).

    .venv/bin/python -m unittest tests.test_testgen
"""

import unittest

from specforge_backend.backend.testgen import apply_skips, parse_junit

JUNIT = """<testsuites><testsuite name="x">
<testcase name="App.IntegrationTests.Features.ZonesApiTests.List_ReturnsZones" classname="App.IntegrationTests.Features.ZonesApiTests" />
<testcase name="App.IntegrationTests.Features.ZonesApiTests.Lookup_Outside_Returns404" classname="App.IntegrationTests.Features.ZonesApiTests">
<failure message="Assert.Equal() Failure: Expected 404, Actual 200">at ZonesApiTests.cs:42</failure></testcase>
</testsuite></testsuites>"""

TESTS = '''public sealed class ZonesApiTests(AppFixture fixture)
{
    [Fact]
    public async Task List_ReturnsZones()
    {
    }

    [Fact]
    public async Task Lookup_Outside_Returns404()
    {
    }
}
'''


class TestgenTests(unittest.TestCase):
    def test_parse_junit_returns_only_failed_with_short_name(self):
        failed = parse_junit(JUNIT)
        self.assertEqual([f["test"] for f in failed], ["Lookup_Outside_Returns404"])
        self.assertIn("Expected 404", failed[0]["message"])

    def test_apply_skips_marks_only_failed_test_and_escapes_quotes(self):
        code = apply_skips(TESTS, [{"test": "Lookup_Outside_Returns404", "message": 'got "200"\n at line'}])
        self.assertEqual(code.count("[Fact(Skip"), 1)
        self.assertIn("[Fact]\n    public async Task List_ReturnsZones", code)
        self.assertIn("got '200' at line", code)
        self.assertIn('Lookup_Outside_Returns404()', code.split("[Fact(Skip")[1])


if __name__ == "__main__":
    unittest.main()
