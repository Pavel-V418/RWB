import os
import sys
from pathlib import Path

import pytest

from specforge.sandbox.base import SandboxPathError
from specforge.sandbox.local_sandbox import LocalSandbox
from specforge.tools import TOOLS, ToolEnv, ToolError, get_tools
from specforge.tools.base import glob_match, truncate_middle
from specforge.tools.shell import OUTPUT_HEAD_CHARS, OUTPUT_TAIL_CHARS

PY = f'"{sys.executable}"'


@pytest.fixture
def env(tmp_path: Path) -> ToolEnv:
    sandbox = LocalSandbox(tmp_path / "work", logs_dir=tmp_path / "logs")
    return ToolEnv(sandbox, protected_globs=["locked/**"])


def call(env: ToolEnv, name: str, **args: object) -> str:
    return TOOLS[name].fn(env, args).content  # type: ignore[arg-type]


def write(env: ToolEnv, path: str, text: str) -> None:
    target = env.sandbox.workspace / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, "utf-8")


# --- read_file -----------------------------------------------------------------------------------


def test_read_file_numbers_lines_and_limits(env: ToolEnv) -> None:
    write(env, "big.txt", "\n".join(f"line {i}" for i in range(1, 1001)))
    first = call(env, "read_file", path="big.txt")
    assert first.startswith("big.txt (строки 1–400 из 1000); дальше — read_file с start_line=401")
    assert "    1\tline 1" in first
    assert "  400\tline 400" in first
    assert "line 401" not in first
    middle = call(env, "read_file", path="/work/big.txt", start_line=995, end_line=2000)
    assert "строки 995–1000 из 1000" in middle
    assert " 1000\tline 1000" in middle


def test_read_file_errors(env: ToolEnv) -> None:
    write(env, "a.txt", "x\n")
    (env.sandbox.workspace / "bin.dat").write_bytes(b"\x00\x01")
    for args, message in [
        ({"path": "nope.txt"}, "не найден"),
        ({"path": "."}, "каталог"),
        ({"path": "a.txt", "start_line": 5}, "неверный диапазон"),
        ({"path": "bin.dat"}, "двоичный"),
        ({"path": "a.txt", "start_line": "abc"}, "целым числом"),
    ]:
        with pytest.raises(ToolError, match=message):
            TOOLS["read_file"].fn(env, args)  # type: ignore[arg-type]


# --- write_file / str_replace --------------------------------------------------------------------


def test_write_file_only_new(env: ToolEnv) -> None:
    result = TOOLS["write_file"].fn(env, {"path": "src/new.py", "content": "a\nb\n"})
    assert result.changed == "src/new.py"
    assert result.stub == "[write_file: src/new.py, 2 строк]"
    assert (env.sandbox.workspace / "src" / "new.py").read_text("utf-8") == "a\nb\n"
    with pytest.raises(ToolError, match="используй str_replace"):
        call(env, "write_file", path="src/new.py", content="x")
    with pytest.raises(ToolError, match="защищён"):
        call(env, "write_file", path="locked/a.txt", content="x")


def test_str_replace_requires_unique_match(env: ToolEnv) -> None:
    write(env, "a.py", "x = 1\ny = 1\nz = 2\n")
    with pytest.raises(ToolError, match="2 раз"):
        call(env, "str_replace", path="a.py", old=" = 1", new=" = 3")
    with pytest.raises(ToolError, match="не найден"):
        call(env, "str_replace", path="a.py", old="w = 9", new="")
    with pytest.raises(ToolError, match="пустым"):
        call(env, "str_replace", path="a.py", old="", new="q")
    out = call(env, "str_replace", path="a.py", old="y = 1", new="y = 10")
    assert "строка 2" in out
    assert "    2\ty = 10" in out
    assert (env.sandbox.workspace / "a.py").read_text("utf-8") == "x = 1\ny = 10\nz = 2\n"


def test_str_replace_keeps_crlf(env: ToolEnv) -> None:
    (env.sandbox.workspace / "w.txt").write_bytes(b"a\r\nb\r\n")
    call(env, "str_replace", path="w.txt", old="b", new="c")
    assert (env.sandbox.workspace / "w.txt").read_bytes() == b"a\r\nc\r\n"


# --- list_dir / grep -----------------------------------------------------------------------------


def test_list_dir_depth_and_skips(env: ToolEnv) -> None:
    write(env, "src/app/main.py", "")
    write(env, "src/app/deep/x.py", "")
    write(env, "node_modules/lib/index.js", "")
    write(env, "README.md", "")
    out = call(env, "list_dir", path=".", depth=2)
    assert out.splitlines() == [
        "./",
        "node_modules/ (пропущен)",
        "src/",
        "  app/",
        "README.md",
    ]


def test_grep_with_glob_and_limits(env: ToolEnv) -> None:
    write(env, "src/a.py", "def foo():\n    return 1\n")
    write(env, "src/b.ts", "function foo() {}\n")
    write(env, "node_modules/x.py", "def foo(): pass\n")
    assert call(env, "grep", pattern=r"foo\(", glob="**/*.py") == "src/a.py:1: def foo():"
    assert call(env, "grep", pattern="nothing") == "совпадений нет"
    write(env, "many.txt", "hit\n" * 500)
    many = call(env, "grep", pattern="hit")
    assert many.count("\n") == 100
    assert "первые 100" in many
    with pytest.raises(ToolError, match="регулярное"):
        call(env, "grep", pattern="(")


# --- run -----------------------------------------------------------------------------------------


def test_run_truncates_output_and_keeps_full_log(env: ToolEnv) -> None:
    script = "print('A' * 3000); print('M' * 5000); print('Z' * 5000)"
    result = TOOLS["run"].fn(env, {"cmd": f'{PY} -c "{script}"'})
    content = result.content
    assert "exit 0" in content
    assert "…[пропущено" in content
    assert "полный лог:" in content
    body = content.split("\n", 2)[2]  # без строк «$ команда» и «exit …»
    head, _, tail = body.partition("…[пропущено")
    assert head.count("A") == OUTPUT_HEAD_CHARS
    assert tail.rstrip().endswith("Z" * 100)
    assert len(content) < OUTPUT_HEAD_CHARS + OUTPUT_TAIL_CHARS + 500
    assert result.stub.startswith("[run: exit 0, 3 строк, лог: ")
    log_name = result.stub.split("лог: ")[1].rstrip("]")
    assert "/" not in log_name
    assert "\\" not in log_name
    assert str(env.sandbox.workspace.parent) not in content
    assert content.splitlines()[1] == "exit 0"  # без длительности
    log_path = env.sandbox.logs_dir / log_name  # type: ignore[attr-defined]  # LocalSandbox
    assert "M" * 5000 in log_path.read_text("utf-8")


def test_run_reports_exit_code_and_timeout(env: ToolEnv) -> None:
    assert "exit 3" in call(env, "run", cmd=f'{PY} -c "raise SystemExit(3)"')
    slow = call(env, "run", cmd=f'{PY} -c "import time; time.sleep(5)"', timeout=1)
    assert "превышен таймаут 1 с" in slow


def test_truncate_middle() -> None:
    assert truncate_middle("abc", 2, 2) == "abc"
    cut = truncate_middle("a" * 10 + "b" * 10, 3, 4, "лог: x")
    assert cut == "aaa\n…[пропущено 13 символов; лог: x]…\nbbbb"


# --- пути ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["../outside.txt", "/etc/passwd", "C:/Windows/win.ini", "/work/../outside.txt", "a/../../x"],
)
def test_paths_cannot_escape_work(env: ToolEnv, path: str) -> None:
    with pytest.raises(SandboxPathError, match="за пределы /work"):
        TOOLS["read_file"].fn(env, {"path": path})
    with pytest.raises(SandboxPathError):
        TOOLS["write_file"].fn(env, {"path": path, "content": "x"})


def test_symlink_outside_work_is_rejected(env: ToolEnv, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("пароль", "utf-8")
    link = env.sandbox.workspace / "link.txt"
    try:
        os.symlink(secret, link)
    except OSError:
        pytest.skip("нет прав на создание симлинков")
    with pytest.raises(SandboxPathError):
        call(env, "read_file", path="link.txt")
    assert call(env, "grep", pattern="пароль") == "совпадений нет"


def test_work_prefix_and_dot_paths(env: ToolEnv) -> None:
    write(env, "a.txt", "ok")
    for path in ("a.txt", "./a.txt", "/work/a.txt", "/work/./a.txt"):
        assert "ok" in call(env, "read_file", path=path)


def test_glob_match() -> None:
    assert glob_match("tests/contract/a/b.txt", "tests/contract/**")
    assert glob_match("a.py", "**/*.py")
    assert glob_match("src/x/a.py", "**/*.py")
    assert not glob_match("src/a.py", "*.py")
    assert glob_match("src/a.py", "src/?.py")


def test_get_tools() -> None:
    assert [t.name for t in get_tools(["run", "read_file", "run", "finish"])] == [
        "run",
        "read_file",
    ]
    with pytest.raises(ValueError, match="teleport"):
        get_tools(["teleport"])
