"""Сайт документации MkDocs из артефактов конвейера и результатов команды. Без LLM:
тексты техписателя (DocsText) приходят готовыми, всё остальное собирается из данных."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from ..schemas import Requirements
from ..schemas_docs import DocsText
from ..schemas_system import SaProse
from ..team_inputs import PATHS, SEVERITIES, TeamInputs
from .contracts import Model, snake, table_name
from .diagrams import components_mermaid, er_mermaid, sequence_mermaid, state_mermaid
from .sa_document import _constraints

STATUS_RU = {"passed": "пройден", "failed": "упал", "skipped": "пропущен"}


def _c(s) -> str:
    return (str(s) if s not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")


def _mmd(code: str) -> list[str]:
    return ["```mermaid", code, "```"]


def pending(what: str, who: str, path: str) -> list[str]:
    return [f'!!! warning "Ожидается: {what}"',
            f"    Раздел заполнится автоматически, когда {who} положит результат в `artifacts/{path}`, "
            "и этап документации будет перезапущен.", ""]


def slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s.lower(), flags=re.U)
    return re.sub(r"[\s_]+", "-", s).strip("-")[:60] or "adr"


# =========================================================================== трассируемость


def trace_rows(req: Requirements, m: Model, ti: TeamInputs) -> list[dict]:
    rows = []
    for f in req.functional:
        api = [f"{o.method.upper()} {o.path}" for o in m.design.operations if f.id in o.fr]
        events = [e.name for e in m.design.events if f.id in e.fr]
        no_api = [n.reason for n in m.design.no_api if n.fr == f.id]
        screens = [s.title for s in ti.screens if f.id in s.fr]
        ac_ids = set(f.acceptance)
        tests = [t for t in ti.tests if f.id in t.tags or ac_ids & set(t.tags)]
        ac_tested = {tag for t in tests for tag in t.tags if tag in ac_ids}
        if not tests:
            status = "нет тестов"
        elif any(t.status == "failed" for t in tests):
            status = "есть падения"
        elif all(t.status == "skipped" for t in tests):
            status = "тесты пропущены"
        else:
            status = "подтверждено"
        rows.append({"fr": f, "api": api, "events": events, "no_api": no_api, "screens": screens, "tests": tests,
                     "ac_tested": ac_tested, "status": status})
    return rows


def _designed(r: dict) -> bool:
    return bool(r["api"] or r["events"] or r.get("no_api"))


def _designed_pct(rows: list[dict]) -> float:
    return round(100 * sum(1 for r in rows if _designed(r)) / len(rows), 1) if rows else 100.0


def trace_metrics(req: Requirements, rows: list[dict], ti: TeamInputs) -> dict:
    n = len(rows) or 1
    acs = len(req.acceptance) or 1
    return {
        "fr_total": len(rows),
        "fr_designed_pct": round(100 * sum(1 for r in rows if _designed(r)) / n, 1),
        "must_designed_pct": _designed_pct([r for r in rows if r["fr"].priority == "must"]),
        "fr_not_designed": [r["fr"].id for r in rows if not _designed(r)],
        "fr_with_screens_pct": round(100 * sum(1 for r in rows if r["screens"]) / n, 1) if ti.screens else None,
        "fr_with_tests_pct": round(100 * sum(1 for r in rows if r["tests"]) / n, 1) if ti.tests else None,
        "fr_confirmed_pct": round(100 * sum(1 for r in rows if r["status"] == "подтверждено") / n, 1) if ti.tests else None,
        "ac_with_tests_pct": round(100 * sum(len(r["ac_tested"]) for r in rows) / acs, 1) if ti.tests else None,
    }


# =========================================================================== страницы


def page_index(req: Requirements, d: DocsText | None, tm: dict) -> str:
    o = [f"# {req.meta.product_name}", ""]
    if d:
        p = d.product
        o += [f"**{p.tagline}**", "", "## Проблема", "", p.problem, "", "## Для кого", ""]
        o += [f"- {a}" for a in p.audience]
        o += ["", "## Ценность", ""] + [f"- {v}" for v in p.value]
        o += ["", "## Возможности", "", "| Возможность | Что делает | Требования |", "| --- | --- | --- |"]
        o += [f"| {c.title} | {_c(c.description)} | {', '.join(c.fr)} |" for c in p.capabilities]
    else:
        o += [req.meta.summary]
    o += ["", "## Цели", ""] + [f"- {g.statement}" for g in req.goals]
    o += ["", "## Состояние", "", "| Показатель | Значение |", "| --- | --- |",
          f"| Функциональных требований | {tm['fr_total']} |",
          f"| Спроектировано (API или события) | {tm['fr_designed_pct']}% |",
          f"| Подтверждено тестами | {_pct(tm['fr_confirmed_pct'])} |",
          "", "Документация собрана автоматически конвейером SpecForge из бизнес-требований и результатов этапов."]
    return "\n".join(o) + "\n"


def _pct(v) -> str:
    return "ожидается отчёт о тестировании" if v is None else f"{v}%"


def page_architecture(m: Model, prose: SaProse | None, adr_files: list[tuple[str, str]]) -> str:
    o = ["# Архитектура", "", "## Компоненты", ""] + _mmd(components_mermaid(m))
    o += ["", "| Компонент | Тип | Технология | Ответственность |", "| --- | --- | --- | --- |"]
    o += [f"| {c.name} | {c.kind} | {_c(c.tech)} | {_c(c.responsibility)} |" for c in m.design.components]
    if prose and prose.architecture_notes:
        o += ["", "## Ключевые решения", ""] + [f"- {x}" for x in prose.architecture_notes]
    if adr_files:
        o += ["", "Подробно — в журнале решений:", ""] + [f"- [{t}](adr/{f})" for f, t in adr_files]
    for seq in m.design.sequences:
        o += ["", f"## {seq.title}", ""] + _mmd(sequence_mermaid(seq))
    for sm in m.domain.state_machines:
        o += ["", f"## Жизненный цикл: {sm.entity}.{sm.field}", ""] + _mmd(state_mermaid(sm))
    return "\n".join(o) + "\n"


def adr_pages(d: DocsText | None) -> list[tuple[str, str, str]]:
    out = []
    if not d:
        return out
    for i, a in enumerate(d.adrs, 1):
        name = f"ADR-{i:03d}.md"  # ASCII-имена: без проблем с URL и файловыми системами
        statuses = " | ".join(f"**{s}**" if s == a.status else s for s in ("ПРЕДЛОЖЕНО", "ПРИНЯТО", "ЗАМЕНЕНО"))
        body = [f"# ADR-{i:03d}. {a.title}", "",
                "ADR (Architecture Decision Record) фиксирует выбор между альтернативами, обоснование и последствия.", "",
                "## Контекст и постановка проблемы", "", a.context, "", "## Статус", "", statuses, "", "## Альтернативы", ""]
        for n, opt in enumerate(a.alternatives, 1):
            body += [f"### Вариант {n}. {opt.name.split(':', 1)[-1].strip() if opt.name.lower().startswith('вариант') else opt.name}",
                     "", "Плюсы:", ""] + [f"- {x}" for x in opt.pros or ["—"]] + ["", "Минусы:", ""] + \
                    [f"- {x}" for x in opt.cons or ["—"]] + [""]
        body += ["## Принятое решение", "", a.decision, "", "## Последствия принятого решения", ""]
        body += [f"- {x}" for x in a.consequences] + [""]
        body += ["## Ответственные", ""] + ([f"- {x}" for x in a.responsible] or ["- —"]) + [""]
        if a.related:
            body += ["Связано: " + ", ".join(a.related), ""]
        out.append((name, f"ADR-{i:03d}. {a.title}", "\n".join(body)))
    return out


def page_api(m: Model, has_events: bool) -> str:
    o = ["# API", "", "Спецификации: [OpenAPI 3.1](contracts/openapi.yaml)"
         + (", [AsyncAPI 3.0](contracts/asyncapi.yaml)" if has_events else "") + ".", "",
         "- базовый путь `/api/v1`, аутентификация `Authorization: Bearer <JWT>`;",
         "- ошибки — `application/problem+json` (RFC 9457) с `traceId`;",
         "- критичные операции — заголовок `Idempotency-Key`; превышение лимита — 429 и `Retry-After`;",
         "- списки — `page`, `pageSize`, ответ `{items, page, pageSize, total}`.", ""]
    by_tag: dict[str, list] = {}
    for op in m.design.operations:
        by_tag.setdefault(op.tag, []).append(op)
    for tag, ops in by_tag.items():
        o += [f"## {tag}", "", "| Метод и путь | Назначение | Роли | Требования |", "| --- | --- | --- | --- |"]
        o += [f"| `{op.method.upper()} {op.path}` | {_c(op.summary)} | {', '.join(op.roles)} | {', '.join(op.fr)} |"
              for op in ops]
        o.append("")
    if has_events:
        o += ["## События", "", "| Событие | Топик | Производитель | Потребители |", "| --- | --- | --- | --- |"]
        o += [f"| `{e.name}` | `{e.name}.v{e.version}` | {e.producer} | {', '.join(e.consumers)} |" for e in m.design.events]
    return "\n".join(o) + "\n"


def page_data_model(m: Model) -> str:
    o = ["# Модель данных", "", "Физическая схема: [schema.sql](contracts/schema.sql).", ""] + _mmd(er_mermaid(m))
    for e in m.domain.entities:
        o += ["", f"## {e.name}", "", f"Таблица `{table_name(e.name)}`. {e.description}", "",
              "| Поле | Колонка | Тип | Обяз. | Ограничения |", "| --- | --- | --- | :---: | --- |"]
        for f in m.fields(e.name):
            o.append(f"| `{f.name}` | `{snake(f.name)}` | {f.type} | {'да' if f.required else ''} | "
                     f"{_c(_constraints(f))}{' (только БД)' if f.internal else ''} |")
    return "\n".join(o) + "\n"


def page_run(m: Model, ti: TeamInputs) -> str:
    o = ["# Запуск", ""]
    if ti.infra.run_readme:
        return "\n".join(o + [re.sub(r"^# .*\n", "", ti.infra.run_readme, count=1)]) + "\n"
    o += pending("инструкция по запуску", "участник 3 (DevOps)", f"{PATHS['infra_dir']}/README-run.md")
    o += ["## Общий порядок", "", "Требуется Docker 24+ и Docker Compose v2. Из корня репозитория:", "",
          "```bash", "cp .env.example .env          # секреты и пароли задаются здесь, в репозитории их нет",
          "docker compose up -d --build", "docker compose ps              # все сервисы в состоянии healthy", "```", ""]
    if ti.infra.services:
        o += ["## Сервисы", "", "| Сервис | Образ | Порты | Health check | Зависит от |", "| --- | --- | --- | :---: | --- |"]
        o += [f"| {s['name']} | {_c(s['image'])} | {_c(', '.join(s['ports']))} | {'✓' if s['healthcheck'] else ''} | "
              f"{_c(', '.join(s['depends_on']))} |" for s in ti.infra.services]
    else:
        o += ["## Компоненты", "", "| Компонент | Технология |", "| --- | --- |"]
        o += [f"| {c.name} | {_c(c.tech)} |" for c in m.design.components if c.kind != "external"]
    if ti.infra.env_example:
        keys = [l.split("=", 1)[0] for l in ti.infra.env_example.splitlines() if "=" in l and not l.startswith("#")]
        o += ["", "## Переменные окружения", "", ", ".join(f"`{k}`" for k in keys)]
    return "\n".join(o) + "\n"


def page_user_guide(req: Requirements, d: DocsText | None, ti: TeamInputs) -> str:
    o = ["# Руководство пользователя", ""]
    rname = {r.key: r.name for r in req.roles}
    if not ti.screens:
        o += pending("экраны и скриншоты интерфейса", "участник 4 (UX)", PATHS["screens"])
    if d:
        for g in d.user_guide:
            o += [f"## {rname.get(g.role, g.role)}", "", g.intro, ""]
            role_screens = [s for s in ti.screens if g.role in s.roles]
            for t in g.tasks:
                o += [f"### {t.title}", ""] + [f"{i}. {s}" for i, s in enumerate(t.steps, 1)]
                o += ["", f"Результат: {t.result}", ""]
                shots = [s for s in role_screens if s.screenshot and set(s.fr) & set(t.related)]
                for s in shots[:2]:
                    o += [f"![{s.title}](img/{Path(s.screenshot).name})", ""]
            if g.troubleshooting:
                o += ["### Если что-то пошло не так", "", "| Ситуация | Что делать |", "| --- | --- |"]
                o += [f"| {_c(x.problem)} | {_c(x.solution)} |" for x in g.troubleshooting]
                o.append("")
    else:
        for s in req.scenarios:
            o += [f"## {s.title}", ""] + [f"{i}. {x.lstrip('0123456789. ')}" for i, x in enumerate(s.main_flow, 1)] + [""]
    if ti.screens:
        o += ["## Экраны", "", "| Экран | Платформа | Роли | Требования |", "| --- | --- | --- | --- |"]
        o += [f"| {s.title} | {s.platform} | {', '.join(rname.get(r, r) for r in s.roles)} | {', '.join(s.fr)} |"
              for s in ti.screens]
    return "\n".join(o) + "\n"


def page_dev_guide(req: Requirements, m: Model) -> str:
    o = ["# Руководство по дальнейшей разработке", "",
         "Проект создан конвейером SpecForge. Источник истины — артефакты анализа и контракты; код, БД и документация "
         "выводятся из них. Менять поведение системы правильно в таком порядке: требования → контракты → код → тесты.", "",
         "## Где что лежит", "", "| Путь | Содержимое | Кто меняет |", "| --- | --- | --- |",
         "| `artifacts/01_requirements/` | модель требований, Gherkin-критерии, вопросы и допущения | аналитик |",
         "| `artifacts/02_system/` | доменная модель, OpenAPI, AsyncAPI, DDL, SA-документ | архитектор |",
         "| `artifacts/03_ux/` | спецификация экранов | дизайнер |",
         "| `artifacts/07_tests/` | отчёты тестов и проверок безопасности | QA |",
         "| `artifacts/08_infra/` | docker compose, переменные окружения, инструкция запуска | DevOps |",
         "| `artifacts/09_docs/` | этот сайт документации | генерируется |", "",
         "## Как добавить или изменить функцию", "",
         "1. Внесите изменение в бизнес-требования или ответьте на вопрос в `answers.yaml`.",
         "2. Перезапустите анализ: `specforge-analysis requirements --input <требования> --out artifacts/01_requirements`.",
         "3. Перезапустите проектирование: `specforge-analysis system --input artifacts/01_requirements --out artifacts/02_system`. "
         "Шлюз покажет, какие FR не покрыты API, и проверит DDL.",
         "4. Перегенерируйте DTO и клиенты из `openapi.yaml`, миграцию EF Core — по разнице `schema.sql`.",
         "5. Допишите доменную логику и тесты. В имени теста или в его тегах укажите ID требования или критерия "
         "(`FR-005`, `AC-FR-005-02`) — иначе тест не попадёт в матрицу трассируемости.",
         "6. Перезапустите документацию: `specforge-analysis docs --artifacts artifacts --out artifacts/09_docs`.", "",
         "## Соглашения", "",
         "- REST: `/api/v1/kebab-case`, `operationId` в camelCase, поля JSON в camelCase, ошибки — RFC 9457.",
         "- БД: таблицы и колонки в snake_case, имена таблиц во множественном числе, `id uuid`, `created_at`/`updated_at`, "
         "геометрия в SRID 4326.",
         "- События: `домен.объект.действие`, топик `<имя>.v<версия>`, несовместимые изменения — новая версия топика.",
         "- Секреты — только через переменные окружения; `.env` не коммитится.", ""]
    if m.req.profile.notes:
        o += ["## Профиль решения", ""] + [f"- {n}" for n in m.req.profile.notes]
    return "\n".join(o) + "\n"


def page_tests(req: Requirements, rows: list[dict], ti: TeamInputs, tm: dict) -> str:
    o = ["# Отчёт о тестировании", ""]
    if not ti.tests:
        return "\n".join(o + pending("результаты тестов", "участник 5 (QA)",
                                     f"{PATHS['tests_dir']}/report.json или JUnit XML")
                         + ["Пока доступны критерии приёмки: "
                            f"{len(req.acceptance)} шт. в `artifacts/01_requirements/acceptance/`."]) + "\n"
    cnt = Counter(t.status for t in ti.tests)
    failed = [t for t in ti.tests if t.status == "failed"]
    verdict = "пройдено" if not failed else f"есть падения: {len(failed)}"
    o += [f"Итог: **{verdict}**. Источники: " + ", ".join(f"`{s}`" for s in ti.test_sources) + ".", "",
          "| Показатель | Значение |", "| --- | --- |",
          f"| Тестов всего | {len(ti.tests)} |", f"| Пройдено | {cnt['passed']} |", f"| Упало | {cnt['failed']} |",
          f"| Пропущено | {cnt['skipped']} |", f"| FR с тестами | {tm['fr_with_tests_pct']}% |",
          f"| FR, подтверждённые тестами | {tm['fr_confirmed_pct']}% |",
          f"| Критериев приёмки с тестами | {tm['ac_with_tests_pct']}% |", ""]
    suites = Counter((t.suite or "—", t.status) for t in ti.tests)
    names = sorted({s for s, _ in suites})
    o += ["## По наборам", "", "| Набор | Пройдено | Упало | Пропущено |", "| --- | --- | --- | --- |"]
    o += [f"| {_c(s)} | {suites[(s, 'passed')]} | {suites[(s, 'failed')]} | {suites[(s, 'skipped')]} |" for s in names]
    if failed:
        o += ["", "## Упавшие тесты", "", "| Тест | Требования | Сообщение |", "| --- | --- | --- |"]
        o += [f"| {_c(t.name)} | {', '.join(t.tags) or '—'} | {_c(t.message[:200])} |" for t in failed]
    o += ["", "## Покрытие требований", "", "| FR | Требование | Тестов | Критериев покрыто | Статус |",
          "| --- | --- | --- | --- | --- |"]
    o += [f"| {r['fr'].id} | {_c(r['fr'].title)} | {len(r['tests'])} | {len(r['ac_tested'])}/{len(r['fr'].acceptance)} | "
          f"{r['status']} |" for r in rows]
    untagged = [t for t in ti.tests if not t.tags]
    if untagged:
        o += ["", f"Тестов без ссылки на требования: {len(untagged)}. Добавьте `FR-xxx` или `AC-…` в имя или теги."]
    return "\n".join(o) + "\n"


def page_security(ti: TeamInputs, req: Requirements | None = None) -> str:
    o = ["# Отчёт о проверке безопасности", ""]
    if not ti.findings and not ti.security_sources:
        return "\n".join(o + pending("результаты SAST, SCA, поиска секретов и DAST", "участник 5 (Security)",
                                     f"{PATHS['security_dir']}/security.json или *.sarif")) + "\n"
    open_ = [f for f in ti.findings if f.status == "open"]
    blocking = [f for f in open_ if f.severity in ("critical", "high")]
    verdict = "нет открытых критичных и высоких уязвимостей" if not blocking else \
        f"открыто критичных и высоких: {len(blocking)}"
    o += [f"Итог: **{verdict}**. Источники: " + ", ".join(f"`{s}`" for s in ti.security_sources) + ".", "",
          "| Критичность | Открыто | Исправлено | Ложные / принятые |", "| --- | --- | --- | --- |"]
    for sev in SEVERITIES:
        fs = [f for f in ti.findings if f.severity == sev]
        o.append(f"| {sev} | {sum(1 for f in fs if f.status == 'open')} | {sum(1 for f in fs if f.status == 'fixed')} | "
                 f"{sum(1 for f in fs if f.status in ('false_positive', 'accepted'))} |")
    tools = Counter(f.tool for f in ti.findings)
    if tools:
        o += ["", "Инструменты: " + ", ".join(f"{t} ({n})" for t, n in tools.items()) + "."]
    order = {s: i for i, s in enumerate(SEVERITIES)}
    if req and req.security:
        o += ["", "## По требованиям безопасности", "", "| Требование | Категория | Находок | Открыто |",
              "| --- | --- | --- | --- |"]
        for s in req.security:
            fs = [f for f in ti.findings if s.id in f.reqs]
            o.append(f"| {s.id}. {_c(s.statement)} | {s.category} | {len(fs)} | "
                     f"{sum(1 for f in fs if f.status == 'open')} |")
    shown = sorted(ti.findings, key=lambda f: (f.status != "open", order.get(f.severity, 9)))[:200]
    if shown:
        o += ["", "## Находки", "", "| Критичность | Статус | Инструмент | Правило / CVE | Описание | Где | Требование |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
        o += [f"| {f.severity} | {f.status} | {f.tool} | {_c(f.cve or f.rule)} | {_c(f.title)} | {_c(f.location)} | "
              f"{', '.join(f.reqs) or '—'} |" for f in shown]
    return "\n".join(o) + "\n"


def page_limitations(req: Requirements, m: Model, prose: SaProse | None, ti: TeamInputs,
                     critic: list[dict], gate_warnings: list[dict]) -> str:
    o = ["# Известные ограничения", "", "## Вне рамок прототипа", ""]
    o += [f"- {x}" for x in (prose.scope_out if prose else [c.statement for c in req.constraints if c.kind == "scope"])]
    mocks = [i for i in req.integrations if i.availability == "mock_required"]
    if mocks:
        o += ["", "## Внешние системы заменены mock-сервисами", ""] + [f"- {i.name} — {i.purpose}" for i in mocks]
    defaulted = [q for q in req.questions if q.status == "defaulted"]
    if defaulted:
        o += ["", "## Решения, принятые без ответа заказчика", "", "| Вопрос | Принято |", "| --- | --- |"]
        o += [f"| {q.id}. {_c(q.question)} | {_c(q.answer)} |" for q in defaulted]
    o += ["", "## Допущения системы", "", "| ID | Допущение |", "| --- | --- |"]
    o += [f"| {a.id} | {_c(a.statement)} |" for a in req.assumptions if not a.from_question]
    open_critic = [c for c in critic if c.get("status") in ("open", "repair_attempted")]
    if open_critic or gate_warnings:
        o += ["", "## Замечания проверок, оставшиеся открытыми", "", "| Источник | Элемент | Замечание |", "| --- | --- | --- |"]
        o += [f"| критик анализа{' (ремонт выполнялся)' if c.get('status') == 'repair_attempted' else ''} | "
              f"{c['target']} | {_c(c['issue'])} |" for c in open_critic]
        o += [f"| шлюз {w.get('stage', '')} | {_c(w.get('target'))} | {_c(w['message'])} |" for w in gate_warnings]
    failed = [t for t in ti.tests if t.status == "failed"]
    vulns = [f for f in ti.findings if f.status == "open" and f.severity in ("critical", "high", "medium")]
    if failed or vulns:
        o += ["", "## По результатам тестов и проверок безопасности", ""]
        o += [f"- Тест упал: {t.name} ({', '.join(t.tags) or 'без ссылки на FR'})" for t in failed[:30]]
        o += [f"- Уязвимость {f.severity}: {f.cve or f.rule} — {f.title}" for f in vulns[:30]]
    o += ["", "## Риски", "", "| ID | Риск | Снижение |", "| --- | --- | --- |"]
    o += [f"| {r.id} | {_c(r.statement)} | {_c(r.mitigation)} |" for r in req.risks]
    return "\n".join(o) + "\n"


def page_traceability(rows: list[dict], tm: dict, ti: TeamInputs) -> str:
    o = ["# Матрица трассируемости", "",
         "Каждое требование связано с критериями приёмки, сценариями, операциями API, событиями, экранами и тестами.", "",
         "| Показатель | Значение |", "| --- | --- |",
         f"| FR всего | {tm['fr_total']} |", f"| FR с API или событием | {tm['fr_designed_pct']}% |",
         f"| FR с экранами | {_pct_s(tm['fr_with_screens_pct'], 'спецификация экранов')} |",
         f"| FR с тестами | {_pct_s(tm['fr_with_tests_pct'], 'отчёт о тестировании')} |",
         f"| FR подтверждены тестами | {_pct_s(tm['fr_confirmed_pct'], 'отчёт о тестировании')} |", "",
         "| FR | Требование | Критерии | Сценарии | API | События | Экраны | Тесты | Статус |",
         "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        f = r["fr"]
        tests = f"{sum(1 for t in r['tests'] if t.status == 'passed')}/{len(r['tests'])}" if ti.tests else "—"
        o.append(f"| {f.id} | {_c(f.title)} | {len(f.acceptance)} | {', '.join(f.scenarios) or '—'} | "
                 f"{_c('<br>'.join(r['api']))} | {_c(', '.join(r['events']))} | {_c(', '.join(r['screens']))} | "
                 f"{tests} | {r['status'] if ti.tests else 'спроектировано'} |")
    return "\n".join(o) + "\n"


def _pct_s(v, what: str) -> str:
    return f"ожидается {what}" if v is None else f"{v}%"


def page_efficiency(ti: TeamInputs) -> str:
    o = ["# Эффективность конвейера", "",
         "Расход по этапам из `metrics.jsonl` (строка на каждый вызов LLM). Вызовы из кэша и эталонных фикстур "
         "отмечены отдельно и не отражают реальную модель.", "",
         "| Этап | Вызовов | Повторов | Из кэша / фикстур | Токены вход | Токены выход | Время LLM, с |",
         "| --- | --- | --- | --- | --- | --- | --- |"]
    tot = Counter()
    for stage, rows in sorted(ti.metrics.items()):
        c = Counter()
        for r in rows:
            c["calls"] += 1
            c["retries"] += r.get("attempt", 1) > 1
            c["cached"] += bool(r.get("cached"))
            c["in"] += r.get("input_tokens", 0)
            c["out"] += r.get("output_tokens", 0)
            c["time"] += r.get("duration_s", 0)
        tot.update(c)
        o.append(f"| {stage} | {c['calls']} | {c['retries']} | {c['cached']} | {c['in']} | {c['out']} | {round(c['time'], 1)} |")
    o.append(f"| **Итого** | {tot['calls']} | {tot['retries']} | {tot['cached']} | {tot['in']} | {tot['out']} | "
             f"{round(tot['time'], 1)} |")
    if not ti.metrics:
        o += [""] + pending("метрики этапов", "каждый этап", "<этап>/metrics.jsonl")
    return "\n".join(o) + "\n"


def service_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "service"


def _match_service(c, services: list[dict]) -> dict | None:
    key = service_slug(c.name).replace("-", "")
    for s in services:
        n = service_slug(s["name"]).replace("-", "")
        if n and (n in key or key in n):
            return s
    hints = {"backend": ("api", "backend"), "worker": ("worker",), "mock": ("mock",), "database": ("db", "postgres"),
             "broker": ("kafka",)}.get(c.kind, ())
    return next((s for s in services if any(h in s["name"].lower() for h in hints)), None)


def page_service(c, m: Model, prose: SaProse | None, ti: TeamInputs) -> str:
    """Паспорт сервиса по «Шаблону описания сервисов» организаторов."""
    s = _match_service(c, ti.infra.services)
    infra_pending = f"заполняется участником DevOps в `artifacts/{PATHS['infra_dir']}/`"
    o = [f"# Сервис {c.name}", "",
         "Паспорт по «Шаблону описания сервисов». Разделы, которые зависят от инфраструктуры, заполняются из "
         f"`docker-compose.yml`, `.env.example` и `.gitlab-ci.yml` ({infra_pending}).", "",
         "## Описание", "", c.responsibility, "", "| Название | Описание |", "| --- | --- |",
         f"| Тип приложения | {_c(c.tech)} |", f"| Прикладное ПО / Frameworks | {_c(c.tech)} |",
         "| Системное ПО | Linux, Docker |",
         "| Swagger | [openapi.yaml](../contracts/openapi.yaml) |" if c.kind == "backend" else "| Swagger | неприменимо |",
         "| Архитектура | сервисная: компоненты в отдельных контейнерах, взаимодействие по REST и событиям |", "",
         "## Ссылки", "", "| | Production | Stage | Test |", "| --- | --- | --- | --- |"]
    ports = ", ".join(s["ports"]) if s else ""
    o += [f"| Сервис | отсутствует | отсутствует | {('localhost: ' + ports) if ports else 'docker compose'} |",
          "| Логи | отсутствует | отсутствует | `docker compose logs " + (s["name"] if s else "<сервис>") + "` |", "",
          "## Требования к ресурсам", "", "Контейнер; для прототипа ограничения ресурсов не заданы."
          if not s else "Контейнер `" + s["name"] + "`; ограничения ресурсов — в `docker-compose.yml`.", "",
          "## Отказоустойчивость", ""]
    o += [f"- {x}" for x in (prose.reliability if prose else [])] or ["- Прототип работает в одном экземпляре."]
    o += ["", "## Предельные ограничения работы сервиса", ""]
    perf = [x for x in (prose.performance if prose else [])]
    if perf and c.kind in ("backend", "worker"):
        o += ["| Операция | Нагрузка | Время |", "| --- | --- | --- |"]
        o += [f"| {_c(x.operation)} | {_c(x.load)} | {_c(x.response_time)} |" for x in perf
              if (x.kind == "background") == (c.kind == "worker")]
    else:
        o.append("Не установлены для прототипа.")
    o += ["", "## Работа сервиса", ""]
    if c.kind == "backend":
        o.append(f"Принимает запросы REST (JSON, HTTPS) по пути `/api/v1`: {len(m.design.operations)} методов, "
                 "описание — в разделе API.")
    elif c.kind == "worker":
        ev = [e.name for e in m.design.events if e.producer == c.name or c.name in e.consumers]
        o.append("Фоновые задачи и обработка событий" + (": " + ", ".join(f"`{e}`" for e in ev) if ev else "") + ".")
    else:
        o.append(c.responsibility)
    o += ["", "## Авторизация", "",
          "JWT: запрос содержит заголовок `Authorization: Bearer <token>`, роль — в claims." if c.kind == "backend"
          else "Внутренний сервис, доступен только из сети docker compose.", "",
          "## Описание окружений", "", "### Prod окружение", "", "Отсутствует (прототип).", "",
          "### Stage окружение", "", "Отсутствует.", "", "### Test окружение", ""]
    if s:
        o.append(f"Контейнер `{s['name']}` в docker compose" + (f", порты {ports}" if ports else "")
                 + (f", зависит от: {', '.join(s['depends_on'])}" if s["depends_on"] else "") + ".")
    else:
        o += pending("описание тестового окружения", "участник DevOps", f"{PATHS['infra_dir']}/docker-compose.yml")
    o += ["", "## Сборка", ""]
    o += ([f"GitLab CI, стадии: {', '.join(ti.infra.ci_stages)}."] + [f"- `{j['name']}` ({j['stage']})" for j in ti.infra.ci_jobs]
          if ti.infra.ci_stages else pending("описание сборки", "участник DevOps", ".gitlab-ci.yml"))
    o += ["", "## Деплой", "",
          "Сборка образа в GitLab CI, проверка образа Trivy (уязвимость уровня HIGH и выше останавливает деплой), "
          "запуск через docker compose на VM." if ti.infra.ci_stages else "См. раздел «Запуск»; деплой — через docker compose.", "",
          "## Конфигурация", "", "Настройки — через переменные окружения (приоритет) и файлы конфигурации по умолчанию.", "",
          "## Переменные окружения для CI/CD", ""]
    if ti.infra.env_example:
        keys = [l.split("=", 1)[0].strip() for l in ti.infra.env_example.splitlines() if "=" in l and not l.lstrip().startswith("#")]
        o += ["| Имя переменной | Prod | Stage | Test | Описание |", "| --- | --- | --- | --- | --- |"]
        o += [f"| `{k}` | — | — | из `.env` | — |" for k in keys]
    else:
        o += pending("переменные окружения", "участник DevOps", f"{PATHS['infra_dir']}/.env.example")
    mt = prose.maintenance if prose else None
    o += ["", "## Мониторинг", ""]
    o += [f"- {x}" for x in (mt.monitoring if mt else [])] or ["- Health check контейнера."]
    if s:
        o.append(f"- Health check в docker compose: {'настроен' if s['healthcheck'] else 'не настроен'}.")
    o += ["- Мониторинг ОС и Docker (zabbix-agent2) в прототипе не настраивается.", "", "## Логирование", ""]
    o += [f"- {x}" for x in (mt.logging if mt else [])] or ["- Логи контейнера в stdout."]
    o += ["", "## Сетевая связность", ""]
    from .diagrams import graph
    _, edges = graph(m)
    links = [f"- {'к сервису' if b == c.name else 'от сервиса'}: {a if b == c.name else b} — {label}"
             for a, b, label in edges if c.name in (a, b)]
    o += links or ["- —"]
    o += ["", "## Доступы к сервису", ""]
    o += [f"- {x}" for x in (mt.access if mt else [])] or ["- По ролям приложения."]
    return "\n".join(o) + "\n"


NAV = [
    ("О продукте", "index.md"),
    ("Руководство пользователя", "user-guide.md"),
    ("Запуск", "run.md"),
    ("Архитектура", "architecture.md"),
    ("Журнал решений (ADR)", "adr/index.md"),
    ("API", "api.md"),
    ("Методы API", "api-methods/index.md"),
    ("Паспорта сервисов", "services/index.md"),
    ("Модель данных", "data-model.md"),
    ("Требования", "requirements.md"),
    ("Системный анализ", "system-analysis.md"),
    ("Трассируемость", "traceability.md"),
    ("Отчёт о тестировании", "test-report.md"),
    ("Отчёт о безопасности", "security-report.md"),
    ("Известные ограничения", "limitations.md"),
    ("Руководство разработчика", "dev-guide.md"),
    ("Эффективность конвейера", "efficiency.md"),
]


def mkdocs_yml(req: Requirements, adr_titles: list[tuple[str, str]],
               ui_pages: list[tuple[str, str]] | None = None) -> str:
    lines = [f"site_name: \"{req.meta.product_name.replace(chr(34), '')}\"", "docs_dir: docs", "theme:",
             "  name: material", "  language: ru", "markdown_extensions:", "  - admonition", "  - tables",
             "  - pymdownx.superfences:", "      custom_fences:", "        - name: mermaid", "          class: mermaid",
             "          format: !!python/name:pymdownx.superfences.fence_code_format", "nav:"]
    for title, path in NAV:
        if path in ("api-methods/index.md", "services/index.md"):
            lines.append(f"  - \"{title}\": {path}")
        elif path == "adr/index.md" and adr_titles:
            lines.append(f"  - \"{title}\":")
            lines.append("    - \"Список\": adr/index.md")
            lines += [f"    - \"{t.replace(chr(34), '')}\": adr/{f}" for f, t in adr_titles]
        else:
            lines.append(f"  - \"{title}\": {path}")
        if path == "user-guide.md" and ui_pages:
            lines.append("  - \"Интерфейс\":")
            lines += [f"    - \"{t.replace(chr(34), '')}\": {pth}" for t, pth in ui_pages]
    return "\n".join(lines) + "\n"
