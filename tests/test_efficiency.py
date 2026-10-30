"""Этап 11 «эффективность»: стабильные префиксы промптов."""

import itertools
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from specforge.core.agent_api import AgentContext, ToolAgent
from specforge.core.contracts import AgentSpec, StageResult, StageStatus
from specforge.llm import ChatMessage, Expect, FakeLLM, FakeReply, FakeToolCall, LLMRequest
from specforge.llm.profiles import load_agent_settings
from specforge.paths import PROJECT_ROOT
from tests.test_orchestrator import Harness

PY = f'"{sys.executable}"'
SYSTEM = "Ты — инженер. Пиши код по заданию.\n" + "Правило: проверяй результат командой.\n" * 40
TASK = "Входы ($input_dir):\n$inputs\n\nРабочий каталог: $workdir, выходы — в $output_dir."


def call(name: str, **args: Any) -> FakeReply:
    return FakeReply(tool_calls=[FakeToolCall(name, args)])


@pytest.fixture
def coder(tmp_path: Path) -> ToolAgent:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "system.md").write_text(SYSTEM, "utf-8")
    (prompts / "task.md").write_text(TASK, "utf-8")

    class Coder(ToolAgent):
        spec = AgentSpec(
            name="coder",
            stage="code",
            kind="tool",
            inputs=["input/*.md"],
            outputs=["*.txt"],
            tools=["write_file", "run"],
            prompt_dir=str(prompts),
            max_repairs=0,
        )

    return Coder()


def script() -> FakeLLM:
    return FakeLLM(
        [
            call("write_file", path="notes.txt", content="готово"),
            call("run", cmd=f"{PY} -c \"print(open('notes.txt', encoding='utf-8').read())\""),
            call("finish", summary="записал заметки"),
        ]
    )


def serialized(request: LLMRequest) -> str:
    """Порядок как в чат-шаблоне Qwen: описания инструментов — в начале (в системном блоке),
    затем сообщения."""
    tools = [t.model_dump(mode="json") for t in request.tools]
    messages = [m.model_dump(mode="json") for m in request.messages]
    parts = [tools, *messages]
    return "\n".join(json.dumps(p, ensure_ascii=False, sort_keys=True) for p in parts)


def common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


def test_first_message_identical_across_runs_and_prefix_shared(
    tmp_path: Path, coder: ToolAgent
) -> None:
    runs = []
    for run_id in ("20261030-100000-aaaaaa", "20261031-235959-bbbbbb"):
        llm = script()
        (tmp_path / run_id).mkdir()
        state = Harness(tmp_path / run_id).run([coder], run_id=run_id, llm=llm, use_cache=False)
        assert state["stage_results"]["coder"].status.value == "passed"
        runs.append(llm.calls)

    first, second = runs
    assert serialized(first[0]) == serialized(second[0])  # байт-в-байт
    task = first[0].messages[1].content
    assert "aaaaaa" not in task
    assert "runs" not in task
    assert "artifacts" in task
    assert "/work" in task

    # история только дописывается: каждый запрос начинается с предыдущего целиком
    for previous, current in itertools.pairwise(first):
        assert current.messages[: len(previous.messages)] == previous.messages
        shared = common_prefix(serialized(previous), serialized(current))
        assert shared / len(serialized(current)) >= 0.8
    # между прогонами дальше первого запроса расходятся только id вызовов инструментов —
    # это часть ответа модели, а не префикса платформы
    assert len(first) == len(second) == 3


# --- 2. thinking по агентам ---------------------------------------------------------------------


class ChatAgent:
    """Агент без инструментов: один вызов LLM с thinking «как в профиле»."""

    def __init__(self, name: str) -> None:
        self.spec = AgentSpec(name=name, stage=name, kind="structured", outputs=[f"{name}.txt"])

    def run(self, ctx: AgentContext) -> StageResult:
        ctx.llm.chat([ChatMessage(role="user", content=self.spec.name)])
        (ctx.output_dir / f"{self.spec.name}.txt").write_text("ok", "utf-8")
        return StageResult(
            agent=self.spec.name, status=StageStatus.passed, artifacts=[f"{self.spec.name}.txt"]
        )


def test_agent_thinking_from_yaml_budget_and_repair(tmp_path: Path) -> None:
    settings = load_agent_settings(PROJECT_ROOT / "config" / "profiles.yaml")
    on = {
        "profiler",
        "requirements_critic",
        "vlm_reviewer",
        "security_analyst",
        "acceptance",
    }
    off = {
        "backend_coder",
        "android_coder",
        "test_engineer",
        "devops",
    }
    assert {n for n, v in settings.agents.items() if v} == on
    assert {n for n, v in settings.agents.items() if not v} == off
    assert settings.repair_thinking is False

    # в графе: security_analyst — с thinking, backend_coder — без (профиль CODE одинаковый)
    llm = FakeLLM(
        [
            FakeReply(content="a", expect=Expect(contains="security_analyst", thinking=True)),
            FakeReply(content="b", expect=Expect(contains="backend_coder", thinking=False)),
        ]
    )
    llm.agent_settings = settings
    agents = [ChatAgent("security_analyst"), ChatAgent("backend_coder")]
    Harness(tmp_path).run(agents, llm=llm, use_cache=False)
    llm.assert_exhausted()

    # бюджет сильнее yaml, ремонт не наследует thinking агента
    probe = FakeLLM(["1", "2", "3"])
    probe.agent_settings = settings
    probe.for_agent("security_analyst").without_thinking().chat([])
    probe.without_thinking().for_agent("security_analyst").chat([])
    probe.for_agent("security_analyst").for_repair().chat([])
    assert [c.thinking for c in probe.calls] == [False, False, False]
    assert FakeLLM(["x"]).for_agent("analyst").forced_thinking is None  # без yaml — как в spec
