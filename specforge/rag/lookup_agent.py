"""ask_docs(question, tech) — короткая справка по документации для агентов этапов.

agent_loop только с search_docs, не больше LOOKUP_MAX_STEPS шагов; ответ — аргумент finish:
не больше ANSWER_TOKENS токенов, с примером кода и источником. Песочница — временный каталог:
поиску файлы не нужны.
"""

import tempfile
from pathlib import Path

from specforge.core.agent_api import AgentContext, EmitFn, agent_loop
from specforge.core.contracts import ProfileName
from specforge.core.events import Event
from specforge.llm import LLMClient

LOOKUP_MAX_STEPS = 3
ANSWER_TOKENS = 300
CHARS_PER_TOKEN = 4
LOOKUP_BUDGET_TOKENS = 20_000

SYSTEM = f"""Ты — справочник по документации. Отвечаешь на вопрос разработчика только по
фрагментам, которые вернул инструмент search_docs, и не выдумываешь API.

Порядок:
1. Вызови search_docs с ключевыми словами вопроса (и tech, если он указан).
2. Если нужного нет — один раз переформулируй запрос.
3. Вызови finish(summary=ответ).

Ответ — не больше {ANSWER_TOKENS} токенов: суть в 1–3 предложениях, короткий пример кода
в блоке ``` и строка «Источник: <источник из результатов поиска>». Если в документации ответа
нет — так и напиши в finish, без примера.
"""


def ask_docs(
    question: str,
    tech: str | None,
    *,
    llm: LLMClient,
    run_id: str = "docs-lookup",
    emit: EmitFn | None = None,
) -> str:
    task = f"Технология: {tech or 'любая'}.\nВопрос: {question.strip()}"
    with tempfile.TemporaryDirectory(prefix="specforge-docs-") as tmp:
        root = Path(tmp)
        ctx = AgentContext(
            run_id=run_id,
            input_dir=root,
            output_dir=root,
            workdir=root,
            llm=llm,
            budget_tokens=LOOKUP_BUDGET_TOKENS,
            emit=emit if emit is not None else _discard,
        )
        outcome = agent_loop(
            ctx,
            SYSTEM,
            task,
            ["search_docs"],
            max_steps=LOOKUP_MAX_STEPS,
            profile=ProfileName.REVIEW,
            thinking=False,
        )
    answer = outcome.summary.strip()
    if outcome.stopped_reason != "finish":
        answer = f"Ответ не найден ({outcome.stopped_reason}). {answer}".strip()
    return truncate_tokens(answer, ANSWER_TOKENS)


def _discard(event: Event) -> None:
    del event


def truncate_tokens(text: str, tokens: int) -> str:
    limit = tokens * CHARS_PER_TOKEN
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if cut.count("```") % 2:  # не оставлять незакрытый блок кода
        cut = cut[: cut.rfind("```")].rstrip()
    return cut + "…"
