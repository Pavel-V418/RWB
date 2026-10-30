"""Схема ответа агента-техписателя: только тексты, которые нельзя вывести из артефактов кодом."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Capability(_M):
    title: str
    description: str
    fr: list[str] = []


class ProductText(_M):
    tagline: str
    problem: str
    audience: list[str]
    value: list[str]
    capabilities: list[Capability]


class AdrOption(_M):
    name: str
    pros: list[str]
    cons: list[str]


class Adr(_M):
    """Шаблон ADR организаторов: контекст, статус, альтернативы с плюсами и минусами,
    принятое решение, последствия, ответственные."""

    title: str
    status: Literal["ПРЕДЛОЖЕНО", "ПРИНЯТО", "ЗАМЕНЕНО"] = "ПРЕДЛОЖЕНО"
    context: str
    alternatives: list[AdrOption] = Field(min_length=2)
    decision: str
    consequences: list[str]
    responsible: list[str] = []
    related: list[str] = []          # FR, Q, ASM, NFR


class GuideTask(_M):
    title: str
    steps: list[str]
    result: str
    related: list[str] = []          # FR или UC


class Troubleshoot(_M):
    problem: str
    solution: str


class RoleGuide(_M):
    role: str                         # ключ роли
    intro: str
    tasks: list[GuideTask]
    troubleshooting: list[Troubleshoot] = []


class DocsText(_M):
    product: ProductText
    adrs: list[Adr] = Field(min_length=1)
    user_guide: list[RoleGuide]
