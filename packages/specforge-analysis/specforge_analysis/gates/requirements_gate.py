"""Шлюз этапа 1: детерминированные проверки requirements.json (без LLM, без токенов).

autofix() чинит то, что можно починить без модели, и честно пишет об этом предупреждение.
check()   возвращает оставшиеся проблемы; ошибки блокируют передачу артефактов дальше.
coverage() считает метрики полноты для отчёта.
"""

from __future__ import annotations

import re

from ..contract import Issue
from ..schemas import Requirements, Role

VAGUE = ["быстро", "удобно", "корректно", "своевременно", "понятно", "интуитивно", "оптимально",
         "эффективно", "при необходимости", "и т.д", "и т. д", "достаточно", "максимально",
         "адекватно", "надлежащим образом"]
_VAGUE_RE = re.compile("|".join(re.escape(w) for w in VAGUE), re.I)
_HAS_NUMBER = re.compile(r"\d")

# общие признаки требований, где нужен негативный или граничный критерий: ввод данных, время и сроки,
# права, внешние системы, геоданные, файлы. Доменных слов здесь быть не должно.
NEEDS_NEGATIVE_HINTS = ("ввод", "заполн", "оплат", "внешн", "время", "срок", "таймер", "уведом", "прав",
                        "роль", "доступ", "координат", "геолок", "местоположен", "интеграц", "загруз",
                        "импорт", "файл", "статус", "подтвер")


SYSTEM_ACTORS = {"system", "система", "role-system", "scheduler", "worker"}


def _resolve_roles(req: Requirements) -> list[Issue]:
    fixes: list[Issue] = []
    by_alias = {}
    for r in req.roles:
        for alias in (r.id, r.key, r.name, f"ROLE-{r.key}"):
            by_alias[alias.lower()] = r.id

    def resolve(name: str) -> str:
        low = name.strip().lower()
        if low in by_alias:
            return by_alias[low]
        if low.removeprefix("role-") in SYSTEM_ACTORS or low in SYSTEM_ACTORS:
            req.roles.append(Role(id="ROLE-system", key="system", name="Система", origin="derived",
                                  description="Автоматические процессы: таймеры, фоновые задачи, обработка событий"))
            for a in ("role-system", "system", "система"):
                by_alias[a] = "ROLE-system"
            fixes.append(Issue("W_ROLE_ADDED", "warning", "добавлена роль «Система» для автоматических сценариев",
                               "ROLE-system"))
            return "ROLE-system"
        return name

    for f in req.functional:
        f.actors = [resolve(a) for a in f.actors]
    for sc in req.scenarios:
        sc.actor = resolve(sc.actor)
    for st in req.stories:
        st.role = resolve(st.role)
    return fixes


def autofix(req: Requirements) -> list[Issue]:
    fixes: list[Issue] = []
    idx = req.index()

    # 1. цитата заказчика не найдена в тексте -> это уже не «требование заказчика»
    for coll in (req.functional, req.non_functional, req.constraints, req.roles, req.entities,
                 req.integrations, req.goals):
        for it in coll:
            if it.origin == "customer" and (not it.source or not it.source.line):
                quote = it.source.quote if it.source else None
                it.origin = "derived"
                fixes.append(Issue("W_SOURCE_DOWNGRADED", "warning",
                                   f"цитата не найдена в исходном тексте ({quote!r}); origin -> derived", it.id))

    # 2а. роли: ключ без префикса -> ROLE-ключ; «system» — автоматические процессы, роль добавляем сами
    fixes += _resolve_roles(req)
    idx = req.index()

    # 2. ссылки на несуществующие роли/сущности убираем
    for f in req.functional:
        bad = [a for a in f.actors if a not in idx]
        if bad:
            f.actors = [a for a in f.actors if a in idx]
            fixes.append(Issue("W_REF_DROPPED", "warning", f"удалены неизвестные роли {bad}", f.id))
        bad = [e for e in f.entities if e not in idx]
        if bad:
            f.entities = [e for e in f.entities if e in idx]
            fixes.append(Issue("W_REF_DROPPED", "warning", f"удалены неизвестные сущности {bad}", f.id))

    # 3. критерии/истории к несуществующим FR удаляем
    fr_ids = {f.id for f in req.functional}
    lost = [a.id for a in req.acceptance if a.fr_id not in fr_ids]
    if lost:
        req.acceptance = [a for a in req.acceptance if a.fr_id in fr_ids]
        fixes.append(Issue("W_REF_DROPPED", "warning", f"удалены критерии к неизвестным FR: {lost}"))
    lost = [s.id for s in req.stories if s.fr_id not in fr_ids]
    if lost:
        req.stories = [s for s in req.stories if s.fr_id in fr_ids]
        fixes.append(Issue("W_REF_DROPPED", "warning", f"удалены истории к неизвестным FR: {lost}"))
    for s in req.scenarios:
        bad = [r for r in s.requirements if r not in fr_ids]
        if bad:
            s.requirements = [r for r in s.requirements if r in fr_ids]
            fixes.append(Issue("W_REF_DROPPED", "warning", f"удалены ссылки на неизвестные FR {bad}", s.id))
    for s in req.security:
        s.related = [r for r in s.related if r in idx]
    for coll in (req.assumptions, req.questions):
        for a in coll:
            a.affected = [r for r in a.affected if r in idx]
    return fixes


def needs_negative(fr) -> bool:
    text = f"{fr.title} {fr.statement}".lower()
    return any(h in text for h in NEEDS_NEGATIVE_HINTS)


def check(req: Requirements) -> list[Issue]:
    issues: list[Issue] = []
    idx = req.index()

    def err(code, msg, target=None):
        issues.append(Issue(code, "error", msg, target))

    def warn(code, msg, target=None):
        issues.append(Issue(code, "warning", msg, target))

    # --- обязательное наполнение
    for name in ("goals", "roles", "functional"):
        if not getattr(req, name):
            err("E_EMPTY", f"раздел {name} пуст")

    # --- уникальность ID
    seen: set[str] = set()
    for coll in ("goals", "roles", "functional", "non_functional", "constraints", "entities",
                 "integrations", "security", "scenarios", "acceptance", "stories", "assumptions",
                 "questions", "risks"):
        for it in getattr(req, coll):
            if it.id in seen:
                err("E_DUP_ID", "повторяющийся идентификатор", it.id)
            seen.add(it.id)

    # --- ссылки, которые autofix не правит
    for s in req.scenarios:
        if s.actor not in idx:
            err("E_REF", f"неизвестная роль {s.actor!r}", s.id)
    for s in req.stories:
        if s.role not in idx:
            err("E_REF", f"неизвестная роль {s.role!r}", s.id)

    # --- каждое FR: критерии, сценарий, история, актор
    for f in req.functional:
        acs = [a for a in req.acceptance if a.fr_id == f.id]
        if not acs:
            err("E_NO_AC", "нет критериев приёмки", f.id)
        elif f.priority == "must" and needs_negative(f) and all(a.kind == "positive" for a in acs):
            warn("W_NO_NEGATIVE_AC", "нет negative/edge критерия для требования с вводом, временем, "
                                     "правами или внешними системами", f.id)
        if f.priority == "must" and not f.scenarios:
            warn("W_NO_SCENARIO", "требование не покрыто сценарием", f.id)
        if not f.stories:
            warn("W_NO_STORY", "нет пользовательской истории", f.id)
        if not f.actors:
            warn("W_NO_ACTOR", "не указана роль", f.id)

    # --- проверяемость критериев
    for a in req.acceptance:
        if not a.given or not a.when or not a.then:
            err("E_AC_SHAPE", "критерий без Given/When/Then", a.id)
        text = " ".join(a.then)
        m = _VAGUE_RE.search(text)
        if m and not _HAS_NUMBER.search(text):
            warn("W_VAGUE_AC", f"размытая формулировка «{m.group(0)}» без меры", a.id)

    # --- источники
    for coll in (req.functional, req.non_functional, req.constraints):
        for it in coll:
            if it.origin == "customer" and it.source and it.source.exact is False:
                warn("W_FUZZY_SOURCE", "цитата найдена неточно, проверьте ссылку", it.id)

    # --- роли и сущности используются
    used_roles = {a for f in req.functional for a in f.actors} | {s.actor for s in req.scenarios}
    for r in req.roles:
        if r.id not in used_roles:
            warn("W_ROLE_UNUSED", "роль не участвует ни в одном FR или сценарии", r.id)
    used_ent = {e for f in req.functional for e in f.entities}
    for e in req.entities:
        if e.id not in used_ent:
            warn("W_ENTITY_UNUSED", "сущность не упомянута ни в одном FR", e.id)

    # --- NFR с мерой
    for n in req.non_functional:
        if n.category in ("performance", "availability", "reliability", "scalability") and not n.metric:
            warn("W_NFR_NO_METRIC", "нет измеримой метрики", n.id)

    # --- вопросы
    if len(req.questions) > 5:
        err("E_Q_LIMIT", f"вопросов {len(req.questions)} при лимите 5")
    if req.meta.mode == "interactive":
        for q in req.questions:
            if q.status == "open":
                err("E_Q_OPEN", "нет ответа на вопрос (answers.yaml) в интерактивном режиме", q.id)
    return issues


def coverage(req: Requirements) -> dict:
    fr = req.functional
    n = len(fr) or 1
    must = [f for f in fr if f.priority == "must"]
    return {
        "functional": len(fr),
        "by_origin": {o: sum(1 for f in fr if f.origin == o) for o in ("customer", "derived", "assumption")},
        "fr_with_acceptance_pct": round(100 * sum(1 for f in fr if f.acceptance) / n, 1),
        "must_fr_with_scenario_pct": round(100 * sum(1 for f in must if f.scenarios) / (len(must) or 1), 1),
        "fr_with_story_pct": round(100 * sum(1 for f in fr if f.stories) / n, 1),
        "fr_with_source_line_pct": round(100 * sum(1 for f in fr if f.source and f.source.line) / n, 1),
        "acceptance_total": len(req.acceptance),
        "acceptance_negative_or_edge": sum(1 for a in req.acceptance if a.kind != "positive"),
        "scenarios": len(req.scenarios),
        "questions": len(req.questions),
        "assumptions": len(req.assumptions),
        "security": len(req.security),
        "risks": len(req.risks),
    }
