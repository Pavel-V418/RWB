"""Схемы этапа 2 (системный анализ).

LLM проектирует три вещи: доменную модель, дизайн API и событий, пояснительный текст SA-документа.
Всё остальное — OpenAPI, AsyncAPI, DDL, диаграммы, таблицы — рендерит код из этих данных,
поэтому контракты, БД и документация не могут разойтись.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FieldType = Literal["uuid", "string", "text", "int", "bigint", "decimal", "bool", "date", "datetime",
                    "enum", "json", "point", "polygon", "linestring"]
CAMEL = r"^[a-z][A-Za-z0-9]{0,62}$"


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


# =========================================================================== доменная модель


class DField(_M):
    name: str = Field(pattern=CAMEL)
    type: FieldType
    required: bool = True
    description: str = ""
    title: str = ""                     # подпись поля в интерфейсе (на языке ТЗ)
    enum_labels: dict[str, str] = {}    # значение enum -> подпись в интерфейсе
    max_length: int | None = None
    pattern: str | None = None          # регулярное выражение (ECMA/PCRE-совместимое подмножество)
    minimum: float | None = None
    maximum: float | None = None
    enum_values: list[str] = []
    default: str | None = None
    unique: bool = False
    ref: str | None = None              # имя сущности для внешнего ключа
    pii: bool = False                   # персональные данные
    read_only: bool = False             # вычисляется сервером, в запросах не принимается
    internal: bool = False              # только в БД, в API и событиях не появляется (хэш пароля и т. п.)
    example: str | None = None          # пример значения для документации (обязателен для полей с pattern)


class DEntity(_M):
    name: str = Field(pattern=r"^[A-Z][A-Za-z0-9]{1,40}$")
    description: str
    fields: list[DField]
    audit: bool = True                  # createdAt/updatedAt добавляет код
    indexes: list[list[str]] = []
    fr: list[str] = []


class DTransition(_M):
    from_state: str
    to_state: str
    trigger: str


class DStateMachine(_M):
    entity: str
    field: str
    states: list[str]
    transitions: list[DTransition]


class DomainModel(_M):
    entities: list[DEntity]
    state_machines: list[DStateMachine] = []
    notes: list[str] = []


# =========================================================================== API и события


class ParamDef(_M):
    name: str = Field(pattern=CAMEL)
    type: FieldType = "string"
    required: bool = False
    description: str = ""
    enum_values: list[str] = []


class RequestDef(_M):
    entity: str | None = None           # поля берутся из сущности по именам
    fields: list[str] = []
    extra: list[DField] = []            # поля, которых нет в сущности (например, durationMinutes)


class ResponseDef(_M):
    kind: Literal["object", "list", "page", "none"]
    entity: str | None = None
    fields: list[str] = []              # пусто = все поля сущности
    extra: list[DField] = []


class Operation(_M):
    operation_id: str = Field(pattern=CAMEL)
    method: Literal["get", "post", "put", "patch", "delete"]
    path: str
    summary: str
    tag: str
    roles: list[str]                    # ключи ролей; "system" — сервис-сервис
    fr: list[str]
    query: list[ParamDef] = []
    request: RequestDef | None = None
    response: ResponseDef
    success_status: int = 200
    errors: list[int] = []
    idempotent: bool = False            # требует Idempotency-Key
    rate_limited: bool = False
    # шаблон API организаторов: алгоритм, исключения, таблицы, внешние вызовы, кэширование
    algorithm: list[str] = []           # основной сценарий по шагам
    exceptions: list[str] = []          # сценарий исключений: условие -> код ответа
    tables: list[str] = []              # сущности, которые метод читает или меняет
    external: list[str] = []            # внешние компоненты, которые вызывает метод
    caching: str | None = None


class EventDef(_M):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")  # домен.объект.действие, напр. orders.order.created
    version: int = 1
    description: str
    producer: str
    consumers: list[str]
    key: str                            # поле ключа партиции
    entity: str | None = None
    fields: list[str] = []
    extra: list[DField] = []
    fr: list[str]


class Component(_M):
    name: str
    kind: Literal["mobile", "web", "backend", "worker", "database", "broker", "mock", "external"]
    tech: str
    responsibility: str


class SeqStep(_M):
    source: str
    target: str
    message: str
    reply: bool = False


class Sequence(_M):
    title: str
    scenario: str | None = None
    steps: list[SeqStep]


class NoApi(_M):
    fr: str
    reason: str


class ApiDesign(_M):
    components: list[Component]
    operations: list[Operation]
    events: list[EventDef] = []
    sequences: list[Sequence] = []
    no_api: list[NoApi] = []


class ApiPatch(_M):
    """Ремонт: недостающие операции/события для FR без покрытия."""

    operations: list[Operation] = []
    events: list[EventDef] = []
    no_api: list[NoApi] = []


class DesignPatch(_M):
    """Ремонт ошибок шлюза: исправленные объекты целиком. Операция заменяется по operation_id,
    сущность — по name, жизненный цикл — по (entity, field); новые объекты добавляются."""

    entities: list[DEntity] = []
    state_machines: list[DStateMachine] = []
    operations: list[Operation] = []
    remove_operations: list[str] = []   # operation_id дублей, которые нужно удалить


# =========================================================================== текст SA-документа


class BusinessError(_M):
    code: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    status: int
    title: str
    when: str


class Metric(_M):
    name: str
    description: str
    source: str = ""                    # откуда берётся значение


class PerfItem(_M):
    kind: Literal["ui", "background", "external"]
    operation: str
    load: str                           # нагрузка в час пик
    response_time: str                  # время отклика / обработки


class Maintenance(_M):
    backup: list[str] = []
    archiving: list[str] = []
    access: list[str] = []
    logging: list[str] = []
    monitoring: list[str] = []
    audit: list[str] = []
    remote_access: list[str] = []


class SaProse(_M):
    purpose: str
    scope_in: list[str]
    scope_out: list[str]
    architecture_notes: list[str]
    business_errors: list[BusinessError]
    integration_notes: list[str] = []
    interfaces: list[str] = []          # 3.2.1 интерфейсы передачи информации
    metrics: list[Metric] = []          # 3.4 метрики
    performance: list[PerfItem] = []    # 4.2 производительность
    scalability: list[str] = []         # 4.3
    reliability: list[str] = []         # 4.4
    maintenance: Maintenance = Maintenance()   # 4.5
