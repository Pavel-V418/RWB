from collections.abc import Callable
from pathlib import Path

import pytest

from specforge.core.agent_api import AgentContext
from specforge.core.events import Event
from specforge.llm import LLMClient

MakeCtx = Callable[[LLMClient], AgentContext]


@pytest.fixture
def events() -> list[Event]:
    return []


@pytest.fixture
def make_ctx(tmp_path: Path, events: list[Event]) -> MakeCtx:
    def factory(llm: LLMClient) -> AgentContext:
        input_dir, output_dir, workdir = (tmp_path / d for d in ("in", "out", "run"))
        for d in (input_dir, output_dir, workdir):
            d.mkdir(exist_ok=True)
        return AgentContext(
            run_id="test-run",
            input_dir=input_dir,
            output_dir=output_dir,
            workdir=workdir,
            llm=llm,
            budget_tokens=10_000,
            emit=events.append,
        )

    return factory
