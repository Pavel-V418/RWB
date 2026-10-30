from pathlib import Path

import pytest

from specforge.llm import DEFAULT_PROFILES, LLMConfigError, ProfileName, load_profiles

ROOT = Path(__file__).resolve().parent.parent


def test_defaults() -> None:
    expected = {
        ProfileName.ANALYSIS: (True, 0.6),
        ProfileName.REVIEW: (True, 0.3),
        ProfileName.CODE: (False, 0.2),
        ProfileName.REPAIR: (False, 0.1),
    }
    assert {n: (p.thinking, p.temperature) for n, p in DEFAULT_PROFILES.items()} == expected


def test_repo_profiles_yaml_is_valid() -> None:
    profiles = load_profiles(ROOT / "config" / "profiles.yaml")
    assert set(profiles) >= set(ProfileName)


def test_yaml_overrides_fields_and_adds_profiles(tmp_path: Path) -> None:
    path = tmp_path / "profiles.yaml"
    path.write_text(
        "profiles:\n  CODE: {temperature: 0.0}\n  DRAFT: {thinking: true, max_tokens: 512}\n",
        "utf-8",
    )
    profiles = load_profiles(path)
    assert profiles["CODE"].temperature == 0.0
    assert profiles["CODE"].top_p == DEFAULT_PROFILES["CODE"].top_p  # остальное — по умолчанию
    assert profiles["DRAFT"].thinking
    assert profiles["DRAFT"].max_tokens == 512
    assert profiles["ANALYSIS"] == DEFAULT_PROFILES["ANALYSIS"]


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert load_profiles(tmp_path / "nope.yaml") == DEFAULT_PROFILES
    assert load_profiles(None) == DEFAULT_PROFILES


@pytest.mark.parametrize(
    "text",
    [
        "profiles:\n  CODE: {temperature: 5}\n",
        "profiles:\n  CODE: {tempreature: 0.1}\n",
        "profiles: [1, 2]\n",
        "profiles: {CODE: [\n",
    ],
)
def test_invalid_yaml_rejected(tmp_path: Path, text: str) -> None:
    path = tmp_path / "profiles.yaml"
    path.write_text(text, "utf-8")
    with pytest.raises(LLMConfigError):
        load_profiles(path)
