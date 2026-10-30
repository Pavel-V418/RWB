"""Профили генерации: режим размышлений, сэмплинг, лимит ответа и таймаут под тип задачи.

Значения по умолчанию — ниже; config/profiles.yaml переопределяет их поле за полем и может
добавлять новые профили. top_p/top_k — рекомендации для Qwen3 (сверить с model card).

timeout_s по умолчанию = max_tokens / LLM_MIN_TOKENS_PER_S + 60: запрос не должен обрываться,
пока модель генерирует ответ максимальной длины с минимально приемлемой скоростью.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from specforge.core.contracts import ProfileName, safe_thinking_temperature
from specforge.llm.types import LLMConfigError
from specforge.paths import PROJECT_ROOT

TIMEOUT_MARGIN_S = 60.0


def min_tokens_per_s() -> float:
    return float(os.environ.get("LLM_MIN_TOKENS_PER_S", "25"))


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    thinking: bool = False
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=-1)
    """vLLM-специфичный параметр, уходит в extra_body."""
    max_tokens: int | None = Field(default=None, ge=1)
    presence_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    timeout_s: float | None = Field(default=None, gt=0)
    """Таймаут запроса; не задан — max_tokens / LLM_MIN_TOKENS_PER_S + 60."""

    @model_validator(mode="before")
    @classmethod
    def _defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if data.get("thinking") is True and isinstance(data.get("temperature"), int | float):
            data["temperature"] = safe_thinking_temperature(
                data["temperature"], f"профиль {data.get('name')}"
            )
        if data.get("timeout_s") is None and isinstance(data.get("max_tokens"), int):
            data["timeout_s"] = data["max_tokens"] / min_tokens_per_s() + TIMEOUT_MARGIN_S
        return data


DEFAULT_PROFILES: dict[str, Profile] = {
    p.name: p
    for p in (
        Profile(
            name=ProfileName.ANALYSIS,
            thinking=True,
            temperature=0.6,
            top_p=0.95,
            top_k=20,
            max_tokens=16384,
        ),
        Profile(
            name=ProfileName.REVIEW,
            thinking=True,
            temperature=0.6,
            top_p=0.95,
            top_k=20,
            max_tokens=8192,
        ),
        Profile(
            name=ProfileName.CODE,
            thinking=False,
            temperature=0.2,
            top_p=0.8,
            top_k=20,
            max_tokens=8192,
        ),
        Profile(
            name=ProfileName.REPAIR,
            thinking=False,
            temperature=0.1,
            top_p=0.8,
            top_k=20,
            max_tokens=4096,
        ),
    )
}


def merge_profiles(
    base: Mapping[str, Profile], overrides: Mapping[str, Mapping[str, Any]]
) -> dict[str, Profile]:
    merged = dict(base)
    for name, fields in overrides.items():
        current = merged.get(name)
        base = current.model_dump() if current else {}
        if "max_tokens" in fields and "timeout_s" not in fields:
            base.pop("timeout_s", None)  # таймаут пересчитается от нового max_tokens
        data = {**base, **fields, "name": name}
        try:
            merged[name] = Profile.model_validate(data)
        except ValidationError as exc:
            raise LLMConfigError(f"профиль {name}: {exc}") from exc
    return merged


def load_profiles(path: Path | None) -> dict[str, Profile]:
    """Профили по умолчанию с переопределениями из YAML (если файл есть).

    Относительный путь считается от корня проекта, а не от текущего каталога.
    Формат файла:
        profiles:
          ANALYSIS: {temperature: 0.5}
          MY_PROFILE: {thinking: false, temperature: 0.0}
    """
    if path is not None and not path.is_absolute():
        path = PROJECT_ROOT / path
    if path is None or not path.is_file():
        return dict(DEFAULT_PROFILES)
    try:
        data = yaml.safe_load(path.read_text("utf-8")) or {}
    except yaml.YAMLError as exc:
        raise LLMConfigError(f"{path}: невалидный YAML: {exc}") from exc
    overrides = data.get("profiles") if isinstance(data, dict) else None
    if overrides is None:
        return dict(DEFAULT_PROFILES)
    if not isinstance(overrides, dict) or not all(isinstance(v, dict) for v in overrides.values()):
        raise LLMConfigError(f"{path}: ожидается profiles: {{ИМЯ: {{поле: значение}}}}")
    return merge_profiles(DEFAULT_PROFILES, overrides)


# --- thinking по агентам ------------------------------------------------------------------------


class AgentLLMSettings(BaseModel):
    """Разделы agents / repair в config/profiles.yaml: thinking по имени агента и для ремонта.

    Перекрывают AgentSpec.thinking и профиль без правки кода агентов; бюджет-контроллер при
    расходе > 70% всё равно выключает thinking (LLMClient.without_thinking).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    agents: dict[str, bool] = Field(default_factory=dict)
    repair_thinking: bool | None = None

    def thinking_for(self, agent: str) -> bool | None:
        return self.agents.get(agent)


def load_agent_settings(path: Path | None) -> AgentLLMSettings:
    """Формат:
    agents:
      analyst: {thinking: true}
    repair: {thinking: false}
    """
    if path is not None and not path.is_absolute():
        path = PROJECT_ROOT / path
    if path is None or not path.is_file():
        return AgentLLMSettings()
    try:
        data = yaml.safe_load(path.read_text("utf-8")) or {}
    except yaml.YAMLError as exc:
        raise LLMConfigError(f"{path}: невалидный YAML: {exc}") from exc
    agents_raw = data.get("agents") or {}
    repair_raw = data.get("repair") or {}
    if not isinstance(agents_raw, dict) or not isinstance(repair_raw, dict):
        raise LLMConfigError(f"{path}: ожидается agents: {{имя: {{thinking: bool}}}}")
    agents: dict[str, bool] = {}
    for name, value in agents_raw.items():
        thinking = value.get("thinking") if isinstance(value, dict) else None
        if not isinstance(thinking, bool):
            raise LLMConfigError(f"{path}: agents.{name}.thinking должен быть true или false")
        agents[str(name)] = thinking
    repair = repair_raw.get("thinking")
    if repair is not None and not isinstance(repair, bool):
        raise LLMConfigError(f"{path}: repair.thinking должен быть true или false")
    return AgentLLMSettings(agents=agents, repair_thinking=repair)
