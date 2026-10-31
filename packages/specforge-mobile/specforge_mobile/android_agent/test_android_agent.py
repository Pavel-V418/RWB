"""
Тесты механики Android-агента без LLM и без Gradle (секунды):
    python -m unittest specforge_mobile.android_agent.test_android_agent -v
"""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from specforge_mobile.android_agent import context as ctx
from specforge_mobile.android_agent import workspace as ws
from specforge_mobile.android_agent.agent import DEFAULT_TEMPLATE, related_files, run
from specforge_mobile.android_agent.llm import LLMReply
from specforge_mobile.android_agent.replay import ReplayLLM

REPO = Path(__file__).resolve().parents[2]
CASE1 = REPO / "reference/case1"
FEATURE_MODULE = f"{ctx.FEATURE_MAIN}/FeatureModule.kt"
CORE_FILE = "app/src/main/kotlin/com/specforge/app/core/ui/UiState.kt"


class ParseAndPaths(unittest.TestCase):
    def test_file_blocks_fences_and_prefix(self):
        text = ("мусор\n<<<FILE android/app/src/main/res/values/strings_x.xml>>>\n```xml\n<resources/>\n```\n<<<END>>>\n"
                "<<<FILE app/a.kt>>>\nfun a() = 1\n<<<END>>>\n<<<FILE app/b.kt>>>\nобрезано")
        files, warnings = ws.parse_files(text)
        self.assertEqual(files["app/src/main/res/values/strings_x.xml"], "<resources/>\n")
        self.assertEqual(files["app/a.kt"], "fun a() = 1\n")
        self.assertEqual(len(warnings), 1)

    def test_fenced_code_without_file_markers(self):
        text = ("Вот исправление:\n```kotlin\npackage com.specforge.app.feature.orders\n\nimport x.Y\n\n"
                "data class Order(val id: String)\nclass OrdersViewModel\n```\n"
                "```kotlin\npackage com.specforge.app.feature\n\nclass OrdersTest {\n    @Test fun a() {}\n}\n```")
        files, warnings = ws.parse_files(text)
        self.assertIn(f"{ctx.FEATURE_MAIN}/orders/Order.kt", files)
        self.assertIn(f"{ctx.FEATURE_TEST}/OrdersTest.kt", files)
        self.assertTrue(warnings)
        existing = {f"{ctx.FEATURE_MAIN}/orders/OrdersViewModel.kt"}
        self.assertIn(f"{ctx.FEATURE_MAIN}/orders/OrdersViewModel.kt", ws.parse_files(text, existing)[0])

    def test_only_contract_paths_allowed(self):
        self.assertTrue(ws.is_allowed(f"{ctx.FEATURE_MAIN}/orders/OrdersScreen.kt"))
        self.assertTrue(ws.is_allowed(f"{ctx.FEATURE_TEST}/OrdersViewModelTest.kt"))
        self.assertTrue(ws.is_allowed("app/src/main/res/values/strings_orders.xml"))
        for bad in (CORE_FILE, "app/build.gradle.kts", "app/src/main/AndroidManifest.xml",
                    f"{ctx.FEATURE_MAIN}/../core/X.kt", "gradle/libs.versions.toml"):
            self.assertFalse(ws.is_allowed(bad), bad)


class Inputs(unittest.TestCase):
    def test_spec_variants(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "spec.json"
            p.write_text(json.dumps({"screens": [{"id": "a", "states": [{"state": "ready"}, "error"]}, {"screenId": "b"}]}))
            _, screens = ctx.load_screen_spec(Path(d), p)
            self.assertEqual(screens[0], {"screenId": "a", "states": ["ready", "error"], "title": "a", "requirements": []})
            self.assertEqual(screens[1]["states"], ctx.DEFAULT_STATES)
            p.write_text(json.dumps({"orders": {"states": ["ready"]}}))
            self.assertEqual(ctx.load_screen_spec(Path(d), p)[1][0]["screenId"], "orders")

    def test_api_digest_has_endpoints_and_summaries(self):
        digest = ctx.api_digest(DEFAULT_TEMPLATE)
        self.assertIn("interface SessionsApi", digest)
        self.assertIn("suspend fun extendSession", digest)
        self.assertIn("Продлить", digest)

    def test_core_digest_without_bodies(self):
        core = ctx.core_digest(DEFAULT_TEMPLATE)
        self.assertIn("fun <T> StateContainer(", core)
        self.assertIn("suspend fun <T> apiCall(", core)
        self.assertNotIn("CircularProgressIndicator", core)   # тела функций не попадают


class BrokenThenFixedLLM(ReplayLLM):
    """Слой данных приходит с ошибкой и с попыткой изменить ядро; ремонт присылает эталон."""
    def __init__(self, *a):
        super().__init__(*a)
        self.prompts = []

    def chat(self, messages, **kw):
        self.prompts.append(messages[-1]["content"])
        reply = super().chat(messages, **kw)
        if messages[-1]["content"].startswith("Шаг 1"):
            broken = reply.text.replace("class FeatureModule(", "class FeatureModule(oops ")
            broken += f"\n<<<FILE {CORE_FILE}>>>\nсломано\n<<<END>>>"
            return LLMReply(broken, 100, 100)
        return reply


class AgentLoop(unittest.TestCase):
    def test_repair_loop_contract_and_report(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "06_android"
            llm = BrokenThenFixedLLM(DEFAULT_TEMPLATE, ["login", "session"])
            calls = []

            def gate(project: Path) -> dict:
                calls.append(project)
                if len(calls) == 1:
                    (project / CORE_FILE).write_text("испорчено", encoding="utf-8")  # должен откатиться
                    return {"passed": False, "errors": [{"tool": "kotlinc", "file": FEATURE_MODULE, "line": 16,
                                                         "column": 20, "severity": "error", "category": "compile",
                                                         "message": "Expecting ')'"}]}
                shots = project / "screenshots"
                shots.mkdir()
                for name in ("login--ready", "login--error", "session--ready", "session--loading", "session--empty",
                             "session--error", "session--forbidden"):
                    (shots / f"{name}--mobile.png").write_bytes(b"png")
                return {"passed": True, "errors": []}

            result = run(CASE1, out, llm, gate=gate, log=lambda _: None)

            self.assertTrue(result["passed"], result["errors"])
            self.assertEqual(result["attempts"], 2)
            self.assertGreater(result["tokens"], 0)
            project = out / "project"
            self.assertEqual((project / CORE_FILE).read_bytes(), (DEFAULT_TEMPLATE / CORE_FILE).read_bytes())
            self.assertTrue(any("отклонены пути" in n for n in result["notes"]))
            repair = next(p for p in llm.prompts if p.startswith("Ремонт"))
            self.assertIn("Expecting ')'", repair)
            self.assertIn("class FeatureModule(oops", repair)            # упавший файл передан целиком
            self.assertTrue((out / "screenshots/session--forbidden--mobile.png").is_file())
            self.assertTrue((out / "gate_report.json").is_file() and (out / "plan.json").is_file())

    def test_gives_up_after_max_repairs(self):
        with tempfile.TemporaryDirectory() as d:
            llm = ReplayLLM(DEFAULT_TEMPLATE, ["login", "session"])
            fail = {"passed": False, "errors": [{"tool": "gradle", "file": "", "line": 0, "column": 0,
                                                 "severity": "error", "category": "build", "message": "x"}]}
            result = run(CASE1, Path(d), llm, gate=lambda p: dict(fail), max_repairs=2, log=lambda _: None)
            self.assertFalse(result["passed"])
            self.assertEqual(result["attempts"], 3)

    def test_related_files_found_by_identifier_in_error(self):
        errs = [{"message": "Unresolved reference 'FeatureModule'."}]
        found = related_files(DEFAULT_TEMPLATE, errs, set())
        self.assertEqual(found, [f"{ctx.FEATURE_MAIN}/FeatureModule.kt"])

    def test_missing_sample_stops_before_gradle(self):
        """Нет ScreenSample — Gradle не запускается, ошибка указывает на Kotlin-файл экрана."""
        class NoSessionSample(ReplayLLM):
            def chat(self, messages, **kw):
                reply = super().chat(messages, **kw)
                return LLMReply(re.sub(r"\n@Composable\nfun SessionScreenSample.*?\n}\n", "\n", reply.text, flags=re.S),
                                1, 1)
        with tempfile.TemporaryDirectory() as d:
            gate_calls = []
            result = run(CASE1, Path(d), NoSessionSample(DEFAULT_TEMPLATE, ["login", "session"]),
                         gate=lambda p: gate_calls.append(p) or {"passed": True, "errors": []},
                         max_repairs=0, log=lambda _: None)
            self.assertEqual(gate_calls, [])
            err = result["errors"][0]
            self.assertIn("SessionScreenSample", err["message"])
            self.assertTrue(err["file"].endswith("SessionScreen.kt") or err["file"].endswith("SessionSamples.kt"), err)

if __name__ == "__main__":
    unittest.main()
