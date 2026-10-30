"""Уточняющие вопросы: автопилот (допущения), --ask (остановка и ответы), вместе с approve,
разбор questions.json."""

import json
from pathlib import Path
from typing import Any

from specforge.core.agent_api import AgentContext
from specforge.core.contracts import QUESTIONS_FILE
from specforge.orchestrator.clarify import CLARIFICATIONS_JSON, CLARIFICATIONS_MD, read_questions
from specforge.orchestrator.graph import (
    approve_run,
    build_graph,
    pending_approval,
    start_run,
)
from specforge.orchestrator.registry import Registry
from specforge.telemetry.metrics import compute_metrics, load_summary
from specforge.telemetry.report import render_markdown
from tests.test_orchestrator import FakeAgent, Harness, spec

QUESTIONS = {
    "questions": [
        {
            "id": "Q1",
            "question": "Нужна ли авторизация водителей через корпоративный SSO?",
            "why": "меняет схему аутентификации",
            "impact": "security",
            "assumption": "локальные учётные записи",
        },
        {"id": "Q2", "question": "Сколько хранить историю?", "impact": "data"},
    ]
}


def agents(seen: dict[str, str]) -> list[FakeAgent]:
    def read_clarifications(ctx: AgentContext) -> None:
        path = ctx.input_dir / "input" / CLARIFICATIONS_MD
        seen["b"] = path.read_text(encoding="utf-8") if path.is_file() else ""

    return [
        FakeAgent(spec("a"), {"a.txt": "a", QUESTIONS_FILE: json.dumps(QUESTIONS)}),
        FakeAgent(spec("b", depends_on=["a"]), hook=read_clarifications),
    ]


def start(h: Harness, items: list[FakeAgent], **kwargs: Any) -> tuple[Any, Registry, Any]:
    registry = Registry(items)
    config = h.config()
    graph = build_graph(registry, config)
    start_run(graph, registry, config, run_id="r1", requirements=h.requirements, **kwargs)
    return graph, registry, config


def clarifications(h: Harness) -> list[dict[str, Any]]:
    path = h.runs_dir / "r1" / "artifacts" / "input" / CLARIFICATIONS_JSON
    data: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return data


def test_autopilot_takes_assumptions_without_stopping(tmp_path: Path) -> None:
    h, seen = Harness(tmp_path), {}
    graph, registry, config = start(h, agents(seen))

    assert pending_approval(graph, registry, config, "r1") is None
    records = clarifications(h)
    assert [(r["id"], r["source"]) for r in records] == [("Q1", "assumption"), ("Q2", "assumption")]
    assert records[0]["answer"] == "локальные учётные записи"
    assert "решает агент" in records[1]["answer"]  # допущения нет — явно сказано
    assert "## Допущения (ответа не было)" in seen["b"]  # следующий этап видит уточнения
    event = h.kinds("clarifications")[0]
    assert (event.agent, event.data["asked"], event.data["answered"]) == ("a", 2, 0)
    summary = load_summary(h.runs_dir / "r1")
    md = render_markdown(summary, compute_metrics(summary, events=h.events))
    assert "вопросов агентов 2 (ответов 0, допущений 2), правок артефактов 0" in md


def test_ask_mode_stops_and_records_answers(tmp_path: Path) -> None:
    h, seen = Harness(tmp_path), {}
    graph, registry, config = start(h, agents(seen), clarify=True)

    payload = pending_approval(graph, registry, config, "r1")
    assert payload is not None
    assert payload["kind"] == "questions"
    assert [q["id"] for q in payload["questions"]] == ["Q1", "Q2"]
    assert payload["questions"][0]["agent"] == "a"
    assert "b" not in seen  # следующий этап ждёт ответов
    assert h.kinds("approval_required")[0].message == "ждёт ответов на вопросы (2)"

    approve_run(graph, registry, config, run_id="r1", answers={"Q1": "да, через SSO"})
    records = {r["id"]: r for r in clarifications(h)}
    assert (records["Q1"]["source"], records["Q1"]["answer"]) == ("user", "да, через SSO")
    assert records["Q2"]["source"] == "assumption"
    assert "## Ответы заказчика" in seen["b"]
    assert "да, через SSO" in seen["b"]
    assert h.kinds("clarifications")[0].data["answered"] == 1


def test_questions_and_approval_in_one_stop(tmp_path: Path) -> None:
    h, seen = Harness(tmp_path), {}
    graph, registry, config = start(h, agents(seen), clarify=True, approve_after="a")

    payload = pending_approval(graph, registry, config, "r1")
    assert payload is not None
    assert payload["kind"] == "approval"
    assert len(payload["questions"]) == 2
    approve_run(
        graph,
        registry,
        config,
        run_id="r1",
        edits={"a/a.txt": "исправлено"},
        answers={"Q2": "год"},
    )
    assert pending_approval(graph, registry, config, "r1") is None
    assert (h.runs_dir / "r1" / "artifacts" / "a" / "a.txt").read_text("utf-8") == "исправлено"
    assert {r["id"]: r["source"] for r in clarifications(h)} == {"Q1": "assumption", "Q2": "user"}


def test_read_questions_is_lenient_and_limited(tmp_path: Path) -> None:
    items = [
        {"id": "Q1", "question": "первый", "extra": "лишнее поле не мешает"},
        {"id": "Q1", "question": "дубль"},
        {"id": "Q2"},  # без текста вопроса
        "мусор",
        *({"id": f"Q{i}", "question": f"вопрос {i}"} for i in range(3, 12)),
    ]
    (tmp_path / QUESTIONS_FILE).write_text(json.dumps(items), encoding="utf-8")
    found = read_questions(tmp_path)
    assert [q.id for q in found] == ["Q1", "Q3", "Q4", "Q5", "Q6"]  # не больше 5 по умолчанию
    assert found[0].question == "первый"
    (tmp_path / QUESTIONS_FILE).write_text("{не json", encoding="utf-8")
    assert read_questions(tmp_path) == []
