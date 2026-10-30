import pytest
from pydantic import ValidationError

from specforge.core.contracts import (
    AgentSpec,
    CaseProfile,
    StageResult,
    StageStatus,
    Usage,
    add_usage,
    merge_stage_results,
)


def test_usage_add() -> None:
    a = Usage(prompt_tokens=10, completion_tokens=5, cached_tokens=2, calls=1)
    b = Usage(prompt_tokens=1, completion_tokens=1, cached_tokens=0, calls=1)
    total = a.add(b)
    assert total == Usage(prompt_tokens=11, completion_tokens=6, cached_tokens=2, calls=2)
    assert total.total_tokens == 17
    assert a.calls == 1  # add не мутирует


def test_agent_spec_defaults_and_validation() -> None:
    spec = AgentSpec(name="backend_api", stage="backend", kind="tool")
    assert spec.max_repairs == 2
    assert spec.tools == []
    with pytest.raises(ValidationError):
        AgentSpec(name="Bad-Name", stage="x", kind="tool")
    with pytest.raises(ValidationError):
        AgentSpec(name="x", stage="x", kind="tool", unknown=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        spec.name = "other"  # type: ignore[misc]


def test_stage_result_json_roundtrip() -> None:
    result = StageResult(agent="a", status=StageStatus.degraded, usage=Usage(calls=3))
    assert StageResult.model_validate_json(result.model_dump_json()) == result
    assert result.model_dump(mode="json")["status"] == "degraded"


def test_case_profile_flags() -> None:
    profile = CaseProfile(has_web=True, extra={"has_payments": True})
    assert profile.is_enabled("has_web")
    assert not profile.is_enabled("has_mobile")
    assert profile.is_enabled("has_payments")
    assert not profile.is_enabled("unknown_flag")
    assert not profile.is_enabled("extra")


def test_reducers() -> None:
    old = {"a": StageResult(agent="a", status=StageStatus.failed)}
    new = {
        "a": StageResult(agent="a", status=StageStatus.passed),
        "b": StageResult(agent="b", status=StageStatus.skipped),
    }
    merged = merge_stage_results(old, new)
    assert merged["a"].status == StageStatus.passed
    assert set(merged) == {"a", "b"}
    assert add_usage(Usage(calls=1), Usage(calls=2)).calls == 3
