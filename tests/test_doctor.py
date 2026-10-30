"""specforge doctor на подделках: vLLM (FakeLLM), инфраструктура, агенты, граф, CLI."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import typer
from typer.testing import CliRunner

from specforge.doctor import (
    Check,
    DoctorDeps,
    check_graph,
    check_llm,
    gate_programs,
    register_cli,
    run_doctor,
)
from specforge.llm import FakeLLM, FakeReply, FakeToolCall
from specforge.orchestrator.registry import Registry
from tests.test_orchestrator import FakeAgent, spec

PROMPTS = str(Path(__file__).parent / "fixtures" / "prompts")


def by_name(checks: list[Check]) -> dict[tuple[str, str], Check]:
    return {(c.group, c.name): c for c in checks}


def healthy_llm() -> FakeLLM:
    return FakeLLM(
        [
            FakeReply(tool_calls=[FakeToolCall("add", {"a": 17, "b": 25})]),
            '{"result": 42}',
            FakeReply(content="156", reasoning="12*13=156"),
            FakeReply(content="156"),
        ]
    )


def test_llm_checks_ok_and_model_missing() -> None:
    llm = healthy_llm()
    deps = DoctorDeps(llm_factory=lambda: llm, list_models=lambda _: [llm.config.model])
    checks = check_llm(deps)
    assert [(c.name, c.status) for c in checks] == [
        ("модель", "OK"),
        ("tool calling", "OK"),
        ("json_schema", "OK"),
        ("thinking on/off", "OK"),
    ]
    llm.assert_exhausted()

    broken = FakeLLM([FakeReply(content="42")])  # без tool call
    deps = DoctorDeps(llm_factory=lambda: broken, list_models=lambda _: ["other"])
    (model,) = check_llm(deps)
    assert model.status == "FAIL"
    assert "LLM_MODEL" in model.hint


def test_agents_prompt_dir_image_and_gate_programs() -> None:
    assert gate_programs(["cd web && CI=1 npx tsc --noEmit", "dotnet test | tee log"]) == [
        "npx",
        "dotnet",
        "tee",
    ]
    no_prompts = FakeAgent(spec("coder", prompt_dir="missing"))
    imaged = FakeAgent(
        spec(
            "builder",
            prompt_dir=PROMPTS,
            sandbox_image="specforge/node:22",
            gate_commands=["npx tsc", "gradle test"],
        )
    )
    docker = MagicMock()
    docker.version.return_value = {"Version": "27.0"}
    docker.containers.run.return_value = b"ready\nmissing:gradle\n"
    deps = DoctorDeps(
        env={},
        llm_factory=healthy_llm,
        list_models=lambda llm: [llm.config.model],
        registry_factory=lambda: Registry([no_prompts, imaged]),
        docker_factory=lambda: docker,
    )
    checks = by_name(run_doctor(deps))

    assert checks[("агент coder", "prompt_dir")].status == "FAIL"
    assert "task.md" in checks[("агент coder", "prompt_dir")].hint
    assert checks[("агент builder", "sandbox_image")].status == "OK"
    gate = checks[("агент builder", "gate_commands")]
    assert (gate.status, gate.detail) == ("FAIL", "в specforge/node:22 нет: gradle")
    for group in ("Postgres", "Langfuse", "GitLab"):
        assert next(c for (g, _), c in checks.items() if g == group).status == "SKIP"
    assert checks[("Docker", "демон")].status == "OK"


def test_graph_unreachable_stage_without_mobile() -> None:
    registry = Registry(
        [
            FakeAgent(spec("analysis")),
            FakeAgent(spec("mobile_ui", depends_on=["analysis"], enabled_if="has_mobile")),
            FakeAgent(spec("push", depends_on=["mobile_ui"])),
            FakeAgent(spec("maps", depends_on=["analysis"], enabled_if="has_geo")),
        ]
    )
    checks = {c.name: c for c in check_graph(registry)}
    assert checks["всё включено"].status == "OK"
    assert checks["без geo"].status == "OK"
    cut = checks["без mobile"]
    assert cut.status == "FAIL"
    assert "push: зависит от выключенного mobile_ui" in cut.detail


def test_cli_doctor_prints_table_and_fails_on_bad_registry() -> None:
    def deps() -> DoctorDeps:
        down = MagicMock(side_effect=ConnectionError("refused"))
        return DoctorDeps(
            env={"SPECFORGE_PG_DSN": "postgresql://x", "GITLAB_URL": "u", "GITLAB_TOKEN": "t"},
            llm_factory=healthy_llm,
            list_models=lambda llm: [llm.config.model],
            registry_factory=lambda: Registry([FakeAgent(spec("a", depends_on=["ghost"]))]),
            docker_factory=down,
            pg_connect=down,
            gitlab_factory=lambda url, token: SimpleNamespace(auth=down),
        )

    app = typer.Typer()
    register_cli(app, deps)
    app.command("noop")(lambda: None)
    result = CliRunner().invoke(app, ["doctor", "--width", "200"])
    assert result.exit_code == 1, result.output
    for text in ("specforge doctor", "FAIL", "ghost", "docker compose", "Запустите Docker"):
        assert text.lower() in result.output.lower(), text
    assert "SKIP" in result.output  # Langfuse не настроен
