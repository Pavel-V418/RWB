"""Профили генерации: режим размышлений и параметры сэмплинга под тип задачи.

Значения по умолчанию — ниже; config/profiles.yaml переопределяет их поле за полем и может
добавлять новые профили. top_p/top_k взяты из рекомендаций для Qwen3 (сверить с model card).
"""

from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from specforge.llm.types import LLMConfigError


class ProfileName(StrEnum):
    ANALYSIS = "ANALYSIS"
    REVIEW = "REVIEW"
    CODE = "CODE"
    REPAIR = "REPAIR"


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    thinking: bool = False
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=-1)
    """vLLM-специфичный параметр, уходит в extra_body."""
    max_tokens: int | None = Field(default=None, ge=1)


DEFAULT_PROFILES: dict[str, Profile] = {
    p.name: p
    for p in (
        Profile(name=ProfileName.ANALYSIS, thinking=True, temperature=0.6, top_p=0.95, top_k=20),
        Profile(name=ProfileName.REVIEW, thinking=True, temperature=0.3, top_p=0.95, top_k=20),
        Profile(name=ProfileName.CODE, thinking=False, temperature=0.2, top_p=0.8, top_k=20),
        Profile(name=ProfileName.REPAIR, thinking=False, temperature=0.1, top_p=0.8, top_k=20),
    )
}


def merge_profiles(
    base: Mapping[str, Profile], overrides: Mapping[str, Mapping[str, Any]]
) -> dict[str, Profile]:
    merged = dict(base)
    for name, fields in overrides.items():
        current = merged.get(name)
        data = {**(current.model_dump() if current else {}), **fields, "name": name}
        try:
            merged[name] = Profile.model_validate(data)
        except ValidationError as exc:
            raise LLMConfigError(f"профиль {name}: {exc}") from exc
    return merged


def load_profiles(path: Path | None) -> dict[str, Profile]:
    """Профили по умолчанию с переопределениями из YAML (если файл есть).

    Формат файла:
        profiles:
          ANALYSIS: {temperature: 0.5}
          MY_PROFILE: {thinking: false, temperature: 0.0}
    """
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
