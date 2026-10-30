"""Агенты этапа 2: доменная модель, дизайн API и событий, текст SA-документа, ремонт покрытия."""

from __future__ import annotations

from ..llm import LLM, FakeLLM
from ..prompting import compact, messages
from ..schemas import Requirements
from ..schemas_system import ApiDesign, ApiPatch, DomainModel, SaProse

SYS = "system_architect"


def req_view(req: Requirements) -> dict:
    """Срез requirements.json для архитектора — одинаковый у всех вызовов этапа (prefix cache)."""
    answered = [{"id": q.id, "topic": q.topic, "decision": q.answer} for q in req.questions if q.answer]
    return {
        "product": req.meta.product_name,
        "summary": req.meta.summary,
        "profile": req.profile.model_dump(),
        "roles": [{"key": r.key, "name": r.name} for r in req.roles],
        "functional": [{"id": f.id, "statement": f.statement, "priority": f.priority,
                        "actors": [a.removeprefix("ROLE-") for a in f.actors],
                        "entities": [e.removeprefix("ENT-") for e in f.entities]} for f in req.functional],
        "non_functional": [n.statement + (f" ({n.metric})" if n.metric else "") for n in req.non_functional],
        "constraints": [c.statement for c in req.constraints],
        "entities": [{"name": e.name, "description": e.description,
                      "attributes": [f"{a.name}:{a.type}" + ("" if a.required else "?") for a in e.attributes]}
                     for e in req.entities],
        "integrations": [{"name": i.name, "mode": i.mode, "availability": i.availability} for i in req.integrations],
        "security": [s.statement for s in req.security],
        "scenarios": [{"id": s.id, "title": s.title, "actor": s.actor.removeprefix("ROLE-"),
                       "requirements": s.requirements, "main_flow": s.main_flow} for s in req.scenarios],
        "decisions": answered,
        "assumptions": [a.statement for a in req.assumptions],
    }


def design_domain(llm: LLM, req_json: str) -> DomainModel:
    return llm.structured("domain", messages("domain", system=SYS, req_json=req_json), DomainModel,
                          thinking=True, max_tokens=16000)


def design_api(llm: LLM, req_json: str, domain: DomainModel) -> ApiDesign:
    return llm.structured("api_design", messages("api_design", system=SYS, req_json=req_json,
                                                 domain_json=compact(domain.model_dump())),
                          ApiDesign, thinking=True, max_tokens=24000)


def write_prose(llm: LLM, req_json: str, domain: DomainModel, design: ApiDesign) -> SaProse | None:
    if isinstance(llm, FakeLLM) and not llm.has("sa_prose"):
        return None
    view = {"entities": [e.name for e in domain.entities],
            "components": [c.model_dump() for c in design.components],
            "operations": [f"{o.method.upper()} {o.path} — {o.summary}" for o in design.operations],
            "events": [e.name for e in design.events]}
    return llm.structured("sa_prose", messages("sa_prose", system=SYS, req_json=req_json,
                                               design_json=compact(view)),
                          SaProse, thinking=False, max_tokens=6000)


def repair_api(llm: LLM, req_json: str, domain: DomainModel, design: ApiDesign, fr_ids: list[str],
               attempt: int) -> ApiPatch | None:
    name = f"repair_api_{attempt}"
    if isinstance(llm, FakeLLM) and not llm.has(name):
        return None
    existing = ", ".join(f"{o.method.upper()} {o.path}" for o in design.operations)
    return llm.structured(name, messages("repair_api", system=SYS, req_json=req_json,
                                         domain_json=compact(domain.model_dump()), fr_ids=fr_ids,
                                         existing_ops=existing),
                          ApiPatch, thinking=False, max_tokens=8000)
