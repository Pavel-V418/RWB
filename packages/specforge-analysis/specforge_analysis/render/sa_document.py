"""Документ системного анализа по регламенту организаторов
(«Правила проектирования и ведения документации по системному анализу», шаблон ТЗ).

Структура: 1 Оглавление; 2 Общие положения (2.1–2.6); 3 Функциональные требования (3.1 Frontend,
3.2 Backend, 3.3 Интеграции, 3.4 Метрики); 4 Нефункциональные требования (4.1–4.5).
Разделы, которых требует условие кейса, но нет в регламенте (допущения и открытые вопросы,
трассировка), вынесены в приложения; приложение В сопоставляет разделы кейса и регламента.
Таблицы строятся из тех же данных, что OpenAPI, AsyncAPI и DDL.
"""

from __future__ import annotations

import datetime as dt

from ..schemas_system import SaProse
from .contracts import STD_RESPONSES, Model, event_payload_fields, request_fields, snake, table_name
from .diagrams import components_plantuml, er_plantuml, sequence_plantuml, state_plantuml

ORIGIN_RU = {"customer": "заказчик", "derived": "выведено", "assumption": "допущение", "clarification": "уточнение"}
PRIORITY_RU = {"must": "обязательно", "should": "желательно", "could": "возможно"}
KIND_RU = {"positive": "позитивный", "negative": "негативный", "edge": "граничный"}
AUTHOR = "SpecForge, агент системного анализа"

# заголовки, которые обязан содержать документ (проверяются шлюзом этапа 2)
REQUIRED_HEADINGS = [
    "## 1 Оглавление", "## 2 Общие положения", "### 2.1 История изменения", "### 2.2 Список ответственных лиц",
    "### 2.3 Лист согласования", "### 2.4 Общие сведения", "#### 2.4.1 Термины и определения",
    "#### 2.4.2 Используемые документы", "#### 2.4.3 Общее описание изменений", "#### 2.4.4 Цель доработки",
    "#### 2.4.5 Условия и ограничения", "#### 2.4.6 Критерии успеха, критерии приемки",
    "### 2.5 Сценарии использования (UseCase)", "### 2.6 Диаграммы", "#### 2.6.1 Диаграммы последовательности UML",
    "#### 2.6.2 Иные диаграммы", "## 3 Функциональные требования", "### 3.1 Описание Frontend",
    "#### 3.1.1 Спецификация на интерфейс пользователя", "#### 3.1.2 Ссылки на макеты (Figma)",
    "### 3.2 Описание Backend", "#### 3.2.1 Интерфейсы передачи информации", "#### 3.2.2 Шаблон сервиса",
    "#### 3.2.3 Модель данных. Описание БД", "#### 3.2.4 Описание API", "### 3.3 Интеграции",
    "#### 3.3.1 Внешние интеграции", "#### 3.3.2 Внутренние интеграции", "### 3.4 Метрики",
    "## 4 Нефункциональные требования", "### 4.1 Требования к безопасности", "### 4.2 Требования к производительности",
    "### 4.3 Требования к масштабируемости", "### 4.4 Требования к надежности", "### 4.5 Требования к сопровождению",
]


def _c(s) -> str:
    return (str(s) if s not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")


def _puml(code: str) -> list[str]:
    return ["```plantuml", code, "```"]


def _constraints(f) -> str:
    parts = []
    if f.type in ("string", "text"):
        parts.append(f"до {f.max_length or (255 if f.type == 'string' else 4000)} симв.")
    if f.pattern:
        parts.append(f"`{f.pattern}`")
    if f.enum_values:
        parts.append(", ".join(f"`{v}`" for v in f.enum_values))
    if f.minimum is not None:
        parts.append(f"≥ {f.minimum:g}")
    if f.maximum is not None:
        parts.append(f"≤ {f.maximum:g}")
    if f.unique:
        parts.append("уникально")
    if f.ref:
        parts.append(f"→ {f.ref}")
    if f.pii:
        parts.append("ПДн")
    return "; ".join(parts) or "—"


_UI_ELEMENT = {"enum": "select / radio", "bool": "checkbox", "datetime": "input datetime-local", "date": "input date",
               "int": "input number", "bigint": "input number", "decimal": "input number", "text": "textarea",
               "point": "карта / геолокация устройства", "polygon": "карта / загрузка GeoJSON", "linestring": "карта",
               "json": "input file (JSON)", "uuid": "select (выбор из списка)", "string": "input text"}


def _ui_element(f) -> str:
    if f.type == "string" and f.name.lower().endswith("password"):
        return "input password"
    if f.type == "string" and "phone" in f.name.lower():
        return "input tel"
    return _UI_ELEMENT.get(f.type, "input text")


def _nfr(req, cats: tuple[str, ...]) -> list:
    return [n for n in req.non_functional if n.category in cats]


def _list(items: list[str]) -> list[str]:
    return [f"- {x}" for x in items] if items else ["Не предъявляются в рамках прототипа."]


def render(m: Model, prose: SaProse | None, coverage: dict, has_events: bool, today: str | None = None) -> str:
    req, d, dm = m.req, m.design, m.domain
    p = prose
    today = today or dt.date.today().isoformat()
    rname = {r.key: r.name for r in req.roles} | {"anonymous": "Аноним", "system": "Система"}
    br_of = {fr: b.id for b in req.business for fr in b.fr}

    o: list[str] = [f"# ТЗ. Системный анализ: {req.meta.product_name}", "",
                    "> Документ сформирован конвейером SpecForge по регламенту «Правила проектирования и ведения "
                    "документации по системному анализу». Контракты: [`openapi.yaml`](openapi.yaml)"
                    + (", [`asyncapi.yaml`](asyncapi.yaml)" if has_events else "")
                    + ", [`schema.sql`](schema.sql); методы API — в каталоге [`api-methods/`](api-methods/). "
                      "Требования заказчика, выведенные требования и допущения системы разделены столбцом «Происхождение».", ""]

    # 1 ------------------------------------------------------------------------------------------
    o += ["## 1 Оглавление", ""]
    o += [("  " * (h.count("#") - 2)) + "- " + h.lstrip("# ") for h in REQUIRED_HEADINGS if not h.startswith("## 1 ")]
    o += ["- Приложение А. Допущения и открытые вопросы", "- Приложение Б. Матрица трассируемости",
          "- Приложение В. Соответствие разделам условия кейса", ""]

    # 2 ------------------------------------------------------------------------------------------
    o += ["## 2 Общие положения", "", "### 2.1 История изменения", "",
          "| Версия | Ссылка на задачу Jira | Описание изменения | Автор | Дата |", "| --- | --- | --- | --- | --- |",
          f"| 1.0 | — | Первичная версия по бизнес-требованиям `{req.meta.source_file}` | {AUTHOR} | {today} |", "",
          "### 2.2 Список ответственных лиц", "", "| Роль | Структурное подразделение | ФИО |", "| --- | --- | --- |",
          "| Заказчик | — | — |", "| Руководитель проекта | — | — |", "| Разработчик ПО | — | — |",
          f"| Аналитик | — | {AUTHOR} |", "",
          "### 2.3 Лист согласования", "", "Статус документа: **требуется согласование**.", "",
          "| Роль | Статус | Комментарий | ФИО | Дата |", "| --- | --- | --- | --- | --- |",
          "| Заказчик | требуется согласование | — | — | — |", "| Архитектор | требуется согласование | — | — | — |",
          "| Сотрудник подразделения ИБ | требуется согласование | разделы 4.1 и 4.5 | — | — |", "",
          "Матрица ответственных за согласование артефактов:", "",
          "| Артефакт документации | Архитекторы | Product Owner | Системные аналитики | Разработка | Тестирование |",
          "| --- | --- | --- | --- | --- | --- |",
          "| Бизнес-требования | — | — | Согласование | — | — |", "| Архитектура | Согласование | — | — | — | — |",
          "| UseCase, варианты использования | — | Согласование | — | Согласование | Согласование |",
          "| UI (макеты) | — | — | Согласование | — | — |", "",
          "### 2.4 Общие сведения", "", "#### 2.4.1 Термины и определения", "", "| Термин | Определение |", "| --- | --- |"]
    o += [f"| {g.term} | {_c(g.definition)} |" for g in req.glossary]
    o += ["| FR / NFR | функциональное / нефункциональное требование |", "| BR | пункт бизнес-требований |",
          "| UC | сценарий использования (UseCase) |", "| AC | критерий приёмки |", "",
          "#### 2.4.2 Используемые документы", "", "| № | Наименование документа | Версия, дата | Ссылка или файл |",
          "| --- | --- | --- | --- |",
          f"| 1 | Бизнес-требования | {today} | `../01_requirements/normalized.md` (исходник `{req.meta.source_file}`) |",
          "| 2 | Описание архитектуры | 1.0 | раздел 2.6 и 3.2 настоящего документа |",
          "| 3 | Роли и характеристика пользователей | 1.0 | раздел 3.1.1 настоящего документа |",
          "| 4 | Связанные требования | 1.0 | `../01_requirements/requirements.md`, критерии приёмки `../01_requirements/acceptance/` |",
          "| 5 | Контракты | 1.0 | `openapi.yaml`" + (", `asyncapi.yaml`" if has_events else "") + ", `schema.sql` |", "",
          "#### 2.4.3 Общее описание изменений", "", p.purpose if p else req.meta.summary, "",
          "#### 2.4.4 Цель доработки", ""]
    o += [f"- {g.id}: {g.statement}" + (f" (метрика: {g.metric})" if g.metric else "") for g in req.goals]
    o += ["", "#### 2.4.5 Условия и ограничения", "", "Входит в доработку:", ""]
    o += [f"- {x}" for x in (p.scope_in if p else [f.title for f in req.functional])]
    o += ["", "Не входит в доработку:", ""]
    scope_out = list(p.scope_out) if p else []
    scope_out += [f"{c.statement} ({c.id})" for c in req.constraints if c.kind == "scope" and not any(c.id in s for s in scope_out)]
    o += [f"- {x}" for x in scope_out] or ["- —"]
    o += ["", "Бизнес- и системные ограничения:", "", "| ID | Тип | Ограничение | Происхождение |", "| --- | --- | --- | --- |"]
    o += [f"| {c.id} | {c.kind} | {_c(c.statement)} | {ORIGIN_RU[c.origin]} |" for c in req.constraints if c.kind != "scope"]
    o += ["", "Допущения системы и вопросы к заказчику — в приложении А.", "",
          "#### 2.4.6 Критерии успеха, критерии приемки", "", "Definition of Done:", "",
          "- реализованы все функциональные требования с приоритетом «обязательно» (раздел 3);",
          f"- выполняются все критерии приёмки ({len(req.acceptance)} шт., исполняемые сценарии Gherkin в `../01_requirements/acceptance/`);",
          "- API соответствует `openapi.yaml`, схема БД — `schema.sql`;",
          "- выполнены требования раздела 4 к безопасности и надёжности;", "",
          "| ID | FR | Тип | Дано | Когда | Тогда |", "| --- | --- | --- | --- | --- | --- |"]
    o += [f"| {a.id} | {a.fr_id} | {KIND_RU.get(a.kind, a.kind)} | {_c('; '.join(a.given))} | {_c('; '.join(a.when))} | "
          f"{_c('; '.join(a.then))} |" for a in req.acceptance]

    o += ["", "### 2.5 Сценарии использования (UseCase)", ""]
    for s in req.scenarios:
        actor = rname.get(s.actor.removeprefix("ROLE-"), s.actor)
        o += [f"#### {s.id}. {s.title}", "", "| Элемент | Описание |", "| --- | --- |",
              f"| ID | {s.id} |", f"| Название | {_c(s.title)} |", f"| Акторы | {actor}; Система |",
              f"| Цель | {_c(s.goal)} |", f"| Требования | {', '.join(s.requirements) or '—'} |", "",
              "**Предусловия:**", ""] + (_list(s.preconditions) if s.preconditions else ["- —"])
        o += ["", "**Основной поток:**", ""] + [f"{i}. {x.lstrip('0123456789. ')}" for i, x in enumerate(s.main_flow, 1)]
        if s.alternative_flows:
            o += ["", "**Альтернативные потоки:**", ""] + [f"- AP{i}: {x}" for i, x in enumerate(s.alternative_flows, 1)]
        if s.error_flows:
            o += ["", "**Исключения:**", ""] + [f"- EX{i}: {x}" for i, x in enumerate(s.error_flows, 1)]
        o += ["", "**Постусловия:**", ""] + (_list(s.postconditions) if s.postconditions else ["- —"])
        o += ["", f"**Ожидаемый результат:** {s.expected_result or '—'}", ""]

    o += ["### 2.6 Диаграммы", "", "#### 2.6.1 Диаграммы последовательности UML", ""]
    if d.sequences:
        for seq in d.sequences:
            o += [f"{seq.title}" + (f" ({seq.scenario})" if seq.scenario else "") + ":", ""] + _puml(sequence_plantuml(seq)) + [""]
    else:
        o += ["Не требуются: взаимодействие сводится к одиночным запросам, описанным в разделе 3.2.4.", ""]
    o += ["#### 2.6.2 Иные диаграммы", "", "Диаграмма компонентов:", ""] + _puml(components_plantuml(m))
    for sm in dm.state_machines:
        o += ["", f"Диаграмма состояний {sm.entity}.{sm.field}:", ""] + _puml(state_plantuml(sm))

    # 3 ------------------------------------------------------------------------------------------
    o += ["", "## 3 Функциональные требования", "",
          "Функции пронумерованы FR-n; столбец BR — трассировка к пунктам бизнес-требований (полная матрица — приложение Б). "
          "Формат описания — to be (будущая реализация).", "",
          "| FR | Требование | BR | Приоритет | Происхождение | API / события | Критерии |",
          "| --- | --- | --- | --- | --- | --- | --- |"]
    for f in req.functional:
        cov = coverage.get(f.id, {})
        impl = cov.get("api", []) + cov.get("events", []) + [f"без API: {r}" for r in cov.get("no_api", [])]
        o.append(f"| {f.id} | **{_c(f.title)}.** {_c(f.statement)} | {br_of.get(f.id, '—')} | {PRIORITY_RU[f.priority]} | "
                 f"{ORIGIN_RU[f.origin]} | {_c('; '.join(impl))} | {', '.join(f.acceptance) or '—'} |")

    clients = [c for c in d.components if c.kind in ("mobile", "web")]
    o += ["", "### 3.1 Описание Frontend", "", "#### 3.1.1 Спецификация на интерфейс пользователя", "",
          "Клиентские приложения и роли:", "", "| Приложение | Технология | Назначение |", "| --- | --- | --- |"]
    o += [f"| {c.name} | {_c(c.tech)} | {_c(c.responsibility)} |" for c in clients] or ["| — | — | Неприменимо: интерфейс не требуется |"]
    o += ["", "| Роль | Описание | Доступные методы BE |", "| --- | --- | --- |"]
    for r in req.roles:
        ops = [f"`{op.method.upper()} {op.path}`" for op in d.operations if r.key in op.roles]
        o.append(f"| {r.name} | {_c(r.description)} | {', '.join(ops) or '—'} |")
    o += ["", "Формы ввода: элементы интерфейса, маппинг на поля метода BE и колонки БД, обязательность и формат ввода. "
              "Экранные формы (таблица «ЭФ — изображение — описание») формирует этап проектирования интерфейса "
              "(`artifacts/03_ux`) и включает в документацию.", ""]
    for op in d.operations:
        rf = [f for f in request_fields(m, op) if not f.read_only and not f.internal]
        if not rf:
            continue
        ent = op.request.entity if op.request else None
        o += [f"Форма «{op.summary}» → `{op.method.upper()} {op.path}`:", "",
              "| Поле формы | Элемент UI | Обязательность | Формат ввода / regex | Поле API | Поле БД |",
              "| --- | --- | --- | --- | --- | --- |"]
        for f in rf:
            col = f"`{table_name(ent)}.{snake(f.name)}`" if ent and m.field(ent, f.name) else "—"
            o.append(f"| {_c(f.description or f.name)} | {_ui_element(f)} | {'required' if f.required else 'нет'} | "
                     f"{_c(_constraints(f))} | `{f.name}` | {col} |")
        o.append("")
    o += ["Тексты ошибок для пользователя приходят с BE в поле `title` ответа problem+json; если BE вернул нечитаемую "
          "ошибку (5xx, сеть), интерфейс показывает «Что-то пошло не так, попробуйте ещё раз». Все тексты экранов "
          "по возможности возвращает BE.", ""]
    if p and p.business_errors:
        o += ["| Код ошибки | Сообщение пользователю | Когда |", "| --- | --- | --- |"]
        o += [f"| `{b.code}` | {_c(b.title)} | {_c(b.when)} |" for b in p.business_errors]
        o.append("")
    o += ["#### 3.1.2 Ссылки на макеты (Figma)", "",
          "Неприменимо: макеты в Figma не создаются — интерфейс строится на выбранном UI Kit, скриншоты экранных форм "
          "прикладываются в документации этапа UX.", ""]

    o += ["### 3.2 Описание Backend", "", "#### 3.2.1 Интерфейсы передачи информации", ""]
    o += _list(p.interfaces if p and p.interfaces else [
        "REST, JSON (UTF-8), HTTPS, HTTP/1.1, синхронный запрос-ответ, JWT в заголовке Authorization"])
    o += ["", "#### 3.2.2 Шаблон сервиса", "",
          "Паспорт каждого сервиса оформляется по «Шаблону описания сервисов» и формируется на этапе документации "
          "(`09_docs/docs/services/`).", "", "| Сервис | Тип | Технология | Ответственность |", "| --- | --- | --- | --- |"]
    o += [f"| {c.name} | {c.kind} | {_c(c.tech)} | {_c(c.responsibility)} |" for c in d.components
          if c.kind in ("backend", "worker", "mock")]
    o += ["", "#### 3.2.3 Модель данных. Описание БД", "",
          "СУБД: PostgreSQL 17 + PostGIS, SRID 4326. Имена таблиц и полей — snake_case (например `order_id`), таблицы во "
          "множественном числе. Физическая схема — [`schema.sql`](schema.sql).", ""] + _puml(er_plantuml(m))
    for e in dm.entities:
        o += ["", f"Таблица `{table_name(e.name)}` ({e.name}) — {e.description}", "",
              "| Поле | Тип | Обязательность | Ключ | Ограничения | Описание |", "| --- | --- | --- | --- | --- | --- |"]
        for f in m.fields(e.name):
            key = "PK" if f.name == "id" else ("FK → " + table_name(f.ref) if f.ref else ("UK" if f.unique else "—"))
            o.append(f"| `{snake(f.name)}` | {f.type} | {'да' if f.required else 'нет'} | {key} | {_c(_constraints(f))} | "
                     f"{_c(f.description)}{' (только БД)' if f.internal else ''} |")
    o += ["", "#### 3.2.4 Описание API", "",
          "Общие положения: Security Scheme — HTTP Bearer (JWT); транспорт — HTTPS, HTTP/1.1; схема взаимодействия — "
          "синхронный запрос-ответ; версия в пути `/api/v1`; пути содержат только существительные, действие задаёт "
          "HTTP-метод; ошибки — RFC 9457; критичные операции — заголовок `Idempotency-Key`; превышение лимита — 429. "
          "Каждый метод описан на отдельной странице по шаблону API.", "",
          "| Метод | Назначение | Роли | FR | Успех | Страница метода |", "| --- | --- | --- | --- | --- | --- |"]
    o += [f"| `{op.method.upper()} {op.path}` | {_c(op.summary)} | {', '.join(op.roles)} | {', '.join(op.fr) or '—'} | "
          f"{op.success_status} | [{op.operation_id}](api-methods/{op.operation_id}.md) |" for op in d.operations]

    o += ["", "### 3.3 Интеграции", "", "#### 3.3.1 Внешние интеграции", "",
          "| ID | Система | Направление | Способ | В прототипе |", "| --- | --- | --- | --- | --- |"]
    for i in req.integrations:
        proto = {"mock_required": "адаптер + mock-сервис", "real": "реальная (ссылка/вызов)", "out_of_scope": "вне рамок"}.get(
            i.availability, i.availability)
        o.append(f"| {i.id} | {_c(i.name)} | {i.direction} | {i.mode} | {proto} |")
    if p and p.integration_notes:
        o += [""] + [f"- {x}" for x in p.integration_notes]
    ext_ops = [op for op in d.operations if op.external]
    if ext_ops:
        o += ["", "Вызовы внешних компонентов из методов API:", ""]
        o += [f"- `{op.method.upper()} {op.path}` → {', '.join(op.external)}" for op in ext_ops]
    o += ["", "#### 3.3.2 Внутренние интеграции", ""]
    if has_events:
        o += ["События между компонентами — Kafka, спецификация [`asyncapi.yaml`](asyncapi.yaml). Конверт события: "
              "`{eventId, type, version, occurredAt, source, correlationId, key, data}`. Доставка at-least-once через "
              "transactional outbox; потребители идемпотентны по `eventId`; 3 повтора с задержкой 1, 5, 25 с, затем "
              "топик `<topic>.dlq`; несовместимые изменения схемы — новая версия топика.", "",
              "| Событие | Топик | Производитель | Потребители | Ключ | Данные | Триггер (FR) |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
        for ev in d.events:
            fields = ", ".join(f.name for f in event_payload_fields(m, ev))
            o.append(f"| `{ev.name}` | `{ev.name}.v{ev.version}` | {ev.producer} | {', '.join(ev.consumers) or '—'} | "
                     f"`{ev.key}` | {_c(fields)} | {', '.join(ev.fr)} |")
    else:
        o += ["Неприменимо: требования не предполагают асинхронного обмена событиями между компонентами; "
              "взаимодействие синхронное через REST API (раздел 3.2.4)."]
    o += ["", "### 3.4 Метрики", ""]
    if p and p.metrics:
        o += ["| Метрика | Описание | Источник |", "| --- | --- | --- |"]
        o += [f"| {_c(x.name)} | {_c(x.description)} | {_c(x.source)} |" for x in p.metrics]
    else:
        o += [f"- {g.metric}" for g in req.goals if g.metric] or ["Метрики не заданы бизнес-требованиями."]

    # 4 ------------------------------------------------------------------------------------------
    o += ["", "## 4 Нефункциональные требования", "", "### 4.1 Требования к безопасности", "",
          "| ID | Категория | Требование | Связано |", "| --- | --- | --- | --- |"]
    o += [f"| {s.id} | {s.category} | {_c(s.statement)} | {', '.join(s.related) or '—'} |" for s in req.security]
    o += [f"| {n.id} | {n.category} | {_c(n.statement)} | — |" for n in _nfr(req, ("security", "privacy"))]
    o += ["", "Раздел подлежит согласованию с подразделением информационной безопасности (п. 2.3).", "",
          "### 4.2 Требования к производительности", ""]
    perf = list(p.performance) if p else []
    pn = _nfr(req, ("performance",))
    if pn:
        o += ["| ID | Требование | Метрика |", "| --- | --- | --- |"]
        o += [f"| {n.id} | {_c(n.statement)} | {_c(n.metric)} |" for n in pn]
        o.append("")
    blocks = [("4.2.1 Производительность операций пользовательского интерфейса", "ui", "Уровень нагрузки в час пик", "load"),
              ("4.2.2 Производительность вспомогательных операций", "background", "Уровень нагрузки в час пик", "load"),
              ("4.2.3 Производительность вспомогательных систем", "background", "Уровень нагрузки", "load"),
              ("4.2.4 Производительность внешних интерфейсов и информационных систем", "external", "Максимальная нагрузка", "load"),
              ("4.2.5 Время отклика операций пользовательского интерфейса", "ui", "Время отклика", "response_time"),
              ("4.2.6 Время обработки вспомогательных операций", "background", "Длительность", "response_time"),
              ("4.2.7 Время обработки внешних интерфейсов и информационных систем", "external", "Длительность", "response_time")]
    for title, kind, col, attr in blocks:
        rows = [x for x in perf if x.kind == kind]
        o += [f"#### {title}", ""]
        if rows:
            o += [f"| № | Наименование операции | {col} |", "| --- | --- | --- |"]
            o += [f"| {i} | {_c(x.operation)} | {_c(getattr(x, attr))} |" for i, x in enumerate(rows, 1)]
        else:
            o.append("Неприменимо: операций этого вида нет.")
        o.append("")
    o += ["### 4.3 Требования к масштабируемости", ""]
    o += _list((p.scalability if p else []) + [f"{n.id}: {n.statement}" for n in _nfr(req, ("scalability",))])
    o += ["", "### 4.4 Требования к надежности", ""]
    o += _list((p.reliability if p else []) + [f"{n.id}: {n.statement}" + (f" ({n.metric})" if n.metric else "")
                                               for n in _nfr(req, ("reliability", "availability"))])
    mt = p.maintenance if p else None
    obs = [f"{n.id}: {n.statement}" for n in _nfr(req, ("observability",))]
    other = [f"{n.id}: {n.statement}" for n in _nfr(req, ("maintainability", "compatibility", "usability", "other"))]
    o += ["", "### 4.5 Требования к сопровождению", ""]
    for title, items in [("4.5.1 Требования к резервированию и восстановлению", mt.backup if mt else []),
                         ("4.5.2 Требования к системе архивирования", mt.archiving if mt else []),
                         ("4.5.3 Требования к управлению доступом", mt.access if mt else []),
                         ("4.5.4 Требования к протоколированию", (mt.logging if mt else []) + obs[:1]),
                         ("4.5.5 Требования к мониторингу информационной системы", (mt.monitoring if mt else []) + obs[1:]),
                         ("4.5.6 Требования к подсистеме регистрации событий (аудит)", mt.audit if mt else []),
                         ("4.5.7 Удаленный доступ к информационной системе", mt.remote_access if mt else [])]:
        o += [f"#### {title}", ""] + _list(items) + [""]
    if other:
        o += ["Прочие требования к сопровождению и совместимости:", ""] + [f"- {x}" for x in other] + [""]

    # Приложения ---------------------------------------------------------------------------------
    o += ["## Приложение А. Допущения и открытые вопросы", "",
          "Вопросы к заказчику (не больше 5; без ответа применено умолчание):", "",
          "| ID | Вопрос | Влияние | Статус | Принятое решение |", "| --- | --- | --- | --- | --- |"]
    status = {"open": "открыт", "answered": "ответ заказчика", "defaulted": "умолчание"}
    o += [f"| {q.id} | {_c(q.question)} | {q.impact} | {status[q.status]} | {_c(q.answer)} |" for q in req.questions]
    o += ["", "Допущения системы (не являются требованиями заказчика):", "",
          "| ID | Допущение | Причина | Влияет на |", "| --- | --- | --- | --- |"]
    o += [f"| {a.id} | {_c(a.statement)} | {_c(a.reason)} | {', '.join(a.affected) or '—'} |" for a in req.assumptions]
    o += ["", "Риски:", "", "| ID | Риск | Вероятность / влияние | Снижение |", "| --- | --- | --- | --- |"]
    o += [f"| {r.id} | {_c(r.statement)} | {r.probability} / {r.impact} | {_c(r.mitigation)} |" for r in req.risks]

    o += ["", "## Приложение Б. Матрица трассируемости", "",
          "| BR | Бизнес-требование | FR | UC | AC | API / события |", "| --- | --- | --- | --- | --- | --- |"]
    fr_by_id = {f.id: f for f in req.functional}
    for b in req.business:
        if not b.fr:
            o.append(f"| {b.id} | {_c(b.text)} | — | — | — | {_c(', '.join(b.related))} |")
            continue
        for fid in b.fr:
            f = fr_by_id[fid]
            cov = coverage.get(fid, {})
            impl = cov.get("api", []) + cov.get("events", [])
            o.append(f"| {b.id} | {_c(b.text)} | {fid} | {', '.join(f.scenarios) or '—'} | {len(f.acceptance)} | "
                     f"{_c('; '.join(impl))} |")
    derived = [f for f in req.functional if f.id not in br_of]
    for f in derived:
        cov = coverage.get(f.id, {})
        o.append(f"| — (выведено) | {_c(f.title)} | {f.id} | {', '.join(f.scenarios) or '—'} | {len(f.acceptance)} | "
                 f"{_c('; '.join(cov.get('api', []) + cov.get('events', [])))} |")

    o += ["", "## Приложение В. Соответствие разделам условия кейса", "",
          "| Раздел из условия кейса | Где в документе |", "| --- | --- |",
          "| Назначение системы | 2.4.3, 2.4.4 |", "| Границы решения | 2.4.5 |",
          "| Роли и права доступа | 3.1.1 (роли и методы), страницы методов API («Кому разрешено вызывать метод»), 4.1 |",
          "| Пользовательские сценарии | 2.5 |", "| Функциональные требования | 3 |", "| Нефункциональные требования | 4 |",
          "| Описание компонентов | 2.6.2, 3.2.2 |", "| Модель данных | 3.2.3 |", "| API-контракты | 3.2.4, `openapi.yaml`, `api-methods/` |",
          "| События и интеграции | 3.3 |", "| Ограничения и проверки входных данных | 3.1.1 (формы ввода), страницы методов API |",
          "| Обработка ошибок | 3.1.1 (тексты ошибок), страницы методов API («Неуспешная обработка запроса») |",
          "| Критерии приёмки | 2.4.6 |", "| Допущения и открытые вопросы | Приложение А |"]
    return "\n".join(o).rstrip() + "\n"


def missing_headings(text: str) -> list[str]:
    lines = set(text.splitlines())
    return [h for h in REQUIRED_HEADINGS if h not in lines]
