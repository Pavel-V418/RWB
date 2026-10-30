"""Телеметрия на фейковом прогоне fixtures/runs/fake-run: метрики, отчёт, графики, Langfuse."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from specforge.llm import FakeLLM
from specforge.llm.usage import UsageKey, UsageTracker
from specforge.telemetry.langfuse import LangfuseHook, install_langfuse, langfuse_hook_from_env
from specforge.telemetry.metrics import compute_metrics, load_calls, load_summary
from specforge.telemetry.report import build_report, register_cli

FIXTURE = Path(__file__).parent / "fixtures" / "runs" / "fake-run"


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    target = tmp_path / "runs" / "fake-run"
    shutil.copytree(FIXTURE, target)
    return target


def test_report_built_with_sections_and_cli(run_dir: Path) -> None:
    files = build_report(run_dir)
    md = files.markdown.read_text("utf-8")
    for section in (
        "## Сводка",
        "## Этапы",
        "## Итоговые метрики",
        "## Degraded-этапы и эскалации",
    ):
        assert section in md
    assert "| backend_coder | ⚠️ degraded | 3 | failed | 5 → 2 |" in md
    assert "| web_coder | ✅ passed | 2 | passed | 3 → 0 |" in md
    assert "assembler ⚙" in md
    assert "backend_coder: ремонты исчерпаны (2)" in md

    data = json.loads(files.json_path.read_text("utf-8"))["metrics"]
    assert data["repairs"] == 3
    assert data["retries"] == 3  # 1 повтор у analyst + 2 у backend_coder
    assert data["llm_free_share"] == pytest.approx(1 / 5)
    assert data["errors_before"] == 8
    assert data["errors_after"] == 2

    app = typer.Typer()
    register_cli(app)
    app.command("noop")(lambda: None)  # группа из двух команд: report вызывается по имени
    result = CliRunner().invoke(app, ["report", "fake-run", "--runs-dir", str(run_dir.parent)])
    assert result.exit_code == 0, result.output
    missing = CliRunner().invoke(app, ["report", "nope", "--runs-dir", str(run_dir.parent)])
    assert missing.exit_code == 2


def test_token_totals_match_usage_tracker(run_dir: Path) -> None:
    summary = load_summary(run_dir)
    tracker = UsageTracker()
    for call in load_calls(run_dir):
        tracker.record(UsageKey(call.run_id, call.stage, call.agent), call.usage)

    from_tracker = compute_metrics(summary, tracker=tracker)
    from_results = compute_metrics(summary)
    total = tracker.total(run_id="fake-run")
    for m in (from_tracker, from_results):
        assert (m.prompt_tokens, m.completion_tokens, m.cached_tokens, m.llm_calls) == (
            total.prompt_tokens,
            total.completion_tokens,
            total.cached_tokens,
            total.calls,
        )
        assert m.cache_hit_rate == pytest.approx(total.cached_tokens / total.prompt_tokens)
    for stage in from_tracker.stages:
        assert stage.total_tokens == tracker.total(agent=stage.agent).total_tokens


def test_both_charts_created(run_dir: Path) -> None:
    files = build_report(run_dir)
    for png in (files.tokens_png, files.gantt_png):
        assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        assert png.stat().st_size > 5_000
    stages = {s.agent: s for s in files.metrics.stages}
    # параллельные этапы перекрываются на диаграмме Ганта
    backend, web = stages["backend_coder"], stages["web_coder"]
    assert backend.start_s is not None
    assert web.start_s is not None
    assert web.start_s < backend.start_s + backend.wall_s
    assert files.metrics.wall_s == pytest.approx(268.0)
    assert files.metrics.stage_time_s > files.metrics.wall_s


def test_langfuse_noop_without_env_and_hook_with_client() -> None:
    assert langfuse_hook_from_env({}) is None
    assert langfuse_hook_from_env({"LANGFUSE_PUBLIC_KEY": "pk"}) is None
    llm = FakeLLM(["ok"])
    assert install_langfuse(llm, env={}) is None
    assert llm.bind(run_id="r", stage="s", agent="a").chat([]).content == "ok"

    class Observation:
        def end(self) -> None:
            pass

    class FakeLangfuse:
        def __init__(self) -> None:
            self.generations: list[dict[str, Any]] = []

        def create_trace_id(self, *, seed: str) -> str:
            return f"trace-{seed}"

        def start_observation(self, **kwargs: Any) -> Observation:
            self.generations.append(kwargs)
            return Observation()

    client = FakeLangfuse()
    llm = FakeLLM(["a", "b"])
    llm.add_hook(LangfuseHook(client))
    bound = llm.bind(run_id="r1", stage="api", agent="designer")
    bound.chat([])
    bound.chat([])
    assert [g["trace_context"]["trace_id"] for g in client.generations] == ["trace-r1"] * 2
    assert client.generations[0]["as_type"] == "generation"
    assert client.generations[0]["name"] == "api/designer"
