"""Детерминированный рендер контрактов этапа 2: OpenAPI 3.1, AsyncAPI 3.0, DDL PostgreSQL/PostGIS.

Ни одного вызова LLM: всё строится из DomainModel + ApiDesign. Поэтому поле, объявленное в
доменной модели, имеет один и тот же тип, длину и формат в API, в событиях и в БД.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..schemas import Requirements
from ..schemas_system import ApiDesign, DEntity, DField, DomainModel, Operation

API_PREFIX = "/api/v1"


# =========================================================================== имена


def snake(name: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return s.lower()


def plural(word: str) -> str:
    if re.search(r"[^aeiou]y$", word):
        return word[:-1] + "ies"
    if re.search(r"(s|x|z|ch|sh)$", word):
        return word + "es"
    return word + "s"


def table_name(entity: str) -> str:
    parts = snake(entity).split("_")
    parts[-1] = plural(parts[-1])
    return "_".join(parts)


def pascal(name: str) -> str:
    return name[:1].upper() + name[1:]


AUDIT_FIELDS = [
    DField(name="createdAt", type="datetime", required=True, read_only=True, description="время создания"),
    DField(name="updatedAt", type="datetime", required=True, read_only=True, description="время изменения"),
]


@dataclass
class Model:
    """Доменная модель с индексами для быстрого поиска полей."""

    domain: DomainModel
    design: ApiDesign
    req: Requirements

    def __post_init__(self):
        self.entities: dict[str, DEntity] = {e.name: e for e in self.domain.entities}

    def fields(self, entity: str) -> list[DField]:
        e = self.entities[entity]
        base = list(e.fields)
        if not any(f.name == "id" for f in base):
            base.insert(0, DField(name="id", type="uuid", required=True, read_only=True, description="идентификатор"))
        if e.audit:
            names = {f.name for f in base}
            base += [f for f in AUDIT_FIELDS if f.name not in names]
        return base

    def field(self, entity: str, name: str) -> DField | None:
        for f in self.fields(entity):
            if f.name == name:
                return f
        return None

    def enum_type(self, entity: str, f: DField) -> str:
        return f"{snake(entity)}_{snake(f.name)}"


# =========================================================================== OpenAPI


GEOJSON = {
    "GeoJsonPoint": {
        "type": "object", "description": "GeoJSON Point, WGS 84 (EPSG:4326), порядок [долгота, широта]",
        "required": ["type", "coordinates"],
        "properties": {"type": {"type": "string", "const": "Point"},
                       "coordinates": {"type": "array", "minItems": 2, "maxItems": 2,
                                       "prefixItems": [{"type": "number", "minimum": -180, "maximum": 180},
                                                       {"type": "number", "minimum": -90, "maximum": 90}]}}},
    "GeoJsonPolygon": {
        "type": "object", "description": "GeoJSON Polygon, WGS 84 (EPSG:4326)",
        "required": ["type", "coordinates"],
        "properties": {"type": {"type": "string", "const": "Polygon"},
                       "coordinates": {"type": "array", "minItems": 1,
                                       "items": {"type": "array", "minItems": 4,
                                                 "items": {"type": "array", "minItems": 2, "maxItems": 2,
                                                           "items": {"type": "number"}}}}}},
    "GeoJsonLineString": {
        "type": "object", "description": "GeoJSON LineString, WGS 84 (EPSG:4326)",
        "required": ["type", "coordinates"],
        "properties": {"type": {"type": "string", "const": "LineString"},
                       "coordinates": {"type": "array", "minItems": 2,
                                       "items": {"type": "array", "minItems": 2, "maxItems": 2,
                                                 "items": {"type": "number"}}}}},
}

PROBLEM = {
    "Problem": {
        "type": "object", "description": "Единый формат ошибок, RFC 9457 (application/problem+json)",
        "required": ["type", "title", "status"],
        "properties": {
            "type": {"type": "string", "format": "uri-reference", "example": "/errors/validation"},
            "title": {"type": "string", "maxLength": 200},
            "status": {"type": "integer", "minimum": 400, "maximum": 599},
            "detail": {"type": "string", "maxLength": 2000},
            "instance": {"type": "string"},
            "code": {"type": "string", "description": "машиночитаемый код бизнес-ошибки"},
            "traceId": {"type": "string", "description": "идентификатор трассировки для поиска в логах"},
            "errors": {"type": "object", "description": "ошибки валидации: {поле: [сообщения]} (ValidationProblemDetails)",
                       "additionalProperties": {"type": "array", "items": {"type": "string"}}},
        },
    }
}

STD_RESPONSES = {
    400: ("BadRequest", "Ошибка валидации запроса"),
    401: ("Unauthorized", "Нет или истёк токен доступа"),
    403: ("Forbidden", "Недостаточно прав для операции"),
    404: ("NotFound", "Ресурс не найден"),
    409: ("Conflict", "Конфликт состояния ресурса"),
    422: ("UnprocessableEntity", "Нарушено бизнес-правило"),
    429: ("TooManyRequests", "Превышен лимит запросов"),
    500: ("InternalError", "Внутренняя ошибка"),
    502: ("BadGateway", "Ошибка или таймаут внешнего поставщика (code: external_provider_error)"),
    503: ("ServiceUnavailable", "Сервис временно недоступен"),
}


def _tag(m: "Model", name: str) -> dict:
    """Описание тега берём из сущности, которую отдают его операции (без вызова модели)."""
    for op in m.design.operations:
        if op.tag == name and op.response.entity and op.response.entity in m.entities:
            desc = m.entities[op.response.entity].description
            if desc:
                return {"name": name, "description": desc}
    return {"name": name}


ERROR_TYPE_BASE = "https://errors.app.local/"


def error_code(code: str) -> str:
    """Машиночитаемый код бизнес-ошибки — snake_case; в type — тот же код через дефис."""
    return re.sub(r"[^a-z0-9]+", "_", code.lower()).strip("_")


def error_type(code: str) -> str:
    return ERROR_TYPE_BASE + error_code(code).replace("_", "-")


def field_schema(f: DField) -> dict:
    t = f.type
    if t in ("point", "polygon", "linestring"):
        ref = {"point": "GeoJsonPoint", "polygon": "GeoJsonPolygon", "linestring": "GeoJsonLineString"}[t]
        s: dict = {"$ref": f"#/components/schemas/{ref}"}
        if f.description or f.title:
            s = {"allOf": [s]}
            if f.title:
                s["title"] = f.title
            if f.description:
                s["description"] = f.description
        return s
    s = {
        "uuid": {"type": "string", "format": "uuid"},
        "string": {"type": "string"},
        "text": {"type": "string"},
        "int": {"type": "integer", "format": "int32"},
        "bigint": {"type": "integer", "format": "int64"},
        "decimal": {"type": "number"},
        "bool": {"type": "boolean"},
        "date": {"type": "string", "format": "date"},
        "datetime": {"type": "string", "format": "date-time"},
        "enum": {"type": "string", "enum": list(f.enum_values)},
        "json": {"type": "object", "additionalProperties": True},
    }[t].copy()
    if f.title:
        s["title"] = f.title
    if f.description:
        s["description"] = f.description
    if t == "enum" and f.enum_labels:
        s["x-enum-labels"] = {v: f.enum_labels.get(v, v) for v in f.enum_values}
    if t in ("string", "text"):
        s["maxLength"] = f.max_length or (255 if t == "string" else 4000)
        if f.required:
            s["minLength"] = 1
        if f.pattern:
            s["pattern"] = f.pattern
    if t in ("int", "bigint", "decimal"):
        for key, val in (("minimum", f.minimum), ("maximum", f.maximum)):
            if val is not None:
                s[key] = int(val) if (t != "decimal" or float(val).is_integer()) else val
    if f.read_only:
        s["readOnly"] = True
    d = _default_value(f)
    if d is not None:
        s["default"] = d
    return s


def _default_value(f: DField):
    """Значение по умолчанию в типе поля; неразборчивое значение от модели просто игнорируется."""
    v = (f.default or "").strip()
    if not v:
        return None
    if f.type == "bool":
        return {"true": True, "false": False}.get(v.lower())
    if f.type in ("int", "bigint", "decimal"):
        try:
            return int(v) if re.fullmatch(r"-?\d+", v) else float(v)
        except ValueError:
            return None
    if f.type == "enum":
        return v if v in f.enum_values else None
    if f.type in ("string", "text"):
        return v
    return None


def object_schema(fields: list[DField], *, writable: bool = False, description: str = "") -> dict:
    props, required = {}, []
    for f in fields:
        if f.internal or (writable and f.read_only):
            continue
        props[f.name] = field_schema(f)
        if f.required:
            required.append(f.name)
    s: dict = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        s["required"] = required
    if description:
        s["description"] = description
    return s


def _path_params(path: str) -> list[str]:
    return re.findall(r"\{([A-Za-z0-9_]+)\}", path)


def request_fields(m: Model, op: Operation) -> list[DField]:
    out: list[DField] = []
    if op.request:
        if op.request.entity:
            for name in op.request.fields:
                f = m.field(op.request.entity, name)
                if f:
                    out.append(f)
        out += op.request.extra
    return out


def response_fields(m: Model, op: Operation) -> list[DField] | None:
    r = op.response
    if r.kind == "none":
        return None
    out: list[DField] = []
    if r.entity:
        if r.fields:
            out = [f for f in (m.field(r.entity, n) for n in r.fields) if f]
        else:
            out = m.fields(r.entity)
    return out + list(r.extra)


def build_openapi(m: Model) -> dict:
    req = m.req
    schemas: dict = {}
    for e in m.domain.entities:
        schemas[e.name] = object_schema(m.fields(e.name), description=e.description)
    schemas.update(GEOJSON)
    schemas.update(PROBLEM)

    responses = {name: {"description": desc,
                        "content": {"application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}}}
                 for _, (name, desc) in STD_RESPONSES.items()}
    responses["TooManyRequests"]["headers"] = {
        "Retry-After": {"description": "через сколько секунд повторить", "schema": {"type": "integer"}}}

    paths: dict = {}
    tags: dict[str, None] = {}
    for op in m.design.operations:
        tags[op.tag] = None
        o: dict = {"operationId": op.operation_id, "summary": op.summary, "tags": [op.tag],
                   "x-requirements": op.fr, "x-roles": op.roles}
        if op.roles:
            o["description"] = "Роли: " + ", ".join(op.roles) + ". Требования: " + ", ".join(op.fr) + "."
        anonymous = "anonymous" in op.roles
        o["security"] = [] if anonymous else [{"bearerAuth": []}]

        params: list[dict] = []
        for p in _path_params(op.path):
            params.append({"name": p, "in": "path", "required": True,
                           "schema": {"type": "string", "format": "uuid"} if p.lower().endswith("id")
                           else {"type": "string", "maxLength": 100}})
        for q in op.query:
            qs = field_schema(DField(name=q.name, type=q.type, required=q.required,
                                     enum_values=q.enum_values, description=""))
            qs.pop("minLength", None)
            params.append({"name": q.name, "in": "query", "required": q.required,
                           "description": q.description, "schema": qs})
        if op.response.kind == "page":
            params += [{"$ref": "#/components/parameters/Page"}, {"$ref": "#/components/parameters/PageSize"}]
        if op.idempotent:
            params.append({"$ref": "#/components/parameters/IdempotencyKey"})
        if params:
            o["parameters"] = params

        rf = request_fields(m, op)
        if op.request and rf:
            name = f"{pascal(op.operation_id)}Request"
            schemas[name] = object_schema(rf, writable=True)
            o["requestBody"] = {"required": True,
                                "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{name}"}}}}

        resp: dict = {}
        fields = response_fields(m, op)
        if fields is None:
            resp[str(op.success_status)] = {"description": "Успешно"}
        else:
            r = op.response
            if r.entity and not r.fields and not r.extra:
                item_ref = {"$ref": f"#/components/schemas/{r.entity}"}
            else:
                name = f"{pascal(op.operation_id)}Item" if r.kind != "object" else f"{pascal(op.operation_id)}Response"
                schemas[name] = object_schema(fields)
                item_ref = {"$ref": f"#/components/schemas/{name}"}
            if r.kind == "object":
                body = item_ref
            elif r.kind == "list":
                body = {"type": "array", "items": item_ref}
            else:
                body = {"type": "object", "required": ["items", "page", "pageSize", "total"],
                        "properties": {"items": {"type": "array", "items": item_ref},
                                       "page": {"type": "integer", "minimum": 1},
                                       "pageSize": {"type": "integer", "minimum": 1, "maximum": 200},
                                       "total": {"type": "integer", "minimum": 0}}}
            resp[str(op.success_status)] = {"description": "Успешно",
                                            "content": {"application/json": {"schema": body}}}
        codes = set(op.errors)
        if op.request or op.query or _path_params(op.path):
            codes.add(400)
        if not anonymous:
            codes |= {401, 403}
        # шаблон API организаторов: 401 и 500 у каждого метода; лимит запросов (429) — тоже у каждого
        codes |= {401, 429, 500}
        for c in sorted(codes):
            if c in STD_RESPONSES:
                resp[str(c)] = {"$ref": f"#/components/responses/{STD_RESPONSES[c][0]}"}
        o["responses"] = resp
        paths.setdefault(op.path, {})[op.method] = o

    return {
        "openapi": "3.1.0",
        "info": {"title": f"{req.meta.product_name} API", "version": "1.0.0",
                 "description": req.meta.summary + "\n\nСгенерировано specforge-analysis из доменной модели и дизайна API."},
        "servers": [{"url": "http://localhost:8080", "description": "локальный запуск (docker compose)"}],
        "tags": [_tag(m, t) for t in tags],
        "security": [{"bearerAuth": []}],
        "paths": paths,
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}},
            "parameters": {
                "IdempotencyKey": {"name": "Idempotency-Key", "in": "header", "required": True,
                                   "description": "ключ идемпотентности; повтор с тем же ключом возвращает первый результат",
                                   "schema": {"type": "string", "format": "uuid"}},
                "Page": {"name": "page", "in": "query", "required": False,
                         "schema": {"type": "integer", "minimum": 1, "default": 1}},
                "PageSize": {"name": "pageSize", "in": "query", "required": False,
                             "schema": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}},
            },
            "responses": responses,
            "schemas": schemas,
        },
    }


# =========================================================================== AsyncAPI


def event_payload_fields(m: Model, ev) -> list[DField]:
    out: list[DField] = []
    if ev.entity:
        names = ev.fields or [f.name for f in m.fields(ev.entity)]
        out = [f for f in (m.field(ev.entity, n) for n in names) if f]
    return out + list(ev.extra)


def message_name(event_name: str) -> str:
    return "".join(pascal(p) for p in re.split(r"[._]", event_name))


def build_asyncapi(m: Model) -> dict | None:
    if not m.design.events:
        return None
    channels, operations, messages, schemas = {}, {}, {}, {}
    for ev in m.design.events:
        msg = message_name(ev.name)
        address = f"{ev.name}.v{ev.version}"
        data = object_schema(event_payload_fields(m, ev))
        for f in data["properties"].values():  # в событиях readOnly не нужен
            f.pop("readOnly", None)
        schemas[f"{msg}Payload"] = {
            "type": "object", "required": ["eventId", "type", "version", "occurredAt", "source", "key", "data"],
            "properties": {
                "eventId": {"type": "string", "format": "uuid", "description": "уникальный id события, ключ дедупликации"},
                "type": {"type": "string", "const": ev.name},
                "version": {"type": "integer", "const": ev.version},
                "occurredAt": {"type": "string", "format": "date-time"},
                "source": {"type": "string", "description": "компонент-источник"},
                "correlationId": {"type": "string", "description": "сквозной идентификатор запроса"},
                "key": {"type": "string", "description": f"ключ партиции: {ev.key}"},
                "data": data,
            },
        }
        messages[msg] = {"name": msg, "title": ev.name, "summary": ev.description,
                         "contentType": "application/json",
                         "payload": {"$ref": f"#/components/schemas/{msg}Payload"},
                         "x-requirements": ev.fr}
        channels[msg] = {"address": address, "description": ev.description,
                         "messages": {msg: {"$ref": f"#/components/messages/{msg}"}},
                         "x-dlq": f"{address}.dlq"}
        operations[f"publish{msg}"] = {
            "action": "send", "channel": {"$ref": f"#/channels/{msg}"},
            "messages": [{"$ref": f"#/channels/{msg}/messages/{msg}"}],
            "summary": f"{ev.producer} публикует {ev.name}",
            "x-producer": ev.producer, "x-consumers": ev.consumers,
        }
    return {
        "asyncapi": "3.0.0",
        "info": {"title": f"{m.req.meta.product_name}: события", "version": "1.0.0",
                 "description": ("Доставка at-least-once. Потребитель идемпотентен по eventId. "
                                 "Повторная обработка: 3 попытки с экспоненциальной задержкой (1, 5, 25 с), "
                                 "затем сообщение уходит в топик <address>.dlq. Несовместимое изменение схемы "
                                 "выпускается новой версией топика (.v2), старая версия поддерживается до миграции потребителей.")},
        "defaultContentType": "application/json",
        "servers": {"kafka": {"host": "kafka:9092", "protocol": "kafka", "description": "брокер в docker compose"}},
        "channels": channels,
        "operations": operations,
        "components": {"messages": messages, "schemas": {**schemas, **{k: v for k, v in GEOJSON.items()}}},
    }


# =========================================================================== DDL


def _sql_type(m: Model, entity: str, f: DField) -> str:
    t = f.type
    if t == "string":
        return f"varchar({f.max_length or 255})"
    return {
        "uuid": "uuid", "text": "text", "int": "integer", "bigint": "bigint", "decimal": "numeric(12,2)",
        "bool": "boolean", "date": "date", "datetime": "timestamptz", "json": "jsonb",
        "point": "geometry(Point, 4326)", "polygon": "geometry(Polygon, 4326)",
        "linestring": "geometry(LineString, 4326)",
    }.get(t) or m.enum_type(entity, f)


_SAFE_LITERAL = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _sql_default(f: DField) -> str | None:
    if f.default is None:
        return None
    v = f.default.strip()
    if f.type == "datetime" and v.lower() in ("now", "now()", "current_timestamp"):
        return "now()"
    if f.type == "bool" and v.lower() in ("true", "false"):
        return v.lower()
    if f.type in ("int", "bigint", "decimal") and re.fullmatch(r"-?\d+(\.\d+)?", v):
        return v
    if f.type in ("enum", "string") and _SAFE_LITERAL.match(v):
        return f"'{v}'"
    return None


def _q(s: str) -> str:
    return s.replace("'", "''")


def _order_tables(m: Model) -> list[DEntity]:
    deps = {e.name: {f.ref for f in e.fields if f.ref and f.ref in m.entities and f.ref != e.name}
            for e in m.domain.entities}
    done: list[str] = []
    pending = [e.name for e in m.domain.entities]
    while pending:
        progress = False
        for n in list(pending):
            if deps[n] <= set(done):
                done.append(n)
                pending.remove(n)
                progress = True
        if not progress:  # цикл зависимостей — внешние ключи всё равно добавятся через ALTER
            done += pending
            break
    return [m.entities[n] for n in done]


def build_platform_ddl(m: Model) -> str:
    """Служебные таблицы (outbox, идемпотентность) уже есть в каркасе backend — отдельный справочный файл."""
    return "-- Служебные таблицы каркаса backend (справочно; в schema.sql не входят)\n" + "\n".join(
        _platform_tables(m)) + "\n"


def _platform_tables(m: Model) -> list[str]:
    out: list[str] = []
    if m.design.events:
        out += ["-- Transactional outbox: события пишутся в одной транзакции с данными,",
                "-- фоновый публикатор отправляет их в Kafka (at-least-once).",
                "CREATE TABLE outbox_events (\n    id uuid PRIMARY KEY,\n    type varchar(100) NOT NULL,\n"
                "    version integer NOT NULL,\n    key varchar(100) NOT NULL,\n    payload jsonb NOT NULL,\n"
                "    occurred_at timestamptz NOT NULL DEFAULT now(),\n    published_at timestamptz\n);",
                "CREATE INDEX ix_outbox_events_unpublished ON outbox_events (occurred_at) WHERE published_at IS NULL;",
                ""]
    if m.design.events or any(op.idempotent for op in m.design.operations):
        out += ["-- Идемпотентность критичных операций (заголовок Idempotency-Key).",
                "CREATE TABLE idempotency_keys (\n    key uuid PRIMARY KEY,\n    operation varchar(100) NOT NULL,\n"
                "    response_status integer NOT NULL,\n    response_body jsonb,\n"
                "    created_at timestamptz NOT NULL DEFAULT now()\n);", ""]
    return out


# Зарезервированные слова PostgreSQL (reserved и «reserved, can be function or type»):
# такие имена таблиц и колонок без кавычек дают синтаксическую ошибку DDL.
PG_RESERVED = frozenset("""
all analyse analyze and any array as asc asymmetric authorization binary both case cast check collate collation
column concurrently constraint create cross current_catalog current_date current_role current_schema current_time
current_timestamp current_user default deferrable desc distinct do else end except false fetch for foreign freeze
from full grant group having ilike in initially inner intersect into is isnull join lateral leading left like limit
localtime localtimestamp natural not notnull null offset on only or order outer overlaps placing primary references
returning right select session_user similar some symmetric system_user table tablesample then to trailing true union
unique user using variadic verbose when where window with
""".split())


def qi(name: str) -> str:
    """Идентификатор SQL: зарезервированное слово берётся в двойные кавычки."""
    return f'"{name}"' if name in PG_RESERVED else name


def reserved_columns(m: Model) -> list[tuple[str, str]]:
    """(сущность, колонка) с зарезервированными именами — для предупреждения шлюза."""
    out = []
    for e in m.domain.entities:
        if table_name(e.name) in PG_RESERVED:
            out.append((e.name, table_name(e.name)))
        for f in m.fields(e.name):
            if snake(f.name) in PG_RESERVED:
                out.append((e.name, snake(f.name)))
    return out


def build_ddl(m: Model, platform: bool = False) -> str:
    out = ["-- Сгенерировано specforge-analysis из доменной модели. Не редактировать вручную:",
           "-- изменения вносятся в доменную модель, затем этап 2 перезапускается.",
           "-- Миграции EF Core (участник 3) строятся по этой схеме.", "",
           "CREATE EXTENSION IF NOT EXISTS postgis;", ""]
    for e in m.domain.entities:
        for f in e.fields:
            if f.type == "enum":
                vals = ", ".join(f"'{_q(v)}'" for v in f.enum_values)
                out.append(f"CREATE TYPE {qi(m.enum_type(e.name, f))} AS ENUM ({vals});")
    out.append("")
    alters: list[str] = []
    indexes: list[str] = []
    comments: list[str] = []
    ordered = _order_tables(m)
    created: set[str] = set()
    for e in ordered:
        tn = table_name(e.name)   # «сырое» имя — для имён ограничений и индексов
        qt = qi(tn)               # имя в SQL-выражениях
        cols = []
        for f in m.fields(e.name):
            col = snake(f.name)
            qc = qi(col)
            if f.name == "id":
                cols.append(f"    id uuid PRIMARY KEY DEFAULT gen_random_uuid()")
                continue
            sql_type = _sql_type(m, e.name, f)
            if f.type == "enum":
                sql_type = qi(sql_type)
            parts = [f"    {qc} {sql_type}"]
            if f.required:
                parts.append("NOT NULL")
            if f.name in ("createdAt", "updatedAt"):
                parts.append("DEFAULT now()")
            else:
                d = _sql_default(f)
                if d:
                    parts.append(f"DEFAULT {d}")
            if f.unique:
                parts.append("UNIQUE")
            if f.minimum is not None and f.type in ("int", "bigint", "decimal"):
                parts.append(f"CHECK ({qc} >= {f.minimum:g})")
            if f.maximum is not None and f.type in ("int", "bigint", "decimal"):
                parts.append(f"CHECK ({qc} <= {f.maximum:g})")
            if f.ref and f.ref in m.entities:
                ref_t = qi(table_name(f.ref))
                fk = f"REFERENCES {ref_t}(id) ON DELETE RESTRICT"
                if f.ref in created or f.ref == e.name:
                    parts.append(fk)
                else:
                    alters.append(f"ALTER TABLE {qt} ADD CONSTRAINT fk_{tn}_{col} FOREIGN KEY ({qc}) "
                                  f"REFERENCES {ref_t}(id) ON DELETE RESTRICT;")
                indexes.append(f"CREATE INDEX ix_{tn}_{col} ON {qt} ({qc});")
            if f.type in ("point", "polygon", "linestring"):
                indexes.append(f"CREATE INDEX ix_{tn}_{col}_gist ON {qt} USING GIST ({qc});")
            cols.append(" ".join(parts))
            if f.description:
                comments.append(f"COMMENT ON COLUMN {qt}.{qc} IS '{_q(f.description)}';")
        out.append(f"CREATE TABLE {qt} (\n" + ",\n".join(cols) + "\n);")
        comments.insert(0, f"COMMENT ON TABLE {qt} IS '{_q(e.description)}';")
        created.add(e.name)
        for idx in e.indexes:
            cols_ = [snake(c) for c in idx if m.field(e.name, c)]
            if cols_:
                indexes.append(f"CREATE INDEX ix_{tn}_{'_'.join(cols_)} ON {qt} ({', '.join(qi(c) for c in cols_)});")
        out.append("")
    if platform:
        out += _platform_tables(m)
    out += alters + ([""] if alters else []) + sorted(set(indexes)) + [""] + comments
    return "\n".join(out) + "\n"
