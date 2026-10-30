import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents._example.schemas.summary import DocumentSummary
from specforge import cli
from specforge.core.contracts import StageStatus
from specforge.llm import FakeLLM
from specforge.orchestrator.registry import RegistryError, available_agents, load_agent
from tests.conftest import MakeCtx

SUMMARY = DocumentSummary(
    title="Заметка",
    summary="Короткий документ для проверки агента.",
    key_points=["первый тезис"],
    open_questions=[],
)


def test_registry_finds_example() -> None:
    assert "_example" in available_agents()
    agent = load_agent("_example")
    assert agent.spec.kind == "structured"
    with pytest.raises(RegistryError, match="не найден"):
        load_agent("no_such_agent")


def test_example_agent_writes_summary(make_ctx: MakeCtx) -> None:
    llm = FakeLLM([SUMMARY.model_dump_json()])
    ctx = make_ctx(llm)
    (ctx.input_dir / "doc.md").write_text("# Заметка\nТекст.", "utf-8")

    result = load_agent("_example").run(ctx)

    assert result.status == StageStatus.passed, result.summary
    assert result.artifacts == ["summary.json"]
    saved = (ctx.output_dir / "summary.json").read_text("utf-8")
    assert DocumentSummary.model_validate_json(saved) == SUMMARY
    assert '<file path="doc.md">' in llm.calls[0].messages[1].content


def test_cli_agent_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    in_dir, out_dir, workdir = tmp_path / "in", tmp_path / "out", tmp_path / "run"
    in_dir.mkdir()
    (in_dir / "doc.md").write_text("# Заметка", "utf-8")
    monkeypatch.setattr(cli, "build_llm", lambda: FakeLLM([SUMMARY.model_dump_json()]))

    res = CliRunner().invoke(
        cli.app,
        [
            "agent",
            "run",
            "_example",
            "--in",
            str(in_dir),
            "--out",
            str(out_dir),
            "--workdir",
            str(workdir),
        ],
    )

    assert res.exit_code == 0, res.output
    assert json.loads((out_dir / "summary.json").read_text("utf-8"))["title"] == "Заметка"
    assert (workdir / "_example.result.json").exists()


def test_cli_unknown_agent(tmp_path: Path) -> None:
    res = CliRunner().invoke(
        cli.app, ["agent", "run", "nope", "--in", str(tmp_path), "--out", str(tmp_path / "o")]
    )
    assert res.exit_code == 2


def test_cli_list() -> None:
    res = CliRunner().invoke(cli.app, ["agent", "list"])
    assert res.exit_code == 0
    assert "_example" in res.output
