"""Устойчивость: инфраструктурный сбой → повтор без ремонта, ошибка кода → ремонт, таймаут этапа →
откат к зелёному снимку, недоступная LLM → ожидание, failed этапа и resume."""

import dataclasses
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from specforge.core.agent_api import AgentContext
from specforge.core.contracts import AgentSpec, StageResult, StageStatus
from specforge.core.events import Event
from specforge.gates.infra import classify_failure
from specforge.llm import (
    ChatMessage,
    FakeLLM,
    FakeReply,
    FakeToolCall,
    LLMConfig,
    LLMEndpointDownError,
)
from specforge.orchestrator.graph import build_graph, resume_run
from specforge.orchestrator.nodes import execute_stage
from specforge.orchestrator.registry import Registry
from specforge.repair.snapshots import Snapshotter
from specforge.sandbox.local_sandbox import LocalSandbox
from tests.conftest import MakeCtx
from tests.test_orchestrator import FakeAgent, Harness, gated, spec

PY = f'"{sys.executable}"'
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="нужен git")


def kinds(events: list[Event], kind: str) -> list[Event]:
    return [e for e in events if e.kind == kind]


def test_classify_infra_vs_code_errors() -> None:
    assert classify_failure(137, 0.4, "Killed") is not None
    assert classify_failure(137, 600, "Killed") is None  # долгий процесс — не OOM при старте
    assert classify_failure(1, 2, "write /data: no space left on device") == "нет места на диске"
    assert classify_failure(1, 3, "Error: pull access denied for x/y") == "ошибка pull / registry"
    assert classify_failure(125, 1, "OCI runtime create failed: ...") == "упал контейнер / Docker"
    assert classify_failure(1, 2, "Program.cs(3,1): error CS1002: ; expected") is None
    assert classify_failure(0, 1, "no space left on device") is None


def test_oom_exit_code_retries_stage_without_repair(
    make_ctx: MakeCtx, events: list[Event], tmp_path: Path
) -> None:
    counter = tmp_path / "attempts.txt"
    gate = f"{PY} oom.py"
    script = (
        "import pathlib, sys\n"
        f"p = pathlib.Path(r'{counter}')\n"
        "n = int(p.read_text()) + 1 if p.exists() else 1\n"
        "p.write_text(str(n))\n"
        "sys.exit(137 if n == 1 else 0)\n"
    )
    agent = FakeAgent(
        spec(
            "svc",
            outputs=["oom.py"],
            gate_commands=[gate],
            gate_parsers={gate: "generic"},
            max_repairs=2,
        ),
        {"oom.py": script},
    )
    result = execute_stage(agent, make_ctx(FakeLLM([])))  # ремонт вызвал бы LLM — скрипт пуст

    assert result.status == StageStatus.passed, result.summary
    assert agent.calls == 2
    retries = kinds(events, "stage_retry")
    assert len(retries) == 1
    assert "OOM" in retries[0].message
    assert not kinds(events, "repair_attempt")


@needs_git
def test_compile_error_goes_to_repair_not_retry(make_ctx: MakeCtx, events: list[Event]) -> None:
    llm = FakeLLM(
        [
            FakeReply(
                tool_calls=[
                    FakeToolCall("str_replace", {"path": "value.txt", "old": "bad", "new": "good"})
                ]
            ),
            FakeReply(tool_calls=[FakeToolCall("finish", {"summary": "исправил"})]),
        ]
    )
    agent = gated("g")
    ctx = dataclasses.replace(make_ctx(llm), budget_tokens=500_000)
    result = execute_stage(agent, ctx)

    assert result.status == StageStatus.passed, result.summary
    assert agent.calls == 1
    assert kinds(events, "repair_attempt")
    assert not kinds(events, "stage_retry")
    assert result.gate is not None
    assert result.gate.infra_error is None


class SlowAgent:
    """Меняет файл и зависает дольше лимита этапа."""

    def __init__(self, name: str, timeout_s: float) -> None:
        self.spec = AgentSpec(
            name=name, stage=name, kind="tool", outputs=["*.txt"], timeout_s=timeout_s
        )

    def run(self, ctx: AgentContext) -> StageResult:
        (ctx.output_dir / "result.txt").write_text("сломано поздним этапом", "utf-8")
        time.sleep(3)
        return StageResult(agent=self.spec.name, status=StageStatus.passed)


@needs_git
def test_stage_timeout_rolls_back_to_green(
    make_ctx: MakeCtx, events: list[Event], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SPECFORGE_STAGE_TIMEOUT_GRACE_S", "0.2")
    ctx = make_ctx(FakeLLM([]))
    sandbox = LocalSandbox(ctx.output_dir, logs_dir=ctx.workdir / "logs")
    (ctx.output_dir / "result.txt").write_text("зелёная версия", "utf-8")
    Snapshotter(sandbox, "slow").mark_green()  # прошлый зелёный шлюз этого агента

    result = execute_stage(SlowAgent("slow", timeout_s=0.3), ctx)
    assert result.status == StageStatus.degraded  # артефакт после отката есть
    assert result.stopped_reason == "timeout"
    assert (ctx.output_dir / "result.txt").read_text("utf-8") == "зелёная версия"
    timeout = kinds(events, "stage_timeout")[0]
    assert timeout.data["restored_green"] is True

    fresh = make_ctx(FakeLLM([]))
    for leftover in fresh.output_dir.iterdir():
        if leftover.is_file():
            leftover.unlink()
    shutil.rmtree(fresh.output_dir / ".specforge", ignore_errors=True)
    no_green = execute_stage(SlowAgent("other", timeout_s=0.3), fresh)
    assert no_green.stopped_reason == "timeout"
    assert no_green.status == StageStatus.degraded  # файл записан до зависания, отката нет


class ChatAgent:
    def __init__(self, name: str, **kwargs: Any) -> None:
        self.spec = AgentSpec(
            name=name, stage=name, kind="structured", outputs=[f"{name}.txt"], **kwargs
        )

    def run(self, ctx: AgentContext) -> StageResult:
        from specforge.llm import LLMError  # noqa: PLC0415

        try:
            ctx.llm.chat([ChatMessage(role="user", content=self.spec.name)])
        except LLMError as exc:
            return StageResult(agent=self.spec.name, status=StageStatus.failed, summary=str(exc))
        (ctx.output_dir / f"{self.spec.name}.txt").write_text("ok", "utf-8")
        return StageResult(
            agent=self.spec.name, status=StageStatus.passed, artifacts=[f"{self.spec.name}.txt"]
        )


def down(n: int) -> list[Any]:
    return [LLMEndpointDownError("Connection refused")] * n


def test_llm_down_two_minutes_run_continues(tmp_path: Path) -> None:
    # 5 попыток с обычным backoff, затем ожидание 1+2+4+8+16+30+30+30 = 121 с
    llm = FakeLLM(
        [*down(5 + 7), "ok"],
        config=LLMConfig(model="fake", retry_backoff_s=1, retry_backoff_max_s=30),
    )
    sleeps: list[float] = []
    llm._sleep = sleeps.append  # type: ignore[method-assign]  # без настоящего ожидания

    h2 = Harness(tmp_path)
    state = h2.run([ChatAgent("analyst")], llm=llm, use_cache=False)

    assert state["stage_results"]["analyst"].status == StageStatus.passed
    waits = kinds(h2.events, "llm_unavailable")
    assert len(waits) == 8
    assert sum(sleeps[5:]) >= 120
    assert waits[-1].stage == "analyst"
    assert kinds(h2.events, "llm_available")
    assert kinds(h2.events, "run_finished")


def test_llm_down_longer_than_limit_fails_stage_and_resume_continues(tmp_path: Path) -> None:
    config = LLMConfig(model="fake", max_retries=0, retry_backoff_s=1, wait_max_s=10)
    llm = FakeLLM([*down(100)], config=config)
    llm._sleep = lambda _: None  # type: ignore[method-assign]
    h = Harness(tmp_path)
    agents = [ChatAgent("analyst"), ChatAgent("writer", depends_on=["analyst"])]
    state = h.run(agents, llm=llm, use_cache=False)

    analyst = state["stage_results"]["analyst"]
    assert (analyst.status, analyst.stopped_reason) == (StageStatus.failed, "llm_unavailable")
    assert state["stage_results"]["writer"].status == StageStatus.skipped
    assert kinds(h.events, "run_finished")  # прогон не упал

    healthy = FakeLLM(["a", "b"])
    registry = Registry(agents)
    cfg = h.config(llm=healthy, use_cache=False)
    resumed = resume_run(build_graph(registry, cfg), registry, cfg, run_id="r1")
    assert resumed["stage_results"]["analyst"].status == StageStatus.passed
    assert resumed["stage_results"]["writer"].status == StageStatus.passed
