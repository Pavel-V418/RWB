"""Тесты генератора скриншот-тестов (без Gradle): py tools/android_gate/test_screens.py"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path


from specforge_mobile.tools.android_gate import screens as s

SAMPLE = '''package com.specforge.app.feature.orders

import androidx.compose.runtime.Composable

@Composable
fun OrderListScreenSample(state: String) {
    when (state) {
        "ready" -> OrderListScreen()
        "empty" -> OrderListScreen()
        else -> error("нет")
    }
}

@Composable
fun Other() {}
'''
SCREEN = "package com.specforge.app.feature.profile\n\n@Composable\nfun ProfileScreen(state: Int) {}\n"


def project(tmp: Path) -> Path:
    base = tmp / s.FEATURE_MAIN
    (base / "orders").mkdir(parents=True)
    (base / "orders/OrderListSamples.kt").write_text(SAMPLE, encoding="utf-8")
    (base / "profile").mkdir()
    (base / "profile/ProfileScreen.kt").write_text(SCREEN, encoding="utf-8")
    return tmp


SCREENS = [{"screenId": "order-list", "states": ["ready", "empty", "error"], "requirements": ["FR-3"]},
           {"screenId": "profile", "states": ["ready"], "requirements": []}]


def test_names():
    assert s.pascal("order-list") == "OrderList" and s.sample_name("checklist_item") == "ChecklistItemScreenSample"


def test_generate_imports_and_tests():
    with tempfile.TemporaryDirectory() as d:
        p = project(Path(d))
        text = s.generate(p, SCREENS).read_text(encoding="utf-8")
        assert "import com.specforge.app.feature.orders.OrderListScreenSample" in text
        assert "import com.specforge.app.feature.profile.ProfileScreenSample" in text   # ожидаемый пакет
        assert '@Req("FR-3") @Test fun `FR-3 order-list empty`() = shot("order-list", "empty")' in text
        assert '@Test fun profile_ready() = shot("profile", "ready") { ProfileScreenSample("ready") }' in text
        assert text.count("@Test") == 4


def test_check_points_to_kotlin_files():
    with tempfile.TemporaryDirectory() as d:
        p = project(Path(d))
        errs = s.check(p, SCREENS)
        by_file = {e["file"]: e for e in errs}
        assert len(errs) == 2
        miss_state = by_file[f"{s.FEATURE_MAIN}/orders/OrderListSamples.kt"]
        assert miss_state["line"] == 6 and "error" in miss_state["message"]
        no_sample = by_file[f"{s.FEATURE_MAIN}/profile/ProfileScreen.kt"]
        assert no_sample["line"] == 4 and "ProfileScreenSample" in no_sample["message"]


def test_verify_and_cli_format():
    with tempfile.TemporaryDirectory() as d:
        p = project(Path(d))
        (p / "screenshots").mkdir()
        for st in ("ready", "empty", "error"):
            (p / f"screenshots/order-list--{st}--mobile.png").write_bytes(b"")
        errs = s.verify(p, SCREENS)
        assert [e["file"] for e in errs] == [f"{s.FEATURE_MAIN}/profile/ProfileScreen.kt"]
        spec = p / "spec.json"
        spec.write_text(json.dumps({"screens": SCREENS}), encoding="utf-8")
        out = subprocess.run([sys.executable, str(Path(__file__).parent / "screens.py"), str(p), "--spec", str(spec),
                              "verify"], capture_output=True, text=True, encoding="utf-8")
        assert out.returncode == 1
        assert out.stdout.startswith(f"{s.FEATURE_MAIN}/profile/ProfileScreen.kt:4:1: error: ")


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok ", name)
            n += 1
    print(f"{n} тестов пройдено")
