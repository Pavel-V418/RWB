"""Страницы методов API по шаблону организаторов («Шаблон API» и раздел 3.2.4 регламента).

Каждый метод — отдельная страница: основная информация, история изменений, входящие параметры
(авторизация, заголовки, path, query, тело, пример, кому разрешено), исходящие параметры
(коды, модель ответа, примеры успешного и неуспешного ответа), алгоритм, взаимодействие
с таблицами и внешними компонентами. Всё, кроме алгоритма, выводится из контрактов.
"""

from __future__ import annotations

import datetime as dt
import re

from ..schemas_system import DField, Operation
from .contracts import STD_RESPONSES, Model, error_code, error_type, request_fields, response_fields, snake, table_name
from .examples import example_object, example_value, to_json

UI_ROLE_ANON = "anonymous"


def _c(s) -> str:
    return (str(s) if s not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")


def _type(f: DField) -> str:
    return {"uuid": "string (uuid)", "string": "string", "text": "string", "int": "integer", "bigint": "integer (int64)",
            "decimal": "number", "bool": "boolean", "date": "string (date)", "datetime": "string (date-time, ISO 8601)",
            "enum": "string (enum)", "json": "object", "point": "object (GeoJSON Point)",
            "polygon": "object (GeoJSON Polygon)", "linestring": "object (GeoJSON LineString)"}[f.type]


def _variants(f: DField) -> str:
    parts = []
    if f.enum_values:
        parts.append(", ".join(f"`{v}`" for v in f.enum_values))
    if f.type in ("string", "text"):
        parts.append(f"до {f.max_length or (255 if f.type == 'string' else 4000)} симв.")
    if f.pattern:
        parts.append(f"regex `{f.pattern}`")
    if f.minimum is not None:
        parts.append(f"≥ {f.minimum:g}")
    if f.maximum is not None:
        parts.append(f"≤ {f.maximum:g}")
    if f.default is not None:
        parts.append(f"по умолчанию `{f.default}`")
    return "; ".join(parts) or "—"


def _column(m: Model, entity: str | None, f: DField) -> str:
    if entity and m.field(entity, f.name):
        return f"`{table_name(entity)}.{snake(f.name)}`"
    return "— (не хранится)"


def path_params(path: str) -> list[str]:
    return re.findall(r"\{([A-Za-z0-9_]+)\}", path)


def slug(op: Operation) -> str:
    return op.operation_id


def _codes_from_exceptions(op: Operation) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for e in op.exceptions:
        mm = re.search(r"→\s*(\d{3})", e)
        if mm:
            out.setdefault(int(mm.group(1)), []).append(e.split("→")[0].strip())
    return out


_ERR_NAMES = {400: "validation_error", 401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict",
              422: "business_rule_violation", 429: "too_many_requests", 500: "internal_error",
              502: "external_provider_error", 503: "service_unavailable"}


def _err_name(code: int) -> str:
    return _ERR_NAMES.get(code, f"error_{code}")


def error_codes(op: Operation) -> list[int]:
    codes = set(op.errors) | set(_codes_from_exceptions(op))
    if op.request or op.query or path_params(op.path):
        codes.add(400)
    if UI_ROLE_ANON not in op.roles:
        codes.add(403)
    codes |= {401, 429, 500}
    return sorted(codes)


def render_method(m: Model, op: Operation, role_names: dict[str, str], today: str, berrs: list | None = None) -> str:
    method = op.method.upper()
    rf = request_fields(m, op)
    resp = response_fields(m, op)
    req_entity = op.request.entity if op.request else None
    res_entity = op.response.entity
    anonymous = UI_ROLE_ANON in op.roles
    o = [f"# Метод {method} {op.path}", "",
         "## Основная информация", "", "| Параметр | Значение |", "| --- | --- |",
         f"| Тип | {method} |", f"| Наименование метода | `{op.operation_id}` |",
         f"| Назначение метода | {_c(op.summary)} |",
         f"| Ссылка на Swagger | [`openapi.yaml`](../openapi.yaml), operationId `{op.operation_id}` |",
         f"| Входящие атрибуты запроса | {_c(', '.join(path_params(op.path) + [q.name for q in op.query] + [f.name for f in rf]))} |",
         f"| Реализует требования | {', '.join(op.fr) or '—'} |", "",
         "## История изменений", "", "| Дата | Версия | Описание изменения | Задача в JIRA | Автор |",
         "| --- | --- | --- | --- | --- |", f"| {today} | 1.0 | Описание метода | — | SpecForge, агент системного анализа |", "",
         "## 1. Назначение", "", f"{op.summary}.", "",
         "Транспорт: HTTPS (TLS 1.2+), HTTP/1.1. Схема взаимодействия: синхронный запрос — ответ. Формат тела: JSON, UTF-8.", ""]

    # ----------------------------------------------------------------- входящие параметры
    o += ["## 2. Входные данные (Входящие параметры)", "", "### Авторизация", "",
          "Не требуется (метод выдаёт токен)." if anonymous else
          "JWT в заголовке `Authorization: Bearer <token>`; роль пользователя берётся из claims токена.", "",
          "### Заголовки", "", "| Наименование | Описание | Тип данных | Обязательность | Пример |",
          "| --- | --- | --- | --- | --- |"]
    if not anonymous:
        o.append("| Authorization | токен доступа | string | да | `Bearer eyJhbGciOi…` |")
    if op.request:
        o.append("| Content-Type | формат тела запроса | string | да | `application/json` |")
    if op.idempotent:
        o.append("| Idempotency-Key | ключ идемпотентности: повтор с тем же ключом возвращает первый результат | string (uuid) | да | "
                 "`6f1c2b7e-3d4a-4e5f-9a8b-1c2d3e4f5a6b` |")
    o.append("| X-Correlation-Id | сквозной идентификатор запроса; если не передан — генерируется сервером | string | нет | "
             "`req-20261030-0001` |")
    pp = path_params(op.path)
    o += ["", "### Параметры path", ""]
    if pp:
        o += ["| Наименование | Описание | Тип данных | Обязательность | Пример |", "| --- | --- | --- | --- | --- |"]
        o += [f"| {p} | идентификатор ресурса | string (uuid) | да | `{example_value(DField(name='id', type='uuid'))}` |" for p in pp]
    else:
        o.append("Отсутствуют.")
    o += ["", "### Параметры query", ""]
    q = list(op.query)
    if op.response.kind == "page":
        o_page = [("page", "номер страницы, с 1", "integer", "нет", "1"), ("pageSize", "размер страницы, ≤ 200", "integer", "нет", "50")]
    else:
        o_page = []
    if q or o_page:
        o += ["| Наименование | Описание | Тип данных | Обязательность | Пример |", "| --- | --- | --- | --- | --- |"]
        for p in q:
            f = DField(name=p.name, type=p.type, enum_values=p.enum_values)
            o.append(f"| {p.name} | {_c(p.description)} | {_type(f)} | {'да' if p.required else 'нет'} | `{example_value(f)}` |")
        o += [f"| {a} | {b} | {c} | {d} | `{e}` |" for a, b, c, d, e in o_page]
        o += ["", "Если необязательный параметр не передан, фильтр не применяется; некорректное значение — ответ 400."]
    else:
        o.append("Отсутствуют.")
    o += ["", "### Модель данных тела запроса", ""]
    if op.request and rf:
        o += ["| Поле | Описание | Тип данных | Обязательность | Варианты значений | Поле БД |",
              "| --- | --- | --- | --- | --- | --- |"]
        for f in rf:
            if f.read_only or f.internal:
                continue
            o.append(f"| {f.name} | {_c(f.description)} | {_type(f)} | {'да' if f.required else 'нет'} | {_c(_variants(f))} | "
                     f"{_column(m, req_entity, f)} |")
        o += ["", "Если обязательное поле не передано или не проходит проверку — ответ 400 с перечнем полей в `errors`. "
                  "Лишние поля отклоняются (`additionalProperties: false`)."]
        body = example_object(rf, writable=True)
    else:
        o.append("Тело запроса отсутствует.")
        body = None
    url = op.path
    for p in pp:
        url = url.replace("{" + p + "}", example_value(DField(name="id", type="uuid")))
    if op.query:
        first = op.query[0]
        url += f"?{first.name}={example_value(DField(name=first.name, type=first.type, enum_values=first.enum_values))}"
    req_lines = [f"{method} {url} HTTP/1.1", "Host: localhost:8080"]
    if not anonymous:
        req_lines.append("Authorization: Bearer <token>")
    if op.idempotent:
        req_lines.append("Idempotency-Key: 6f1c2b7e-3d4a-4e5f-9a8b-1c2d3e4f5a6b")
    if body is not None:
        req_lines.append("Content-Type: application/json")
    o += ["", "### Пример запроса", "", "```http", *req_lines, "", to_json(body) if body is not None else "", "```", "",
          "### Кому разрешено вызывать метод", ""]
    o += [f"- {role_names.get(r, r)} (`{r}`)" for r in op.roles] or ["- —"]
    o += ["", "Остальным ролям — ответ 403." if not anonymous else "Метод доступен без токена."]

    # ----------------------------------------------------------------- исходящие параметры
    ok = op.success_status
    ok_desc = {200: "Запрос успешно обработан", 201: "Ресурс создан", 202: "Запрос принят в обработку",
               204: "Запрос успешно обработан, тело ответа отсутствует"}.get(ok, "Запрос успешно обработан")
    o += ["", "## 3. Выходные данные (Исходящие параметры)", "", "### Успешная обработка запроса", "",
          "| HTTP Code | Описание | Возвращаемая информация |", "| --- | --- | --- |",
          f"| {ok} | {ok_desc} | {_c(op.response.kind if resp is not None else 'нет тела')} |", ""]
    if resp is not None:
        wrap = {"object": "объект", "list": "массив объектов", "page": "страница `{items, page, pageSize, total}`"}[op.response.kind]
        o += ["#### Модель данных тела ответа", "", f"Формат: {wrap}.", "",
              "| Поле | Описание | Тип данных | Обязательность | Поле БД / ограничения |", "| --- | --- | --- | --- | --- |"]
        for f in resp:
            if f.internal:
                continue
            o.append(f"| {f.name} | {_c(f.description)} | {_type(f)} | {'да' if f.required else 'нет'} | "
                     f"{_column(m, res_entity, f)}; {_c(_variants(f))} |")
        item = example_object(resp)
        ex = item if op.response.kind == "object" else ([item] if op.response.kind == "list" else
                                                         {"items": [item], "page": 1, "pageSize": 50, "total": 1})
        o += ["", "Необязательное поле без значения возвращается как `null`; пустой список — `[]`.", "",
              "#### Пример успешного ответа", "", "```json", to_json(ex), "```"]
    exc = _codes_from_exceptions(op)
    o += ["", "### Неуспешная обработка запроса", "", "| HTTP Code | Описание |", "| --- | --- |"]
    for code in error_codes(op):
        base = STD_RESPONSES.get(code, ("", f"Код {code}"))[1]
        extra = "; ".join(exc.get(code, []))
        o.append(f"| {code} | {_c(base + (': ' + extra if extra else ''))} |")
    o += ["", "#### Модель данных тела ответа (application/problem+json, RFC 9457)", "",
          "| Поле | Описание | Тип данных | Обязательность |", "| --- | --- | --- | --- |",
          "| type | URI типа ошибки | string | да |", "| title | краткое описание для пользователя | string | да |",
          "| status | HTTP-код | integer | да |", "| detail | подробности | string | нет |",
          "| code | машиночитаемый код бизнес-ошибки | string | нет |", "| traceId | идентификатор для поиска в логах | string | нет |",
          "| errors | ошибки валидации: `{\"поле\": [\"сообщение\"]}` | object | нет |", ""]
    first_code = next(iter(sorted(exc)), None) or next((c for c in error_codes(op) if c not in (401, 403, 500)), 401)
    o += ["#### Пример неуспешного ответа", "", "```json", to_json({
        "type": error_type(_berr(berrs, first_code, exc) or _err_name(first_code)), "title": STD_RESPONSES.get(first_code, ("", "Ошибка"))[1],
        "status": first_code, "detail": (exc.get(first_code) or ["описание причины"])[0],
        "code": error_code(_berr(berrs, first_code, exc) or _err_name(first_code)), "traceId": "00-4bf92f3577b34da6a3ce929d0e0e4736-01"}
        | ({"errors": {"fieldName": ["Поле обязательно"]}} if first_code == 400 else {})), "```"]

    # ----------------------------------------------------------------- алгоритм и взаимодействия
    o += ["", "## 4. Описание алгоритма работы метода", "", "**Основной сценарий:**", ""]
    o += [f"{i}. {s.lstrip('0123456789. ')}" for i, s in enumerate(op.algorithm, 1)] or ["Не описан."]
    o += ["", "**Сценарий исключений:**", ""] + ([f"- {e}" for e in op.exceptions] or ["- Стандартная обработка ошибок (см. раздел 3)."])
    checks = [f"`{f.name}`: {_variants(f)}" + (", обязательно" if f.required else "")
              for f in rf if not f.read_only and not f.internal and _variants(f) != "—"]
    if checks:
        o += ["", "**Проверки и валидации:**", ""] + [f"- {c}" for c in checks]
    o += ["", "**Кэширование:** " + (op.caching or "не применяется."),
          "", "**Производительность:** " + "; ".join(x for x in [
              "ограничение частоты запросов, превышение — 429 и `Retry-After`" if op.rate_limited else "",
              "пагинация, pageSize ≤ 200" if op.response.kind == "page" else "",
              "таймаут обработки 30 с"] if x) + "."]
    o += ["", "## 5. Взаимодействие с внешними компонентами. Источники данных", "",
          "Внешние компоненты: " + (", ".join(op.external) if op.external else "не вызываются") + ".", "",
          "### Взаимодействие с таблицами", "", "| Наименование таблицы | Описание таблицы |", "| --- | --- |"]
    tables = list(dict.fromkeys([t for t in op.tables if t in m.entities]
                                + [e for e in (req_entity, res_entity) if e and e in m.entities]))
    o += [f"| `{table_name(t)}` | {_c(m.entities[t].description)} |" for t in tables] or ["| — | — |"]
    return "\n".join(o) + "\n"


def _berr(berrs: list | None, status: int, exc: dict[int, list[str]]) -> str | None:
    """Бизнес-ошибка из описания СА с тем же HTTP-кодом, ближайшая по словам к исключению метода."""
    cands = [b for b in berrs or [] if b.status == status]
    if not cands:
        return None
    words = set(re.findall(r"\w{4,}", " ".join(exc.get(status, [])).lower()))
    return max(cands, key=lambda b: len(words & set(re.findall(r"\w{4,}", f"{b.title} {b.when}".lower())))).code


def render_all(m: Model, today: str | None = None, business_errors: list | None = None) -> dict[str, str]:
    today = today or dt.date.today().isoformat()
    role_names = {r.key: r.name for r in m.req.roles} | {"anonymous": "Аноним", "system": "Система"}
    return {f"api-methods/{slug(op)}.md": render_method(m, op, role_names, today, business_errors) for op in m.design.operations}
