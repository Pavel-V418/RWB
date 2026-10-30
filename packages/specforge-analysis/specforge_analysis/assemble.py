"""Сборка requirements.json из ответов LLM: стабильные ID, связи, ссылки на строки источника,
политика уточняющих вопросов. Здесь нет вызовов LLM."""

from __future__ import annotations

import re

from .normalize import SourceIndex
from .schemas import (
    Acceptance, Analysis, Assumption, Constraint, Entity, Extraction, Functional, Goal,
    GlossaryTerm, Integration, Meta, NonFunctional, Question, Requirements, Risk, Role, Scenario,
    Security, Source, Story, Attribute, AAcceptance, BusinessReq,
)

MAX_QUESTIONS = 5
_SEV = {"high": 0, "medium": 1, "low": 2}
_IMPACT = {"security": 0, "architecture": 1, "core_scenario": 2, "data": 3, "ux": 4, "other": 5}


def _src(index: SourceIndex, quote: str | None) -> Source | None:
    if not quote:
        return None
    m = index.locate(quote)
    if not m:
        return Source(quote=quote, line=None, exact=None)
    return Source(quote=quote, line=m.line, exact=m.exact)


def _order_key(item, i: int) -> tuple:
    """Требования заказчика — в порядке появления в тексте, остальные — после, в порядке ответа."""
    line = item.source.line if (item.source and item.source.line) else None
    if item.origin == "customer" and line:
        return (0, line, i)
    return (1, 0, i)


def _renumber(items: list, prefix: str, width: int) -> None:
    ordered = sorted(enumerate(items), key=lambda p: _order_key(p[1], p[0]))
    items[:] = [it for _, it in ordered]
    for n, it in enumerate(items, 1):
        it.id = f"{prefix}-{n}" if width == 0 else f"{prefix}-{n:0{width}d}"


def role_id(key: str) -> str:
    return f"ROLE-{key}"


def entity_id(name: str) -> str:
    return f"ENT-{name}"


# =========================================================================== базовая модель (после extract)


def build_base(ex: Extraction, index: SourceIndex, source_file: str, mode: str) -> Requirements:
    goals = [Goal(id="", origin="customer" if g.source_quote else "derived", statement=g.statement,
                  metric=g.metric, source=_src(index, g.source_quote)) for g in ex.goals]
    _renumber(goals, "GOAL", 2)

    seen_roles: set[str] = set()
    roles: list[Role] = []
    for r in ex.roles:
        if r.key in seen_roles:
            continue
        seen_roles.add(r.key)
        roles.append(Role(id=role_id(r.key), key=r.key, name=r.name, description=r.description,
                          origin=r.origin, source=_src(index, r.source_quote)))

    seen_ent: set[str] = set()
    entities: list[Entity] = []
    for e in ex.entities:
        if e.name in seen_ent:
            continue
        seen_ent.add(e.name)
        entities.append(Entity(id=entity_id(e.name), name=e.name, description=e.description,
                               attributes=[Attribute(**a.model_dump()) for a in e.attributes],
                               origin=e.origin, source=_src(index, e.source_quote)))

    fr = [Functional(id="", title=f.title, statement=f.statement, priority=f.priority,
                     actors=[role_id(a) for a in f.actors], entities=[entity_id(e) for e in f.entities],
                     origin=f.origin, source=_src(index, f.source_quote)) for f in ex.functional]
    _renumber(fr, "FR", 0)       # регламент: FR-1, FR-2

    nfr = [NonFunctional(id="", category=n.category, statement=n.statement, metric=n.metric,
                         origin=n.origin, source=_src(index, n.source_quote)) for n in ex.non_functional]
    _renumber(nfr, "NFR", 0)

    con = [Constraint(id="", kind=c.kind, statement=c.statement, origin=c.origin,
                      source=_src(index, c.source_quote)) for c in ex.constraints]
    _renumber(con, "CON", 2)

    integ = [Integration(id="", name=i.name, purpose=i.purpose, direction=i.direction, mode=i.mode,
                         availability=i.availability, origin=i.origin, source=_src(index, i.source_quote))
             for i in ex.integrations]
    _renumber(integ, "INT", 2)

    return Requirements(
        meta=Meta(product_name=ex.product_name, summary=ex.summary, source_file=source_file, mode=mode),
        profile=ex.profile, goals=goals, roles=roles, functional=fr, non_functional=nfr,
        constraints=con, entities=entities, integrations=integ,
        glossary=[GlossaryTerm(**g.model_dump()) for g in ex.glossary],
    )


def model_for_analysis(req: Requirements) -> dict:
    """Срез модели, который нужен агенту анализа (без источников — их он видит в тексте)."""
    return {
        "roles": [{"key": r.key, "name": r.name} for r in req.roles],
        "functional": [{"id": f.id, "title": f.title, "statement": f.statement, "priority": f.priority,
                        "actors": [a.removeprefix("ROLE-") for a in f.actors]} for f in req.functional],
        "entities": [{"name": e.name, "attributes": [a.name for a in e.attributes]} for e in req.entities],
        "integrations": [{"id": i.id, "name": i.name, "availability": i.availability} for i in req.integrations],
        "constraints": [c.statement for c in req.constraints],
        "non_functional": [n.statement for n in req.non_functional],
        "profile": req.profile.model_dump(),
    }


# =========================================================================== анализ (после analyze)


def _ac_items(fr_id: str, acs: list[AAcceptance]) -> list[Acceptance]:
    return [Acceptance(id=f"AC-{fr_id}-{n:02d}", origin="derived", fr_id=fr_id, kind=a.kind,
                       title=a.title, given=a.given, when=a.when, then=a.then)
            for n, a in enumerate(acs, 1)]


def set_acceptance(req: Requirements, raw: list[AAcceptance], only_fr: set[str] | None = None) -> None:
    """Пересобирает критерии: для only_fr заменяет, для остальных оставляет как есть."""
    known = {f.id for f in req.functional}
    by_fr: dict[str, list[AAcceptance]] = {}
    for a in raw:
        if a.fr_id in known:
            by_fr.setdefault(a.fr_id, []).append(a)
    kept = [a for a in req.acceptance if only_fr is not None and a.fr_id not in only_fr]
    new: list[Acceptance] = []
    for fr in req.functional:
        if only_fr is not None and fr.id not in only_fr:
            continue
        new += _ac_items(fr.id, by_fr.get(fr.id, []))
    order = {f.id: i for i, f in enumerate(req.functional)}
    req.acceptance = sorted(kept + new, key=lambda a: (order.get(a.fr_id, 999), a.id))
    for f in req.functional:
        f.acceptance = [a.id for a in req.acceptance if a.fr_id == f.id]


def merge_analysis(req: Requirements, an: Analysis, mode: str,
                   answers: dict[str, str] | None = None) -> None:
    roles = {r.key for r in req.roles}

    def rid(key: str) -> str:
        return role_id(key) if key in roles else key  # неизвестный ключ поймает шлюз

    req.scenarios = [Scenario(id=f"UC_{n:03d}", origin="derived", title=s.title, actor=rid(s.actor),
                              goal=s.goal, preconditions=s.preconditions, main_flow=s.main_flow,
                              alternative_flows=s.alternative_flows, error_flows=s.error_flows,
                              postconditions=s.postconditions, expected_result=s.expected_result,
                              requirements=s.requirements) for n, s in enumerate(an.scenarios, 1)]
    set_acceptance(req, an.acceptance)
    req.stories = [Story(id=f"US-{n:03d}", origin="derived", fr_id=s.fr_id, role=rid(s.as_a),
                         i_want=s.i_want, so_that=s.so_that) for n, s in enumerate(an.stories, 1)]

    start = len(req.non_functional)
    for n, d in enumerate(an.derived_nfr, start + 1):
        req.non_functional.append(NonFunctional(id=f"NFR-{n}", origin="derived", category=d.category,
                                                statement=d.statement, metric=d.metric, rationale=d.rationale))
    req.security = [Security(id=f"SEC-{n:02d}", origin="derived", category=s.category,
                             statement=s.statement, rationale=s.rationale, related=s.related)
                    for n, s in enumerate(an.security, 1)]
    req.risks = [Risk(id=f"RISK-{n:02d}", origin="derived", statement=r.statement,
                      probability=r.probability, impact=r.impact, mitigation=r.mitigation)
                 for n, r in enumerate(an.risks, 1)]

    # --- политика вопросов: не больше MAX_QUESTIONS, остальное — допущения
    ranked = sorted(enumerate(an.ambiguities),
                    key=lambda p: (_SEV[p[1].severity], _IMPACT[p[1].impact], p[0]))
    questions: list[Question] = []
    assumptions: list[Assumption] = []
    for _, amb in ranked:
        ask = (amb.impact in ("architecture", "security", "core_scenario", "data")
               and (amb.severity == "high" or (amb.severity == "medium" and not amb.default_is_safe)))
        if ask and len(questions) < MAX_QUESTIONS:
            questions.append(Question(
                id=f"Q-{len(questions) + 1:02d}", origin="derived", topic=amb.topic,
                question=amb.description, impact=amb.impact, severity=amb.severity,
                affected=amb.affected, options=amb.options, default_option=amb.default_option))
        else:
            assumptions.append(Assumption(id="", origin="assumption",
                                          statement=f"{amb.topic}: {amb.default_option}",
                                          reason=amb.description, affected=amb.affected))
    for a in an.assumptions:
        assumptions.append(Assumption(id="", origin="assumption", statement=a.statement,
                                      reason=a.reason, affected=a.affected))

    answers = answers or {}
    for q in questions:
        if q.id in answers and str(answers[q.id]).strip():
            q.answer, q.status, q.origin = str(answers[q.id]).strip(), "answered", "clarification"
        elif mode == "auto":
            q.answer, q.status = q.default_option, "defaulted"
            assumptions.append(Assumption(id="", origin="assumption", from_question=q.id,
                                          statement=f"{q.topic}: {q.default_option}",
                                          reason=f"Принято по умолчанию без ответа на {q.id}. {q.question}",
                                          affected=q.affected))
    for n, a in enumerate(assumptions, 1):
        a.id = f"ASM-{n:02d}"
    req.questions = questions
    req.assumptions = assumptions
    link_backrefs(req)


def link_backrefs(req: Requirements) -> None:
    for f in req.functional:
        f.scenarios = [s.id for s in req.scenarios if f.id in s.requirements]
        f.stories = [s.id for s in req.stories if s.fr_id == f.id]
        f.acceptance = [a.id for a in req.acceptance if a.fr_id == f.id]


_ID_RE = re.compile(r"^(BR|FR|NFR|AC|US|SEC|ASM|Q|RISK|CON|INT|GOAL|ROLE|ENT)-|^UC_")

_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


_WORD = re.compile(r"[a-zа-яё0-9]+", re.I)


def _stems(text: str) -> set[str]:
    return {w.lower()[:5] for w in _WORD.findall(text) if len(w) > 3}


def _best_fr(text: str, frs) -> tuple[str | None, float]:
    """FR, чья формулировка больше всего совпадает с пунктом по основам слов (доля слов пункта)."""
    a = _stems(text)
    if len(a) < 3:
        return None, 0.0
    best, score = None, 0.0
    for f in frs:
        b = _stems(f"{f.title} {f.statement}")
        sc = len(a & b) / len(a)
        if sc > score:
            best, score = f.id, sc
    return best, score


def build_business(req: Requirements, index: SourceIndex) -> list[int]:
    """Пункты бизнес-требований (BR-n) — строки исходного текста, на которые ссылаются элементы заказчика.
    Возвращает номера строк-пунктов списка, на которые не ссылается ничего (кандидаты в пропуски)."""
    by_line: dict[int, BusinessReq] = {}
    colls = (req.functional, req.non_functional, req.constraints, req.integrations, req.roles,
             req.entities, req.goals)
    for coll in colls:
        for it in coll:
            if it.origin != "customer" or not it.source or not it.source.line:
                continue
            ln = it.source.line
            if ln not in by_line:
                text = _BULLET.sub("", index.lines[ln - 1]).strip().rstrip(";.")
                by_line[ln] = BusinessReq(id="", line=ln, text=text, origin="customer",
                                          source=Source(quote=it.source.quote, line=ln, exact=True))
            br = by_line[ln]
            (br.fr if it.id.startswith("FR-") else br.related).append(it.id)
    # пункты списка, на которые модель не сослалась: привязываем к FR с наибольшим совпадением основ слов
    for i, line in enumerate(index.lines):
        ln = i + 1
        if ln in by_line or not _BULLET.match(line):
            continue
        text = _BULLET.sub("", line).strip().rstrip(";.")
        best, score = _best_fr(text, req.functional)
        if best is not None and score >= 0.5:
            by_line[ln] = BusinessReq(id="", line=ln, text=text, origin="customer",
                                      source=Source(quote=text, line=ln, exact=True), fr=[best])
    req.business = [by_line[k] for k in sorted(by_line)]
    for n, br in enumerate(req.business, 1):
        br.id = f"BR-{n}"
    covered = set(by_line)
    return [i + 1 for i, l in enumerate(index.lines) if _BULLET.match(l) and (i + 1) not in covered]


def looks_like_id(s: str) -> bool:
    return bool(_ID_RE.match(s))
