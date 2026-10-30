"""Инфраструктура без Docker и сети: структура compose и логика smoke_llm на FakeLLM."""

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from specforge.core.contracts import Usage
from specforge.llm import FakeLLM, FakeReply, FakeToolCall, LLMClient, LLMTransientError

ROOT = Path(__file__).resolve().parent.parent
INFRA = ROOT / "infra"


def _load_smoke() -> ModuleType:
    spec = importlib.util.spec_from_file_location("smoke_llm", INFRA / "smoke_llm.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = _load_smoke()


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    data = yaml.safe_load((INFRA / "docker-compose.platform.yml").read_text("utf-8"))
    assert isinstance(data, dict)
    return data


def test_every_service_has_healthcheck(compose: dict[str, Any]) -> None:
    services = compose["services"]
    assert {"vllm", "postgres", "langfuse-web", "langfuse-worker"} <= set(services)
    missing = [name for name, svc in services.items() if "test" not in svc.get("healthcheck", {})]
    assert not missing


def test_vllm_service_settings(compose: dict[str, Any]) -> None:
    vllm = compose["services"]["vllm"]
    assert vllm["image"].startswith("vllm/vllm-openai")
    env = vllm["environment"]
    for key in ("TENSOR_PARALLEL", "DTYPE", "MAX_MODEL_LEN", "GPU_MEM_UTIL"):
        assert key in env
    assert "131072" in env["MAX_MODEL_LEN"]
    assert "Qwen/Qwen3.8-27B" in env["VLLM_MODEL_ID"]
    script = (INFRA / "vllm" / "entrypoint.sh").read_text("utf-8")
    for flag in (
        "--enable-prefix-caching",
        "--enable-auto-tool-choice",
        "--enable-prompt-tokens-details",
        '--tool-call-parser "${TOOL_CALL_PARSER',
        '--reasoning-parser "${REASONING_PARSER',
    ):
        assert flag in script
    assert "\r\n" not in script


def test_compose_env_vars_documented(compose: dict[str, Any]) -> None:
    text = (INFRA / "docker-compose.platform.yml").read_text("utf-8")
    used = set(re.findall(r"\$\{([A-Z0-9_]+)(?::-[^}]*)?\}", text))
    example = (ROOT / ".env.example").read_text("utf-8")
    documented = set(re.findall(r"^([A-Z0-9_]+)=", example, re.MULTILINE))
    assert used - documented == set()


def _smoke_script() -> list[Any]:
    return [
        "Париж",
        FakeReply(tool_calls=[FakeToolCall("add", {"a": 17, "b": 25})]),
        FakeReply(structured={"expression": "6*7", "result": 42}),
        FakeReply(content="156", reasoning="12*13=156", usage=Usage(prompt_tokens=5, calls=1)),
        "156",
    ]


def test_smoke_checks_pass_on_good_endpoint() -> None:
    llm = FakeLLM(_smoke_script())
    results = smoke.run_checks(llm)
    assert [r.name for r in results] == [
        "chat",
        "tool_call",
        "json_schema",
        "thinking_on",
        "thinking_off",
    ]
    assert all(r.ok for r in results), smoke.format_table(results)
    assert [c.thinking for c in llm.calls[-2:]] == [True, False]
    assert "thinking_on" in smoke.format_table(results)


def test_smoke_checks_report_failures() -> None:
    script = _smoke_script()
    script[1] = "не буду вызывать инструмент"
    script[4] = FakeReply(content="156", reasoning="а я всё равно думаю")
    llm: LLMClient = FakeLLM([*script[:2], LLMTransientError("x"), *script[2:]])
    results = {r.name: r.ok for r in smoke.run_checks(llm)}
    assert results == {
        "chat": True,
        "tool_call": False,
        "json_schema": True,
        "thinking_on": True,
        "thinking_off": False,
    }


def test_smoke_skips_without_base_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)  # без .env
    assert smoke.main() == 0
    assert "SKIP" in capsys.readouterr().out


@pytest.mark.skipif(not os.environ.get("LLM_BASE_URL"), reason="LLM_BASE_URL не задан")
def test_smoke_live_endpoint() -> None:
    assert smoke.main() == 0


def test_smoke_runs_as_script(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LLM_")}
    proc = subprocess.run(
        [sys.executable, str(INFRA / "smoke_llm.py")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "SKIP" in proc.stdout
