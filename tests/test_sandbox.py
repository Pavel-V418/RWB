import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from specforge.core.agent_api import ToolAgent
from specforge.core.contracts import AgentSpec, StageStatus
from specforge.llm import FakeLLM, FakeReply, FakeToolCall
from specforge.orchestrator import nodes
from specforge.orchestrator.nodes import create_sandbox, execute_stage
from specforge.sandbox.base import Sandbox, SandboxError
from specforge.sandbox.docker_sandbox import CACHE_VOLUME_PREFIX, DockerSandbox
from specforge.sandbox.local_sandbox import LocalSandbox
from tests.conftest import MakeCtx

PY = f'"{sys.executable}"'
PROMPTS = Path(__file__).parent / "fixtures" / "prompts"


# --- LocalSandbox --------------------------------------------------------------------------------


def test_local_sandbox_exec_and_log(tmp_path: Path) -> None:
    with LocalSandbox(tmp_path / "w", logs_dir=tmp_path / "logs", log_prefix="t") as sandbox:
        assert isinstance(sandbox, Sandbox)
        sandbox.write_text("sub/a.txt", "привет")
        result = sandbox.exec(f'{PY} -c "import os; print(os.listdir())"', cwd="sub")
        assert result.exit_code == 0
        assert "a.txt" in result.stdout
        assert result.log_path is not None
        log = Path(result.log_path)
        assert log.name.startswith("t-001-")
        assert "cwd: /work/sub" in log.read_text("utf-8")
        assert sandbox.read_text("/work/sub/a.txt") == "привет"


def test_local_sandbox_temporary_workspace_removed(tmp_path: Path) -> None:
    sandbox = LocalSandbox()
    workspace = sandbox.workspace
    assert workspace.is_dir()
    sandbox.close()
    assert not workspace.exists()


def test_local_sandbox_timeout(tmp_path: Path) -> None:
    result = LocalSandbox(tmp_path).exec(f'{PY} -c "import time; time.sleep(5)"', timeout_s=0.5)
    assert result.timed_out
    assert result.exit_code == 124
    assert result.log_path is None  # без logs_dir лог не пишется


# --- DockerSandbox на поддельном клиенте ---------------------------------------------------------


class FakeContainer:
    def __init__(self) -> None:
        self.execs: list[dict[str, Any]] = []
        self.removed = False
        self.next_exit = 0

    def exec_run(self, cmd: list[str], **kwargs: Any) -> Any:
        self.execs.append({"cmd": cmd, **kwargs})
        return SimpleNamespace(exit_code=self.next_exit, output=(b"out\n", b"err\n"))

    def remove(self, force: bool = False) -> None:
        self.removed = force


class FakeDocker:
    def __init__(self) -> None:
        self.container = FakeContainer()
        self.run_kwargs: dict[str, Any] = {}
        self.containers = SimpleNamespace(run=self._run)

    def _run(self, image: str, **kwargs: Any) -> FakeContainer:
        self.run_kwargs = {"image": image, **kwargs}
        return self.container


def test_docker_sandbox_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_CPUS", "2")
    monkeypatch.setenv("SANDBOX_MEMORY", "4g")
    client = FakeDocker()
    sandbox = DockerSandbox(
        "specforge/dotnet:10", tmp_path / "w", logs_dir=tmp_path / "logs", client=client
    )

    run = client.run_kwargs
    assert run["image"] == "specforge/dotnet:10"
    assert run["working_dir"] == "/work"
    assert run["volumes"][str((tmp_path / "w").resolve())] == {"bind": "/work", "mode": "rw"}
    caches = {k for k in run["volumes"] if k.startswith(CACHE_VOLUME_PREFIX)}
    assert caches == {f"{CACHE_VOLUME_PREFIX}{n}" for n in ("nuget", "npm", "gradle", "pip")}
    assert run["environment"]["NUGET_PACKAGES"] == "/cache/nuget"
    assert run["nano_cpus"] == 2_000_000_000
    assert run["mem_limit"] == "4g"

    (tmp_path / "w" / "src").mkdir()
    result = sandbox.exec("dotnet build", timeout_s=60, cwd="src")
    assert (result.exit_code, result.stdout, result.stderr) == (0, "out\n", "err\n")
    assert result.log_path is not None
    executed = client.container.execs[0]
    assert executed["cmd"] == ["sh", "-c", "timeout -k 5 60 sh -c 'dotnet build'"]
    assert executed["workdir"] == "/work/src"
    assert executed["demux"] is True

    client.container.next_exit = 124
    assert sandbox.exec("sleep 100", timeout_s=1).timed_out

    sandbox.close()
    assert client.container.removed
    with pytest.raises(SandboxError, match="закрыта"):
        sandbox.exec("ls")
    sandbox.close()  # повторное закрытие безопасно


# --- выбор песочницы и этап ----------------------------------------------------------------------


def _spec(**kwargs: Any) -> AgentSpec:
    return AgentSpec(name="coder", stage="code", kind="tool", prompt_dir=str(PROMPTS), **kwargs)


def test_create_sandbox_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logs = tmp_path / "logs"
    assert isinstance(create_sandbox(_spec(), tmp_path, logs), LocalSandbox)

    client = FakeDocker()
    monkeypatch.setattr(
        nodes,
        "DockerSandbox",
        lambda image, ws, **kw: DockerSandbox(image, ws, client=client, **kw),
    )
    assert isinstance(create_sandbox(_spec(sandbox_image="img"), tmp_path, logs), DockerSandbox)

    monkeypatch.setenv("SPECFORGE_SANDBOX", "local")
    assert isinstance(create_sandbox(_spec(sandbox_image="img"), tmp_path, logs), LocalSandbox)
    monkeypatch.setenv("SPECFORGE_SANDBOX", "docker")
    with pytest.raises(SandboxError, match="нет sandbox_image"):
        create_sandbox(_spec(), tmp_path, logs)
    monkeypatch.setenv("SPECFORGE_SANDBOX", "vm")
    with pytest.raises(SandboxError, match="ожидается"):
        create_sandbox(_spec(), tmp_path, logs)


def test_execute_stage_runs_agent_and_gate_in_one_sandbox(
    make_ctx: MakeCtx, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[LocalSandbox] = []
    closed: list[bool] = []

    class TrackingSandbox(LocalSandbox):
        def close(self) -> None:
            closed.append(True)
            super().close()

    def factory(spec: AgentSpec, workspace: Path, logs_dir: Path) -> LocalSandbox:
        created.append(TrackingSandbox(workspace, logs_dir=logs_dir))
        return created[-1]

    monkeypatch.setattr(nodes, "create_sandbox", factory)

    class Coder(ToolAgent):
        spec = _spec(
            tools=["write_file"],
            gate_commands=[f"{PY} -c \"assert open('hello.txt').read() == 'hi'\""],
        )

    llm = FakeLLM(
        [
            FakeReply(
                tool_calls=[FakeToolCall("write_file", {"path": "hello.txt", "content": "hi"})]
            ),
            FakeReply(tool_calls=[FakeToolCall("finish", {"summary": "ok"})]),
        ]
    )
    ctx = make_ctx(llm)
    result = execute_stage(Coder(), ctx)

    assert result.status == StageStatus.passed, result.summary
    assert result.gate is not None
    assert result.gate.passed
    assert len(created) == 1
    assert closed == [True]
    assert created[0].workspace == ctx.output_dir
    assert llm.calls[0].messages[0].content.startswith("Ты — тестовый агент")


def test_execute_stage_reports_sandbox_failure(
    make_ctx: MakeCtx, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(spec: AgentSpec, workspace: Path, logs_dir: Path) -> LocalSandbox:
        raise SandboxError("docker недоступен")

    monkeypatch.setattr(nodes, "create_sandbox", broken)

    class Coder(ToolAgent):
        spec = _spec()

    result = execute_stage(Coder(), make_ctx(FakeLLM()))
    assert result.status == StageStatus.failed
    assert "docker недоступен" in result.summary
