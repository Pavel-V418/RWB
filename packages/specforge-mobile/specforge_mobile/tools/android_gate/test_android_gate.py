"""Тесты шлюза без Gradle: подсовываем логи и JUnit XML. Запуск: python -m pytest -q  (или python test_android_gate.py)"""
import json
import sys
import tempfile
import textwrap
from pathlib import Path


from specforge_mobile.tools.android_gate import android_gate as g


def make_project() -> Path:
    root = Path(tempfile.mkdtemp())
    src = root / "app/src/main/kotlin/com/specforge/app/feature/session"
    src.mkdir(parents=True)
    (src / "SessionScreen.kt").write_text("\n".join(f"line {i}" for i in range(1, 31)), encoding="utf-8")
    core = root / "app/src/main/kotlin/com/specforge/app/core"
    core.mkdir(parents=True)
    (core / "Core.kt").write_text("package core\n", encoding="utf-8")
    test = root / "app/src/test/kotlin/com/specforge/app/feature"
    test.mkdir(parents=True)
    (test / "SessionViewModelTest.kt").write_text("\n" * 50, encoding="utf-8")
    (root / "settings.gradle.kts").write_text("rootProject.name='x'\n", encoding="utf-8")
    return root


def test_kotlin_errors_new_and_old_format():
    root = make_project()
    f = (root / "app/src/main/kotlin/com/specforge/app/feature/session/SessionScreen.kt").resolve()
    log = textwrap.dedent(f"""
        > Task :app:compileDebugKotlin FAILED
        e: {f.as_uri()}:12:7 Unresolved reference 'Foo'.
        w: {f.as_uri()}:3:1 Parameter 'x' is never used
        e: {f}: (20, 3): Type mismatch: inferred type is Int but String was expected
        e: {f.as_uri()}:12:7 Unresolved reference 'Foo'.
    """)
    errs = g.parse_build_log(log, root)
    assert len(errs) == 2, errs                       # предупреждение и дубль отброшены
    e = errs[0]
    assert e["file"] == "app/src/main/kotlin/com/specforge/app/feature/session/SessionScreen.kt"
    assert (e["line"], e["column"], e["tool"], e["category"]) == (12, 7, "kotlinc", "compile")
    assert ">  12 | line 12" in e["code_window"]
    assert errs[1]["line"] == 20 and "Type mismatch" in errs[1]["message"]


def test_windows_file_uri():
    root = Path("C:/proj")
    assert g.rel("file:///C:/proj/app/A.kt", root).endswith("app/A.kt")


def test_aapt_error():
    root = make_project()
    log = f"ERROR: {root}/app/src/main/res/values/strings.xml:7: AAPT: error: resource string/x not found."
    errs = g.parse_build_log(log, root)
    assert errs[0]["category"] == "resource" and errs[0]["line"] == 7
    assert errs[0]["file"] == "app/src/main/res/values/strings.xml"


def test_junit_failure_points_to_test_line():
    root = make_project()
    res = root / "app/build/test-results/testDebugUnitTest"
    res.mkdir(parents=True)
    (res / "TEST-x.xml").write_text(textwrap.dedent("""
        <testsuite name="x" tests="2" failures="1">
          <testcase classname="com.specforge.app.feature.SessionViewModelTest" name="таймер идёт"/>
          <testcase classname="com.specforge.app.feature.SessionViewModelTest" name="старт сессии">
            <failure message="expected:&lt;1&gt; but was:&lt;2&gt;" type="java.lang.AssertionError">java.lang.AssertionError: expected:&lt;1&gt; but was:&lt;2&gt;
        at org.junit.Assert.fail(Assert.java:89)
        at com.specforge.app.feature.SessionViewModelTest$старт$1.invokeSuspend(SessionViewModelTest.kt:42)
            </failure>
          </testcase>
        </testsuite>
    """).strip(), encoding="utf-8")
    errs, stats = g.parse_junit(root / "app/build/test-results", root)
    assert stats == {"tests": 2, "failures": 1, "skipped": 0}
    assert errs[0]["file"].endswith("SessionViewModelTest.kt") and errs[0]["line"] == 42
    assert "expected:<1> but was:<2>" in errs[0]["message"]
    assert errs[0]["category"] == "test"


def test_stack_frame_with_spaces_in_test_name():
    trace = ("\tat com.specforge.app.feature.LoginViewModelTest.FR-1 повторное нажатие игнорируется"
             "(LoginViewModelTest.kt:98)\n\tat com.x.A.b$lambda$1(A.kt:5)")
    assert g.STACK_FRAME.findall(trace) == [("com.specforge.app.feature.LoginViewModelTest", "LoginViewModelTest.kt", "98"),
                                            ("com.x.A", "A.kt", "5")]


def test_frozen_contract():
    root = make_project()
    assert g.write_frozen_manifest(root, ["app/src/main/kotlin/com/specforge/app/core/**/*.kt"]) == 1
    assert g.frozen_violations(root) == []
    (root / "app/src/main/kotlin/com/specforge/app/core/Core.kt").write_text("changed", encoding="utf-8")
    v = g.frozen_violations(root)
    assert len(v) == 1 and v[0]["category"] == "contract"


def test_gradle_failure_without_file():
    root = make_project()
    log = textwrap.dedent("""
        FAILURE: Build failed with an exception.

        * What went wrong:
        Could not resolve all files for configuration ':app:debugRuntimeClasspath'.
        > Could not find com.example:missing:1.0.

        * Try:
        > Run with --info
        BUILD FAILED in 3s
    """)
    result = g.evaluate(root, 1, log)
    assert not result["passed"]
    assert result["errors"][0]["category"] == "build"
    assert "Could not find com.example:missing" in result["errors"][0]["message"]


def test_oom_is_explained():
    root = make_project()
    result = g.evaluate(root, 1, "java.lang.OutOfMemoryError: Java heap space\nBUILD FAILED")
    assert "памяти" in result["errors"][0]["message"]


def test_success_report_shape():
    root = make_project()
    (root / "screenshots").mkdir()
    (root / "screenshots/session--ready--mobile.png").write_bytes(b"\x89PNG")
    result = g.evaluate(root, 0, "BUILD SUCCESSFUL in 40s")
    assert result["passed"] and result["errors"] == []
    assert result["artifacts"]["screenshots"] == ["screenshots/session--ready--mobile.png"]
    assert set(result) >= {"passed", "errors", "tokens", "attempts"}
    json.dumps(result)


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"{len(tests)} тестов пройдено")
