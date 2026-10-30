"""Агенты этапа 1. Каждый — один вызов LLM со структурным выводом.

profiler входит в extract: профиль кейса возвращается тем же вызовом, отдельный запрос не нужен
(экономия одного прохода по исходному тексту). Отдельно профиль сохраняется в profile.json.
"""

from __future__ import annotations

import re

from ..assemble import model_for_analysis
from ..llm import LLM, FakeLLM
from ..prompting import compact, messages
from ..schemas import AcceptancePatch, Analysis, Critique, Extraction, Requirements

# лимиты вывода с запасом; thinking включаем только там, где нужен разбор
EXTRACT_MAX_TOKENS = 16000
ANALYZE_MAX_TOKENS = 24000
CRITIC_MAX_TOKENS = 6000
REPAIR_MAX_TOKENS = 8000


def extract(llm: LLM, source: str) -> Extraction:
    return llm.structured("extract", messages("extract", source=source), Extraction,
                          thinking=True, max_tokens=EXTRACT_MAX_TOKENS)


def analyze(llm: LLM, source: str, req: Requirements) -> Analysis:
    return llm.structured("analyze", messages("analyze", source=source,
                                              model_json=compact(model_for_analysis(req))),
                          Analysis, thinking=True, max_tokens=ANALYZE_MAX_TOKENS)


def critic_view(req: Requirements) -> dict:
    return {
        "functional": [{"id": f.id, "title": f.title, "statement": f.statement, "priority": f.priority,
                        "origin": f.origin} for f in req.functional],
        "acceptance": [{"id": a.id, "kind": a.kind, "given": a.given, "when": a.when, "then": a.then}
                       for a in req.acceptance],
        "scenarios": [{"id": s.id, "title": s.title, "requirements": s.requirements,
                       "main_flow": s.main_flow, "alternative_flows": s.alternative_flows,
                       "error_flows": s.error_flows} for s in req.scenarios],
        "assumptions": [a.statement for a in req.assumptions],
        "questions": [q.question for q in req.questions],
    }


def critique(llm: LLM, source: str, req: Requirements) -> Critique | None:
    if isinstance(llm, FakeLLM) and not llm.has("critic"):
        return None
    return llm.structured("critic", messages("critic", source=source, model_json=compact(critic_view(req))),
                          Critique, thinking=True, max_tokens=CRITIC_MAX_TOKENS)


def repair_acceptance(llm: LLM, source: str, req: Requirements, fr_ids: list[str],
                      problems: list[str], attempt: int) -> AcceptancePatch | None:
    name = f"repair_acceptance_{attempt}"
    if isinstance(llm, FakeLLM) and not llm.has(name):
        return None
    items = []
    for f in req.functional:
        if f.id in fr_ids:
            items.append({"id": f.id, "title": f.title, "statement": f.statement,
                          "acceptance": [{"kind": a.kind, "title": a.title, "given": a.given,
                                          "when": a.when, "then": a.then}
                                         for a in req.acceptance if a.fr_id == f.id]})
    return llm.structured(name, messages("repair_acceptance", source=source, items_json=compact(items),
                                         problems=problems),
                          AcceptancePatch, thinking=False, max_tokens=REPAIR_MAX_TOKENS)


_FR_IN_TARGET = re.compile(r"\b(FR-\d+)\b")


def fr_of(target: str | None) -> str | None:
    if not target:
        return None
    m = _FR_IN_TARGET.search(target)
    return m.group(1) if m else None
