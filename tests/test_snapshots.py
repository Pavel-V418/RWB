import shutil
import subprocess
from pathlib import Path

import pytest

from specforge.repair.snapshots import SnapshotError, Snapshotter
from specforge.sandbox.base import ExecResult
from specforge.sandbox.local_sandbox import LocalSandbox

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="нужен git")


@needs_git
def test_snapshot_restore_and_diff(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path / "work")
    ws = sandbox.workspace
    (ws / "a.txt").write_text("one\n", "utf-8")
    snaps = Snapshotter(sandbox)
    assert snaps.available
    sha = snaps.snapshot("before")

    (ws / "a.txt").write_text("two\n", "utf-8")
    (ws / "new.txt").write_text("x", "utf-8")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "dep.js").write_text("dep", "utf-8")

    assert snaps.changed_files(sha) == ["a.txt", "new.txt"]  # node_modules исключён
    assert "+two" in snaps.diff(sha)
    snaps.restore(sha)
    assert (ws / "a.txt").read_text("utf-8") == "one\n"
    assert not (ws / "new.txt").exists()
    assert (ws / "node_modules" / "dep.js").exists()  # зависимости не трогаются

    assert snaps.green() is None
    green = snaps.mark_green()
    assert snaps.green() == green
    assert Snapshotter(sandbox).green() == green  # состояние переживает новый объект


@needs_git
def test_parent_repository_is_untouched(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=parent, check=True)
    sandbox = LocalSandbox(parent / "work")
    (sandbox.workspace / "a.txt").write_text("x", "utf-8")
    Snapshotter(sandbox).snapshot("s")
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=parent, capture_output=True, text=True, check=False
    )
    assert log.stdout == ""  # в родительском репозитории коммитов не появилось
    assert (sandbox.workspace / ".specforge" / "git").is_dir()


def test_without_git_snapshots_are_disabled(tmp_path: Path) -> None:
    class NoGit(LocalSandbox):
        def exec(
            self, command: str, *, timeout_s: float | None = None, cwd: str | None = None
        ) -> ExecResult:
            return ExecResult(command=command, exit_code=127, stderr="git: not found")

    snaps = Snapshotter(NoGit(tmp_path))
    assert not snaps.available
    assert snaps.green() is None
    with pytest.raises(SnapshotError, match="нет git"):
        snaps.snapshot("x")
