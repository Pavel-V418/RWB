from pathlib import Path

import pytest

from specforge.llm import DEFAULT_PROFILES, LLMConfigError, Profile, ProfileName, load_profiles

ROOT = Path(__file__).resolve().parent.parent


def test_defaults() -> None:
    expected = {
        ProfileName.ANALYSIS: (True, 0.6),
        ProfileName.REVIEW: (True, 0.6),
        ProfileName.CODE: (False, 0.2),
        ProfileName.REPAIR: (False, 0.1),
    }
    assert {n: (p.thinking, p.temperature) for n, p in DEFAULT_PROFILES.items()} == expected
    limits = {n: (p.top_p, p.max_tokens) for n, p in DEFAULT_PROFILES.items()}
    assert limits == {
        "ANALYSIS": (0.95, 16384),
        "REVIEW": (0.95, 8192),
        "CODE": (0.8, 8192),
        "REPAIR": (0.8, 4096),
    }
    assert DEFAULT_PROFILES["REPAIR"].timeout_s == 4096 / 25 + 60


def test_timeout_from_min_tokens_per_s(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MIN_TOKENS_PER_S", "50")
    assert Profile(name="X", max_tokens=1000).timeout_s == 1000 / 50 + 60
    assert Profile(name="X", max_tokens=1000, timeout_s=5).timeout_s == 5
    assert Profile(name="X").timeout_s is None  # без max_tokens — таймаут клиента


def test_thinking_profile_low_temperature_raised() -> None:
    with pytest.warns(UserWarning, match="профиль X"):
        profile = Profile(name="X", thinking=True, temperature=0.1)
    assert profile.temperature == 0.6


def test_override_max_tokens_recomputes_timeout(tmp_path: Path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text("profiles:\n  CODE: {max_tokens: 2500, presence_penalty: 0.5}\n", "utf-8")
    code = load_profiles(path)["CODE"]
    assert (code.max_tokens, code.timeout_s, code.presence_penalty) == (2500, 2500 / 25 + 60, 0.5)


def test_relative_path_is_resolved_from_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # в cwd свой config/profiles.yaml — он должен игнорироваться
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "profiles.yaml").write_text("profiles:\n  CODE: {temperature: 1.5}\n")
    profiles = load_profiles(Path("config/profiles.yaml"))
    assert profiles["CODE"].temperature == 0.2  # взят файл из корня проекта, а не из cwd
    assert profiles == load_profiles(ROOT / "config" / "profiles.yaml")


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
