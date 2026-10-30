"""Уточняющие вопросы: агент пишет questions.json, платформа спрашивает пользователя (режим
--ask) или принимает допущения (автопилот) и кладёт ответы во вход следующих этапов.

artifacts/input/clarifications.json — список Clarification, clarifications.md — то же для
промптов (`inputs=["input/clarifications.md"]`). Ответ пользователя → source=user; нет ответа →
assumption из вопроса (source=assumption) — допущения явно отделены от требований заказчика.
"""

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import ValidationError

from specforge.core.artifacts import INPUT_STAGE, RunWorkspace
from specforge.core.contracts import QUESTIONS_FILE, Clarification, ClarificationQuestion

CLARIFICATIONS_JSON = "clarifications.json"
CLARIFICATIONS_MD = "clarifications.md"
DEFAULT_MAX_QUESTIONS = 5
NO_ASSUMPTION = "ответа нет, допущение не задано — решает агент, отметить в открытых вопросах"


def max_questions() -> int:
    return int(os.environ.get("SPECFORGE_MAX_QUESTIONS", DEFAULT_MAX_QUESTIONS))


def read_questions(stage_dir: Path, limit: int | None = None) -> list[ClarificationQuestion]:
    """Вопросы из questions.json этапа: невалидные пропускаются, id уникальны, не больше limit."""
    path = stage_dir / QUESTIONS_FILE
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    items = data.get("questions") if isinstance(data, dict) else data
    fields = set(ClarificationQuestion.model_fields)
    found: dict[str, ClarificationQuestion] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            question = ClarificationQuestion.model_validate(
                {k: v for k, v in item.items() if k in fields}
            )
        except ValidationError:
            continue
        found.setdefault(question.id, question)
    return list(found.values())[: max_questions() if limit is None else limit]


def record_answers(
    ws: RunWorkspace,
    agent: str,
    questions: Sequence[ClarificationQuestion],
    answers: Mapping[str, str],
) -> list[Clarification]:
    """Дописать ответы агента в artifacts/input/clarifications.{json,md} (повтор — перезапись
    ответов того же агента) и пересобрать манифест input."""
    records = [
        Clarification(
            **q.model_dump(),
            agent=agent,
            answer=(answers.get(q.id) or "").strip() or q.assumption or NO_ASSUMPTION,
            source="user" if (answers.get(q.id) or "").strip() else "assumption",
        )
        for q in questions
    ]
    existing = [c for c in load_clarifications(ws) if c.agent != agent]
    merged = [*existing, *records]
    ws.input_dir.mkdir(parents=True, exist_ok=True)
    (ws.input_dir / CLARIFICATIONS_JSON).write_text(
        json.dumps([c.model_dump() for c in merged], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (ws.input_dir / CLARIFICATIONS_MD).write_text(render_markdown(merged), encoding="utf-8")
    ws.write_manifest(INPUT_STAGE, "clarification")
    return records


def load_clarifications(ws: RunWorkspace) -> list[Clarification]:
    path = ws.input_dir / CLARIFICATIONS_JSON
    if not path.is_file():
        return []
    try:
        return [Clarification.model_validate(c) for c in json.loads(path.read_text("utf-8"))]
    except (ValueError, ValidationError):
        return []


def render_markdown(items: Sequence[Clarification]) -> str:
    lines = ["# Уточнения к требованиям", ""]
    answered = [c for c in items if c.source == "user"]
    assumed = [c for c in items if c.source == "assumption"]
    for title, group in (("Ответы заказчика", answered), ("Допущения (ответа не было)", assumed)):
        if not group:
            continue
        lines += [f"## {title}", ""]
        for c in group:
            lines += [f"### {c.id}. {c.question}", "", f"**Ответ:** {c.answer}"]
            if c.why:
                lines.append(f"*Зачем:* {c.why}")
            lines += [f"*Влияет на:* {c.impact}; *спросил:* {c.agent}", ""]
    return "\n".join(lines).rstrip() + "\n"
