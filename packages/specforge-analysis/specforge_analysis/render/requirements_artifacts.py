"""Рендер артефактов этапа 1 из requirements.json. Чистый код, без LLM."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from ..schemas import Requirements

ORIGIN_RU = {"customer": "заказчик", "derived": "выведено", "assumption": "допущение",
             "clarification": "уточнение"}
PRIORITY_RU = {"must": "обязательно", "should": "желательно", "could": "возможно"}



def _br_fr(b) -> str:
    """FR пункта бизнес-требований; кандидаты, подобранные кодом, помечены явно."""
    items = list(b.fr) + [f"{c} (кандидат)" for c in b.fr_candidates]
    return ", ".join(items) or "—"

def _src(it) -> str:
    if it.source and it.source.line:
        return f"стр. {it.source.line}"
    return "—"


def _cell(s: str | None) -> str:
    return (s or "—").replace("|", "\\|").replace("\n", " ")


def _role_name(req: Requirements, rid: str) -> str:
    for r in req.roles:
        if r.id == rid:
            return r.name
    return rid


# --------------------------------------------------------------------------- requirements.md


def requirements_md(req: Requirements) -> str:
    o: list[str] = [f"# Модель требований: {req.meta.product_name}", "",
                    "> Сгенерировано автоматически из `requirements.json`. Не редактируйте вручную: "
                    "правки вносятся во вход или в `answers.yaml`, затем этап перезапускается.", "",
                    req.meta.summary, ""]
    p = req.profile
    flags = [("web-интерфейс", p.needs_web_ui), ("админка", p.needs_admin_ui),
             ("мобильное приложение", p.needs_mobile_app), ("геоданные", p.needs_geo),
             ("асинхронные события", p.needs_async_events), ("обновления в реальном времени", p.needs_realtime_updates),
             ("фоновые задачи и таймеры", p.needs_scheduled_jobs), ("файлы", p.needs_file_storage),
             ("уведомления пользователю", p.needs_user_notifications)]
    o += ["## Профиль решения", "",
          "Включено: " + ", ".join(n for n, v in flags if v) + ".",
          "Не требуется: " + (", ".join(n for n, v in flags if not v) or "—") + ".", ""]
    if p.notes:
        o += [f"- {n}" for n in p.notes] + [""]

    if req.business:
        o += ["## Бизнес-требования и трассировка", "",
              "Пункты исходных требований (BR) и функциональные требования, которые их реализуют.", "",
              "| BR | Строка | Бизнес-требование | FR | Прочее |", "| --- | --- | --- | --- | --- |"]
        o += [f"| {b.id} | {b.line} | {_cell(b.text)} | {_br_fr(b)} | {', '.join(b.related) or '—'} |"
              for b in req.business]
        if any(b.fr_candidates for b in req.business):
            o += ["", "«кандидат» — FR подобран кодом по совпадению слов, моделью не подтверждён; проверьте вручную."]
        o.append("")
    o += ["## Цели", "", "| ID | Цель | Метрика | Источник |", "| --- | --- | --- | --- |"]
    o += [f"| {g.id} | {_cell(g.statement)} | {_cell(g.metric)} | {_src(g)} |" for g in req.goals]
    o += ["", "## Роли", "", "| ID | Роль | Описание | Происхождение |", "| --- | --- | --- | --- |"]
    o += [f"| {r.id} | {r.name} | {_cell(r.description)} | {ORIGIN_RU[r.origin]} |" for r in req.roles]

    o += ["", "## Функциональные требования", "",
          "| ID | Требование | Приоритет | Роли | Происхождение | Источник | Критерии |",
          "| --- | --- | --- | --- | --- | --- | --- |"]
    for f in req.functional:
        roles = ", ".join(_role_name(req, a) for a in f.actors) or "—"
        o.append(f"| {f.id} | **{_cell(f.title)}.** {_cell(f.statement)} | {PRIORITY_RU[f.priority]} | "
                 f"{roles} | {ORIGIN_RU[f.origin]} | {_src(f)} | {', '.join(f.acceptance) or '—'} |")

    o += ["", "## Нефункциональные требования", "",
          "| ID | Категория | Требование | Метрика | Происхождение |", "| --- | --- | --- | --- | --- |"]
    o += [f"| {n.id} | {n.category} | {_cell(n.statement)} | {_cell(n.metric)} | {ORIGIN_RU[n.origin]} |"
          for n in req.non_functional]
    o += ["", "## Ограничения", "", "| ID | Тип | Ограничение | Источник |", "| --- | --- | --- | --- |"]
    o += [f"| {c.id} | {c.kind} | {_cell(c.statement)} | {_src(c)} |" for c in req.constraints]
    o += ["", "## Сущности", ""]
    for e in req.entities:
        attrs = ", ".join(f"`{a.name}`: {a.type}" + ("" if a.required else "?") for a in e.attributes)
        o.append(f"- **{e.id}** — {e.description}" + (f" Атрибуты: {attrs}." if attrs else ""))
    o += ["", "## Интеграции", "", "| ID | Система | Назначение | Способ | Доступность |",
          "| --- | --- | --- | --- | --- |"]
    o += [f"| {i.id} | {_cell(i.name)} | {_cell(i.purpose)} | {i.mode}, {i.direction} | {i.availability} |"
          for i in req.integrations]
    o += ["", "## Требования безопасности", "", "| ID | Категория | Требование | Связано |",
          "| --- | --- | --- | --- |"]
    o += [f"| {s.id} | {s.category} | {_cell(s.statement)} | {', '.join(s.related) or '—'} |"
          for s in req.security]

    o += ["", "## Пользовательские сценарии", ""]
    for s in req.scenarios:
        o += [f"### {s.id}. {s.title}", "",
              f"Актор: {_role_name(req, s.actor)}. Цель: {s.goal}. Требования: {', '.join(s.requirements) or '—'}.", ""]
        if s.preconditions:
            o += ["Предусловия:", ""] + [f"- {x}" for x in s.preconditions] + [""]
        o += ["Основной поток:", ""] + [f"{i}. {x.lstrip('0123456789. ')}" for i, x in enumerate(s.main_flow, 1)] + [""]
        if s.alternative_flows:
            o += ["Альтернативные потоки:", ""] + [f"- {x}" for x in s.alternative_flows] + [""]
        if s.error_flows:
            o += ["Исключения:", ""] + [f"- {x}" for x in s.error_flows] + [""]
        if s.postconditions:
            o += ["Постусловия:", ""] + [f"- {x}" for x in s.postconditions] + [""]
        if s.expected_result:
            o += [f"Ожидаемый результат: {s.expected_result}", ""]

    o += ["## Допущения", "", "Допущения приняты системой, а не заказчиком; каждое можно отменить ответом в `answers.yaml`.", "",
          "| ID | Допущение | Причина | Влияет на |", "| --- | --- | --- | --- |"]
    o += [f"| {a.id} | {_cell(a.statement)} | {_cell(a.reason)} | {', '.join(a.affected) or '—'} |"
          for a in req.assumptions]
    o += ["", "## Риски", "", "| ID | Риск | Вероятность | Влияние | Снижение |", "| --- | --- | --- | --- | --- |"]
    o += [f"| {r.id} | {_cell(r.statement)} | {r.probability} | {r.impact} | {_cell(r.mitigation)} |"
          for r in req.risks]
    if req.glossary:
        o += ["", "## Глоссарий", "", "| Термин | Определение |", "| --- | --- |"]
        o += [f"| {g.term} | {_cell(g.definition)} |" for g in req.glossary]
    return "\n".join(o) + "\n"


# --------------------------------------------------------------------------- user_stories.md


def stories_md(req: Requirements) -> str:
    fr = {f.id: f for f in req.functional}
    o = ["# Пользовательские истории", ""]
    for s in req.stories:
        f = fr.get(s.fr_id)
        o += [f"## {s.id} · {s.fr_id} {f.title if f else ''}".rstrip(), "",
              f"**Как** {_role_name(req, s.role).lower()}, **я хочу** {s.i_want}, **чтобы** {s.so_that}.", ""]
        if f and f.acceptance:
            o += ["Критерии приёмки: " + ", ".join(f.acceptance), ""]
    return "\n".join(o)


# --------------------------------------------------------------------------- acceptance/*.feature


def _steps(kw: str, items: list[str]) -> list[str]:
    out = []
    for i, s in enumerate(items):
        out.append(f"    {kw if i == 0 else 'And'} {s.strip()}")
    return out


def feature_files(req: Requirements) -> dict[str, str]:
    """Один .feature на FR: в Gherkin один Feature на файл. Ключевые слова английские —
    их понимают Reqnroll/SpecFlow, Playwright-bdd, pytest-bdd без настройки языка."""
    out: dict[str, str] = {}
    for f in req.functional:
        acs = [a for a in req.acceptance if a.fr_id == f.id]
        if not acs:
            continue
        lines = [f"# Сгенерировано specforge-analysis из requirements.json, {f.id}",
                 f"@{f.id} @{f.priority}", f"Feature: {f.id} {f.title}", f"  {f.statement}", ""]
        for a in acs:
            lines += [f"  @{a.id} @{a.kind}", f"  Scenario: {a.id} {a.title}"]
            lines += _steps("Given", a.given) + _steps("When", a.when) + _steps("Then", a.then) + [""]
        out[f"{f.id}.feature"] = "\n".join(lines)
    return out


# --------------------------------------------------------------------------- questions.md / answers template


def questions_md(req: Requirements) -> str:
    o = ["# Уточняющие вопросы", "",
         f"Вопросов: {len(req.questions)} (лимит 5). Остальные неоднозначности закрыты допущениями — см. `requirements.md`, раздел «Допущения».", ""]
    if req.meta.mode == "auto":
        o += ["Режим autopilot: на каждый вопрос принят вариант по умолчанию. Чтобы ответить иначе, "
              "впишите ответ в `answers.yaml` и перезапустите этап.", ""]
    for q in req.questions:
        status = {"open": "ожидает ответа", "answered": "есть ответ", "defaulted": "принято умолчание"}[q.status]
        o += [f"## {q.id}. {q.topic}", "", q.question, "",
              f"Влияние: {q.impact}, критичность: {q.severity}. Затрагивает: {', '.join(q.affected) or '—'}.", "",
              "Варианты:", ""]
        o += [f"- {opt}" + (" (по умолчанию)" if opt == q.default_option else "") for opt in q.options]
        o += ["", f"Статус: {status}." + (f" Ответ: {q.answer}" if q.answer else ""), ""]
    return "\n".join(o)


def answers_template(req: Requirements) -> str:
    o = ["# Ответы на уточняющие вопросы. Оставьте пустым, чтобы принять умолчание.", ""]
    for q in req.questions:
        o += [f"# {q.id}. {q.topic}", f"# по умолчанию: {q.default_option}",
              f"{q.id}: {json.dumps(q.answer if q.status == 'answered' else '', ensure_ascii=False)}", ""]
    return "\n".join(o)


# --------------------------------------------------------------------------- traceability.csv


TRACE_COLUMNS = ["business_req", "requirement", "title", "priority", "origin", "source_line", "scenarios", "stories",
                 "acceptance", "api", "events", "entities", "screens", "tests", "status"]


def traceability_csv(req: Requirements) -> str:
    """Первые столбцы заполняет этап 1. api/events/screens/tests — следующие агенты
    (архитектор, UX, тест-инженер) дописывают в тот же файл."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(TRACE_COLUMNS)
    br_of = {fr: b.id for b in req.business for fr in b.fr}
    for f in req.functional:
        w.writerow([br_of.get(f.id, ""), f.id, f.title, f.priority, f.origin, f.source.line if f.source and f.source.line else "",
                    " ".join(f.scenarios), " ".join(f.stories), " ".join(f.acceptance),
                    "", "", " ".join(f.entities), "", "", "analysed"])
    return buf.getvalue()


# --------------------------------------------------------------------------- отчёт этапа


def report_md(req: Requirements, gate: dict, coverage: dict, metrics: dict, critique: list[dict]) -> str:
    o = ["# Отчёт этапа 1: анализ требований", "",
         f"Шлюз: **{'пройден' if gate['passed'] else 'НЕ пройден'}**. "
         f"Ошибок: {len(gate['errors'])}, предупреждений: {len(gate['warnings'])}.", "",
         "## Покрытие", "", "| Метрика | Значение |", "| --- | --- |"]
    labels = {
        "functional": "Функциональных требований",
        "fr_with_acceptance_pct": "FR с критериями приёмки, %",
        "must_fr_with_scenario_pct": "Обязательных FR в сценариях, %",
        "fr_with_story_pct": "FR с пользовательской историей, %",
        "fr_with_source_line_pct": "FR со ссылкой на строку источника, %",
        "acceptance_total": "Критериев приёмки",
        "acceptance_negative_or_edge": "из них negative/edge",
        "scenarios": "Сценариев", "questions": "Уточняющих вопросов", "assumptions": "Допущений",
        "security": "Требований безопасности", "risks": "Рисков",
    }
    for k, label in labels.items():
        o.append(f"| {label} | {coverage[k]} |")
    bo = coverage["by_origin"]
    o.append(f"| FR по происхождению | заказчик {bo['customer']}, выведено {bo['derived']} |")
    o += ["", "## Расход LLM", "", "| Метрика | Значение |", "| --- | --- |"]
    for k, label in [("llm_calls", "Вызовов"), ("input_tokens", "Входных токенов"),
                     ("output_tokens", "Выходных токенов"), ("retries", "Повторов"),
                     ("cached_calls", "Из кэша / фикстур"), ("duration_s", "Время LLM, с")]:
        o.append(f"| {label} | {metrics.get(k, 0)} |")
    if gate["errors"] or gate["warnings"]:
        o += ["", "## Замечания шлюза", "", "| Уровень | Код | Элемент | Сообщение |", "| --- | --- | --- | --- |"]
        for i in gate["errors"] + gate["warnings"]:
            o.append(f"| {i['severity']} | {i['code']} | {i['target'] or '—'} | {_cell(i['message'])} |")
    if critique:
        o += ["", "## Замечания критика", "", "| Критичность | Тип | Элемент | Замечание | Статус |",
              "| --- | --- | --- | --- | --- |"]
        for c in critique:
            status = {"open": "открыто", "repair_attempted": "ремонт выполнялся, проверьте"}.get(
                c.get("status", "open"), c.get("status", "open"))
            o.append(f"| {c['severity']} | {c['category']} | {c['target']} | {_cell(c['issue'])} | {status} |")
    return "\n".join(o) + "\n"


def write_all(req: Requirements, out: Path) -> list[str]:
    files: dict[str, str] = {
        "requirements.json": req.model_dump_json(indent=2),
        "profile.json": req.profile.model_dump_json(indent=2),
        "requirements.md": requirements_md(req),
        "user_stories.md": stories_md(req),
        "questions.md": questions_md(req),
        "answers.template.yaml": answers_template(req),
        "traceability.csv": traceability_csv(req),
    }
    for name, content in feature_files(req).items():
        files[f"acceptance/{name}"] = content
    feat_dir = out / "acceptance"
    if feat_dir.exists():
        for old in feat_dir.glob("*.feature"):
            old.unlink()
    for name, content in files.items():
        p = out / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return sorted(files)
