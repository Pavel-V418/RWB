"""Этап 11 «эффективность»: бенчмарк параллельности, дедупликация, селекторы входов, «до/после»."""

import importlib.util
import json
import shutil
import threading
import time
from pathlib import Path
from types import ModuleType

import pytest
import typer
import yaml
from typer.testing import CliRunner

from specforge.core.agent_api import StructuredAgent
from specforge.core.contracts import AgentSpec, StageStatus
from specforge.core.events import Event
from specforge.core.selectors import (
    SelectorError,
    jsonpath_select,
    markdown_section,
    split_pattern,
)
from specforge.llm import ChatMessage, FakeLLM
from specforge.telemetry.compare import compare, save_baseline
from specforge.telemetry.metrics import RunMetrics
from specforge.telemetry.report import build_report, register_cli
from tests.conftest import MakeCtx

ROOT = Path(__file__).parent.parent
FIXTURE_RUN = Path(__file__).parent / "fixtures" / "runs" / "fake-run"
NL = chr(10)


def load_bench() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bench", ROOT / "infra" / "bench_concurrency.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- 3. бенчмарк параллельности ------------------------------------------------------------------


def test_bench_concurrency_recommends_level_before_p95_growth(tmp_path: Path) -> None:
    bench = load_bench()
    server = threading.BoundedSemaphore(4)  # «эндпоинт» держит 4 запроса одновременно

    def call() -> int:
        with server:
            time.sleep(0.06)
        return 10

    results = bench.bench(call, levels=(1, 2, 4, 8), requests_per_thread=2)
    assert [r.threads for r in results] == [1, 2, 4, 8]
    assert results[2].throughput_rps > results[0].throughput_rps * 2.5
    assert results[3].p95_s > results[0].p95_s * 1.5  # на 8 потоках очередь
    assert bench.recommend(results) == 4

    report = bench.render(results, 4, "http://fake", "fake")
    assert "**Рекомендуемый `LLM_MAX_CONCURRENCY=4`**" in report
    assert "| 4 ✅ |" in report
    assert bench.percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)


# --- 4. дедупликация ---------------------------------------------------------------------------


def test_dedup_only_at_zero_temperature_within_run() -> None:
    llm = FakeLLM(["первый", "второй", "третий", "четвёртый"])
    run = llm.bind(run_id="r1", stage="s", agent="a")
    messages = [ChatMessage(role="user", content="одинаковый запрос")]

    first = run.chat(messages, temperature=0)
    again = run.chat(messages, temperature=0)  # из кэша — транспорт не вызывается
    assert again.content == first.content == "первый"
    assert again.usage.dedup_hits == 1
    assert again.usage.total_tokens == 0
    assert len(llm.calls) == 1

    assert run.chat(messages, temperature=0.2).content == "второй"  # temperature > 0 — без кэша
    other_run = llm.bind(run_id="r2", stage="s", agent="a")
    assert other_run.chat(messages, temperature=0).content == "третий"  # другой прогон
    assert run.chat(messages, temperature=0, thinking=True).content == "четвёртый"

    usage = llm.tracker.total(run_id="r1")
    assert (usage.calls, usage.dedup_hits) == (3, 1)
    assert run.usage.dedup_hits == 1


# --- 5. селекторы входов ----------------------------------------------------------------------

OPENAPI = {
    "openapi": "3.1.0",
    "info": {"title": "API", "version": "1"},
    "paths": {
        "/orders": {"get": {"summary": "список"}},
        "/orders/{id}": {"get": {"summary": "один"}},
        "/users": {"get": {"summary": "пользователи"}},
    },
    "components": {"schemas": {"Order": {"type": "object"}, "User": {"type": "object"}}},
}
SPEC_MD = NL.join(
    [
        "# ТЗ",
        "вступление",
        "## Требования к API",
        "общие правила",
        "### Заказы",
        "```",
        "# не заголовок",
        "```",
        "## Интерфейс",
        "экраны",
    ]
)


def test_selectors_jsonpath_markdown_and_agent_inputs(
    make_ctx: MakeCtx, events: list[Event]
) -> None:
    assert split_pattern("api/openapi.yaml#$.paths./orders") == (
        "api/openapi.yaml",
        "$.paths./orders",
    )
    assert split_pattern("api/*.yaml") == ("api/*.yaml", None)
    assert jsonpath_select(OPENAPI, "$.paths./orders") == {
        "paths": {"/orders": {"get": {"summary": "список"}}}
    }
    assert jsonpath_select(OPENAPI, "$.paths['/orders/{id}'].get.summary") == {
        "paths": {"/orders/{id}": {"get": {"summary": "один"}}}
    }
    assert set(jsonpath_select(OPENAPI, "$.components.schemas.*")["components"]["schemas"]) == {
        "Order",
        "User",
    }
    with pytest.raises(SelectorError):
        jsonpath_select(OPENAPI, "$.paths./nope")

    section = markdown_section(SPEC_MD, "требования к api")
    assert section.splitlines() == SPEC_MD.splitlines()[2:8]  # с подразделом и блоком кода
    with pytest.raises(SelectorError):
        markdown_section(SPEC_MD, "Нет такого")

    ctx = make_ctx(FakeLLM([]))
    (ctx.input_dir / "api").mkdir()
    (ctx.input_dir / "api" / "openapi.yaml").write_text(yaml.safe_dump(OPENAPI), "utf-8")
    (ctx.input_dir / "spec.md").write_text(SPEC_MD, "utf-8")
    (ctx.input_dir / "big.md").write_text("x" * 40_000, "utf-8")

    class Reader(StructuredAgent[RunMetrics]):
        spec = AgentSpec(
            name="reader",
            stage="r",
            kind="structured",
            inputs=[
                "api/openapi.yaml#$.paths./orders",
                "spec.md#Интерфейс",
                "big.md",
                "spec.md#Нет",
            ],
        )
        schema = RunMetrics

    files = {f.path: f.content for f in Reader().read_inputs(ctx)}
    assert set(files) == {
        "api/openapi.yaml#$.paths./orders",
        "big.md",
        "spec.md#Интерфейс",
        "spec.md#Нет",
    }
    assert yaml.safe_load(files["api/openapi.yaml#$.paths./orders"]) == {
        "paths": {"/orders": {"get": {"summary": "список"}}}
    }
    assert "/users" not in files["api/openapi.yaml#$.paths./orders"]
    assert files["spec.md#Интерфейс"].strip() == "## Интерфейс" + NL + "экраны"
    assert files["spec.md#Нет"].startswith("(селектор 'Нет' не применён")
    kinds = [e.kind for e in events]
    assert "input_too_large" in kinds  # 40 000 символов ≈ 10 000 токенов > 8000
    assert "input_selector_failed" in kinds


# --- 6. отчёт «до/после» ------------------------------------------------------------------------


def test_report_before_after_and_quality_regressions(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "fake-run"
    shutil.copytree(FIXTURE_RUN, run_dir)
    baseline = tmp_path / "bench" / "baseline"
    build_report(run_dir, baseline=None)
    save_baseline(run_dir / "report", baseline)
    assert (baseline / "report.json").is_file()

    # «после»: токенов меньше, но web_coder стал degraded с лишней попыткой
    summary = json.loads((run_dir / "pipeline.json").read_text("utf-8"))
    for stage in summary["stages"].values():
        stage["usage"]["prompt_tokens"] //= 2
    web = summary["stages"]["web_coder"]
    web["status"] = StageStatus.degraded.value
    web["attempts"] = 3
    web["gate"]["passed"] = False
    web["gate"]["total_errors"] = 1
    (run_dir / "pipeline.json").write_text(json.dumps(summary, ensure_ascii=False), "utf-8")

    files = build_report(run_dir, baseline=baseline)
    assert files.comparison is not None
    totals = files.comparison.totals
    assert totals["prompt_tokens"].after < totals["prompt_tokens"].before
    assert totals["prompt_tokens"].change_pct == pytest.approx(-50, abs=1)
    md = files.markdown.read_text("utf-8")
    assert "## До / после (baseline `fake-run`)" in md
    assert "### ⚠ Стало хуже по качеству" in md
    assert "**web_coder: статус passed → degraded**" in md
    assert "**web_coder: шлюз стал красным**" in md
    assert "**web_coder: попыток 2 → 3**" in md
    assert "| web_coder ⚠ |" in md
    assert "backend_coder ⚠" not in md
    assert (
        json.loads(files.json_path.read_text("utf-8"))["comparison"]["baseline_run_id"]
        == "fake-run"
    )

    same = compare(files.metrics, files.metrics)
    assert same.regressions == []

    app = typer.Typer()
    register_cli(app)
    app.command("noop")(lambda: None)
    result = CliRunner().invoke(
        app, ["report", "fake-run", "--runs-dir", str(run_dir.parent), "--baseline", str(baseline)]
    )
    assert result.exit_code == 0, result.output
    assert "Хуже baseline по качеству: web_coder" in result.output
