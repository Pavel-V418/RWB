"""GateRunner на LocalSandbox: команды печатают реальный вывод инструментов из фикстур."""

import sys
from pathlib import Path

import pytest

from specforge.core.contracts import AgentSpec, Diagnostic
from specforge.gates.runner import GateConfigError, GateRunner, normalize, prioritize
from specforge.sandbox.local_sandbox import LocalSandbox

PY = f'"{sys.executable}"'
FIXTURES = Path(__file__).parent / "fixtures" / "gates"


def emit(fixture: str, exit_code: int = 1) -> str:
    """Команда, печатающая фикстуру и завершающаяся с кодом exit_code."""
    path = (FIXTURES / fixture).as_posix()
    script = (
        f"import sys; sys.stdout.reconfigure(encoding='utf-8'); "
        f"sys.stdout.write(open('{path}', encoding='utf-8').read()); sys.exit({exit_code})"
    )
    return f'{PY} -c "{script}"'


def spec(commands: list[str], parsers: dict[str, str] | None = None) -> AgentSpec:
    return AgentSpec(
        name="coder", stage="code", kind="tool", gate_commands=commands, gate_parsers=parsers or {}
    )


@pytest.fixture
def sandbox(tmp_path: Path) -> LocalSandbox:
    return LocalSandbox(tmp_path / "work", logs_dir=tmp_path / "sandbox-logs")


@pytest.fixture
def runner(tmp_path: Path) -> GateRunner:
    return GateRunner(runs_dir=tmp_path / "runs")


def write_source(sandbox: LocalSandbox, rel: str, lines: int = 40) -> None:
    target = sandbox.workspace / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(f"line {i}" for i in range(1, lines + 1)) + "\n", "utf-8")


def test_all_green(runner: GateRunner, sandbox: LocalSandbox, tmp_path: Path) -> None:
    result = runner.run(spec([f"{PY} -c pass", f"{PY} -c pass"]), sandbox, "r1")
    assert result.passed
    assert result.diagnostics == []
    assert result.total_errors == 0
    assert result.log_path == str(tmp_path / "runs" / "r1" / "logs" / "coder.gate.log")


def test_msbuild_errors_with_code_window(runner: GateRunner, sandbox: LocalSandbox) -> None:
    write_source(sandbox, "src/Api/Program.cs")
    command = emit("msbuild_build.txt")
    result = runner.run(spec([command], {command: "msbuild"}), sandbox, "r1")

    assert not result.passed
    assert result.total_errors == 4  # дубли из сводки «Build FAILED» схлопнуты
    first = result.diagnostics[0]
    assert (first.file, first.line, first.code) == ("src/Api/Program.cs", 12, "CS1002")
    assert first.code_window is not None
    window = first.code_window.splitlines()
    assert len(window) == 15
    assert window[0] == "     5 | line 5"
    assert window[7] == ">   12 | line 12"
    assert window[-1] == "    19 | line 19"
    assert result.diagnostics[-1].severity == "warning"  # предупреждения — после ошибок
    others = [d for d in result.diagnostics if d.file and d.file != "src/Api/Program.cs"]
    assert all(d.code_window is None for d in others)  # файла нет в workspace


def test_stops_at_first_failing_command(
    runner: GateRunner, sandbox: LocalSandbox, tmp_path: Path
) -> None:
    marker = sandbox.workspace / "third-ran.txt"
    third = f"{PY} -c \"open('third-ran.txt', 'w').write('x')\""
    result = runner.run(spec([f"{PY} -c pass", emit("tsc.txt"), third]), sandbox, "r1")
    assert not result.passed
    assert not marker.exists()
    log = Path(result.log_path or "").read_text("utf-8")
    assert log.count("$ ") == 2
    assert "parser: generic" in log  # python -c … без явного парсера


def test_explicit_parsers_and_priority(runner: GateRunner, sandbox: LocalSandbox) -> None:
    lint = emit("eslint.json", exit_code=0)
    build = emit("ng_build_esbuild.txt", exit_code=0)
    tests = emit("dotnet_test_console.txt")
    parsers = {lint: "eslint", build: "tsc", tests: "trx"}
    write_source(sandbox, "src/app/orders/order-list.component.ts")
    result = runner.run(spec([lint, build, tests], parsers), sandbox, "r1")

    assert not result.passed
    categories = [d.category for d in result.diagnostics]
    assert categories == sorted(categories, key=["compile", "test", "lint"].index)
    assert [(d.category, d.severity) for d in result.diagnostics[:3]] == [
        ("compile", "error"),
        ("compile", "error"),
        ("compile", "warning"),
    ]
    assert result.diagnostics[0].code_window is not None
    eslint_files = {d.file for d in result.diagnostics if d.tool == "eslint"}
    assert "web/src/app/orders/order.service.ts" in eslint_files  # /work/… → относительный
    assert result.total_errors == 2 + 2 + 2


def test_limit_twenty_and_total_errors(runner: GateRunner, sandbox: LocalSandbox) -> None:
    errors = "\n".join(
        f"src/f{i}.ts:{i}:1 - error TS2304: Cannot find name 'x{i}'." for i in range(1, 31)
    )
    (sandbox.workspace / "errors.txt").write_text(errors, "utf-8")
    command = f"{PY} -c \"import sys; sys.stdout.write(open('errors.txt').read()); sys.exit(2)\""
    result = runner.run(spec([command], {command: "tsc"}), sandbox, "r1")
    assert len(result.diagnostics) == 20
    assert result.total_errors == 30
    assert result.diagnostics[0].code == "TS2304"


def test_fallbacks_when_parser_finds_nothing(runner: GateRunner, sandbox: LocalSandbox) -> None:
    generic = emit("generic_npm.txt")
    result = runner.run(spec([generic], {generic: "tsc"}), sandbox, "r1")
    assert result.diagnostics
    assert {d.category for d in result.diagnostics} == {"generic"}

    silent = f'{PY} -c "raise SystemExit(7)"'
    result = runner.run(spec([silent]), sandbox, "r2")
    (only,) = result.diagnostics
    assert only.code == "exit:7"
    assert "завершилась с кодом 7" in only.message


def test_timeout(sandbox: LocalSandbox, tmp_path: Path) -> None:
    runner = GateRunner(runs_dir=tmp_path / "runs", timeout_s=0.5)
    result = runner.run(spec([f'{PY} -c "import time; time.sleep(5)"']), sandbox, "r1")
    assert not result.passed
    assert "таймаут" in result.diagnostics[0].message


def test_junit_reports_from_sandbox(runner: GateRunner, sandbox: LocalSandbox) -> None:
    write_source(sandbox, "mobile/app/src/test/java/com/example/app/OrderRepositoryTest.kt")
    report = FIXTURES / "TEST-com.example.app.OrderRepositoryTest.xml"
    target = "mobile/app/build/test-results/test/TEST-x.xml"
    command = (
        f'{PY} -c "import os, shutil, sys; '
        f"os.makedirs(os.path.dirname('{target}'), exist_ok=True); "
        f"shutil.copy('{report.as_posix()}', '{target}'); print('BUILD FAILED'); sys.exit(1)\""
    )
    result = runner.run(spec([command], {command: "gradle"}), sandbox, "r1")
    first = result.diagnostics[0]
    assert (first.category, first.line) == ("test", 27)
    assert first.code_window is not None
    assert ">   27 | line 27" in first.code_window


def test_unknown_parser_is_config_error(runner: GateRunner, sandbox: LocalSandbox) -> None:
    with pytest.raises(GateConfigError, match="cobol"):
        runner.run(spec(["make"], {"make": "cobol"}), sandbox, "r1")


def test_normalize_paths(tmp_path: Path) -> None:
    def norm(file: str) -> str | None:
        return normalize(Diagnostic(tool="t", file=file, message="m"), tmp_path).file

    assert norm("/work/src/a.cs") == "src/a.cs"
    assert norm("file:///work/app/Main.kt") == "app/Main.kt"
    assert norm("./src/a.ts") == "src/a.ts"
    assert norm("src/a.ts") == "src/a.ts"
    assert norm("/usr/lib/x.h") == "/usr/lib/x.h"
    host = (tmp_path / "src" / "b.cs").as_posix()
    if host[1] == ":":  # Windows: путь хоста внутри workspace
        assert norm(host) == "src/b.cs"
        assert norm(host.replace("/", "\\")) == "src/b.cs"


def test_prioritize_dedups_and_orders() -> None:
    def d(category: str, severity: str = "error", message: str = "m") -> Diagnostic:
        return Diagnostic(tool="t", category=category, severity=severity, message=message)  # type: ignore[arg-type]

    items = [
        d("lint"),
        d("test"),
        d("compile", "warning"),
        d("compile"),
        d("compile"),
        d("generic"),
    ]
    selected, total = prioritize(items, 3)
    assert [(x.category, x.severity) for x in selected] == [
        ("compile", "error"),
        ("compile", "warning"),
        ("test", "error"),
    ]
    assert total == 4  # дубль compile/error посчитан один раз
