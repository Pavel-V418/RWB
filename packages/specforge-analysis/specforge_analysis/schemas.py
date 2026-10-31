"""Схемы данных этапа 1.

Две группы моделей:
* X*/A*/C* — то, что возвращает LLM (без идентификаторов, связи по именам и FR-ID);
* Requirements и её элементы — итоговый requirements.json со стабильными ID,
  происхождением (origin) и ссылкой на строку исходного текста.

ID присваивает код, а не модель: так они стабильны между прогонами.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Origin = Literal["customer", "derived", "assumption", "clarification"]
Priority = Literal["must", "should", "could"]
NfrCategory = Literal[
    "performance", "reliability", "availability", "security", "privacy", "usability",
    "compatibility", "maintainability", "observability", "scalability", "other",
]


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


# =========================================================================== ответы LLM: извлечение


class XGoal(_M):
    statement: str
    metric: str | None = None
    source_quote: str | None = None


class XRole(_M):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,30}$")
    name: str
    description: str
    origin: Origin = "customer"
    source_quote: str | None = None


class XAttribute(_M):
    name: str
    type: str
    description: str = ""
    required: bool = True


class XEntity(_M):
    name: str = Field(pattern=r"^[A-Z][A-Za-z0-9]{1,40}$")
    description: str
    attributes: list[XAttribute] = []
    origin: Origin = "customer"
    source_quote: str | None = None


class XFunctional(_M):
    title: str
    statement: str
    priority: Priority = "must"
    actors: list[str] = []      # ключи ролей
    entities: list[str] = []    # имена сущностей
    origin: Origin = "customer"
    source_quote: str | None = None


class XNonFunctional(_M):
    category: NfrCategory
    statement: str
    metric: str | None = None
    origin: Origin = "customer"
    source_quote: str | None = None


class XConstraint(_M):
    kind: Literal["technology", "business", "regulatory", "data", "integration", "scope"]
    statement: str
    origin: Origin = "customer"
    source_quote: str | None = None


class XIntegration(_M):
    name: str
    purpose: str
    direction: Literal["inbound", "outbound", "bidirectional"]
    mode: Literal["rest", "events", "deeplink", "file", "sdk", "device", "other"]
    availability: Literal["real", "mock_required", "out_of_scope"]
    origin: Origin = "customer"
    source_quote: str | None = None


class Profile(_M):
    """Профиль кейса: какие ветки конвейера включать."""

    needs_web_ui: bool
    needs_admin_ui: bool
    needs_mobile_app: bool
    needs_geo: bool
    needs_async_events: bool
    needs_realtime_updates: bool
    needs_scheduled_jobs: bool
    needs_file_storage: bool
    needs_user_notifications: bool
    external_integrations: list[str] = []
    notes: list[str] = []


class XGlossary(_M):
    term: str
    definition: str


class Extraction(_M):
    product_name: str
    summary: str
    goals: list[XGoal]
    roles: list[XRole]
    functional: list[XFunctional]
    non_functional: list[XNonFunctional] = []
    constraints: list[XConstraint] = []
    entities: list[XEntity] = []
    integrations: list[XIntegration] = []
    glossary: list[XGlossary] = []
    profile: Profile


# =========================================================================== ответы LLM: анализ


class AAcceptance(_M):
    fr_id: str
    kind: Literal["positive", "negative", "edge"] = "positive"
    title: str
    given: list[str]
    when: list[str]
    then: list[str]


class AScenario(_M):
    title: str
    actor: str
    goal: str
    preconditions: list[str] = []
    main_flow: list[str]
    alternative_flows: list[str] = []
    error_flows: list[str] = []
    postconditions: list[str] = []
    expected_result: str = ""
    requirements: list[str]  # FR-ID


class AStory(_M):
    fr_id: str
    as_a: str      # ключ роли
    i_want: str
    so_that: str


class ADerivedNfr(_M):
    category: NfrCategory
    statement: str
    metric: str | None = None
    rationale: str


class ASecurity(_M):
    category: Literal["authn", "authz", "data_protection", "privacy", "audit",
                      "input_validation", "rate_limiting", "secrets", "other"]
    statement: str
    rationale: str
    related: list[str] = []  # FR-ID


class AAmbiguity(_M):
    topic: str
    description: str
    impact: Literal["architecture", "security", "core_scenario", "data", "ux", "other"]
    severity: Literal["high", "medium", "low"]
    affected: list[str] = []
    options: list[str] = Field(min_length=2, max_length=4)
    default_option: str
    default_is_safe: bool  # умолчание легко поменять позже, не ломая архитектуру


class AAssumption(_M):
    statement: str
    reason: str
    affected: list[str] = []


class ARisk(_M):
    statement: str
    probability: Literal["high", "medium", "low"]
    impact: Literal["high", "medium", "low"]
    mitigation: str


class Analysis(_M):
    scenarios: list[AScenario]
    acceptance: list[AAcceptance]
    stories: list[AStory]
    derived_nfr: list[ADerivedNfr] = []
    security: list[ASecurity] = []
    ambiguities: list[AAmbiguity] = []
    assumptions: list[AAssumption] = []
    risks: list[ARisk] = []


# =========================================================================== ответы LLM: критик и ремонт


class CFinding(_M):
    severity: Literal["blocker", "major", "minor"]
    category: Literal["contradiction", "untestable", "missing_flow", "missing_error", "incomplete",
                      "ambiguous", "inconsistent_terms", "other"]
    target: str  # ID элемента
    issue: str
    fix: str


class Critique(_M):
    findings: list[CFinding] = []


class AcceptancePatch(_M):
    """Ремонт: новые/исправленные критерии приёмки для перечисленных FR (заменяют прежние)."""

    acceptance: list[AAcceptance]


# =========================================================================== итоговая модель (requirements.json)


class Source(_M):
    quote: str | None = None
    line: int | None = None
    exact: bool | None = None


class Item(_M):
    id: str
    origin: Origin = "customer"
    source: Source | None = None


class Goal(Item):
    statement: str
    metric: str | None = None


class Role(Item):
    key: str
    name: str
    description: str


class Attribute(_M):
    name: str
    type: str
    description: str = ""
    required: bool = True


class Entity(Item):
    name: str
    description: str
    attributes: list[Attribute] = []


class Functional(Item):
    title: str
    statement: str
    priority: Priority = "must"
    actors: list[str] = []      # ROLE-ID
    entities: list[str] = []    # ENT-ID
    acceptance: list[str] = []  # AC-ID
    scenarios: list[str] = []   # UC-ID
    stories: list[str] = []     # US-ID


class NonFunctional(Item):
    category: NfrCategory
    statement: str
    metric: str | None = None
    rationale: str | None = None


class Constraint(Item):
    kind: str
    statement: str


class Integration(Item):
    name: str
    purpose: str
    direction: str
    mode: str
    availability: str


class Security(Item):
    category: str
    statement: str
    rationale: str
    related: list[str] = []


class Scenario(Item):
    title: str
    actor: str  # ROLE-ID
    goal: str
    preconditions: list[str] = []
    main_flow: list[str]
    alternative_flows: list[str] = []
    error_flows: list[str] = []
    postconditions: list[str] = []
    expected_result: str = ""
    requirements: list[str] = []


class BusinessReq(Item):
    """Пункт бизнес-требований: строка исходного текста, на которую ссылаются требования заказчика."""

    line: int
    text: str
    fr: list[str] = []
    related: list[str] = []      # прочие элементы (ограничения, интеграции, роли…)
    fr_candidates: list[str] = []  # подобраны кодом по совпадению слов, не моделью: требуют проверки


class Acceptance(Item):
    fr_id: str
    kind: str
    title: str
    given: list[str]
    when: list[str]
    then: list[str]


class Story(Item):
    fr_id: str
    role: str  # ROLE-ID
    i_want: str
    so_that: str


class Assumption(Item):
    statement: str
    reason: str
    affected: list[str] = []
    from_question: str | None = None


class Question(Item):
    topic: str
    question: str
    impact: str
    severity: str
    affected: list[str] = []
    options: list[str]
    default_option: str
    answer: str | None = None
    status: Literal["open", "answered", "defaulted"] = "open"


class Risk(Item):
    statement: str
    probability: str
    impact: str
    mitigation: str


class GlossaryTerm(_M):
    term: str
    definition: str


class Meta(_M):
    product_name: str
    summary: str
    source_file: str
    pipeline: str = "specforge-analysis"
    version: str = "0.1.0"
    mode: str = "auto"


class Requirements(_M):
    meta: Meta
    profile: Profile
    business: list[BusinessReq] = []
    goals: list[Goal] = []
    roles: list[Role] = []
    functional: list[Functional] = []
    non_functional: list[NonFunctional] = []
    constraints: list[Constraint] = []
    entities: list[Entity] = []
    integrations: list[Integration] = []
    security: list[Security] = []
    scenarios: list[Scenario] = []
    acceptance: list[Acceptance] = []
    stories: list[Story] = []
    assumptions: list[Assumption] = []
    questions: list[Question] = []
    risks: list[Risk] = []
    glossary: list[GlossaryTerm] = []

    def index(self) -> dict[str, Item]:
        out: dict[str, Item] = {}
        for name in ("business", "goals", "roles", "functional", "non_functional", "constraints", "entities",
                     "integrations", "security", "scenarios", "acceptance", "stories",
                     "assumptions", "questions", "risks"):
            for it in getattr(self, name):
                out[it.id] = it
        return out
