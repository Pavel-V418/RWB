"""GitLabPublisher на моке python-gitlab и фейковом прогоне tests/fixtures/runs/fake-run."""

import base64
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from specforge.core.events import Event, EventBus, EventType
from specforge.orchestrator.publisher import GitLabPublisher

FIXTURE = Path(__file__).parent / "fixtures" / "runs" / "fake-run"
ENV = {"GITLAB_URL": "https://gitlab.test", "GITLAB_TOKEN": "t", "GITLAB_PROJECT": "team/app"}


def fake_project(branches: tuple[str, ...] = ("main",), tree: tuple[str, ...] = ()) -> Any:
    project = MagicMock()
    project.id = 7
    project.branches.list.return_value = [SimpleNamespace(name=b) for b in branches]
    project.repository_tree.return_value = [{"path": p, "type": "blob"} for p in tree]
    project.mergerequests.list.return_value = []
    project.mergerequests.create.return_value = SimpleNamespace(iid=1, web_url="https://mr/1")
    return project


@pytest.fixture
def runs_dir(tmp_path: Path) -> Path:
    shutil.copytree(FIXTURE, tmp_path / "runs" / "fake-run")
    return tmp_path / "runs"


def setup(runs_dir: Path, env: dict[str, str], gl: Any) -> tuple[EventBus, list[Event]]:
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe(seen.append)
    GitLabPublisher(bus, runs_dir, env=env, client_factory=lambda url, token: gl).attach()
    return bus, seen


def stage_event(kind: str, stage: str, agent: str, message: str = "готово") -> Event:
    return Event(kind=kind, run_id="fake-run", stage=stage, agent=agent, message=message)


def commits(project: Any) -> list[dict[str, Any]]:
    return [c.args[0] for c in project.commits.create.call_args_list]


def test_branch_stage_commits_report_and_single_mr(runs_dir: Path) -> None:
    project = fake_project(tree=("README.md", "backend/backend_coder.txt"))
    gl = MagicMock()
    gl.projects.get.return_value = project
    bus, seen = setup(runs_dir, ENV, gl)

    bus.emit(Event(kind=EventType.run_started, run_id="fake-run"))
    project.branches.create.assert_called_once_with({"branch": "run/fake-run", "ref": "main"})

    bus.emit(stage_event(EventType.stage_degraded, "backend", "backend_coder", "ремонт\nдетали"))
    bus.emit(stage_event(EventType.stage_degraded, "backend", "backend_coder"))  # без изменений
    bus.emit(stage_event(EventType.stage_passed, "web", "web_coder"))
    bus.emit(stage_event(EventType.stage_passed, "assembly", "assembler"))  # нет в раскладке
    stage_commit, input_commit, web_commit = commits(project)
    assert stage_commit["branch"] == "run/fake-run"
    assert stage_commit["commit_message"] == "stage(backend_coder): ремонт"
    actions = {a["file_path"]: a["action"] for a in stage_commit["actions"]}
    assert actions == {"backend/backend_coder.txt": "update", "backend/manifest.json": "create"}
    assert [a["file_path"] for a in input_commit["actions"]] == ["docs/tz/requirements.md"]
    assert web_commit["commit_message"] == "stage(web_coder): готово"
    assert any(e.kind == "publish_warning" and "assembly" in e.message for e in seen)

    bus.emit(Event(kind=EventType.run_finished, run_id="fake-run"))
    bus.emit(Event(kind=EventType.run_finished, run_id="fake-run"))
    report_commit = commits(project)[-1]
    files = {a["file_path"]: a for a in report_commit["actions"]}
    assert {"docs/report/report.md", "docs/report/stages_gantt.png"} <= set(files)
    verdict = json.loads(base64.b64decode(files["docs/report/verdict.json"]["content"]))
    assert verdict["verdict"] == "degraded"
    assert report_commit["commit_message"].startswith("report: degraded")

    project.mergerequests.create.assert_called_once()
    mr = project.mergerequests.create.call_args.args[0]
    assert (mr["source_branch"], mr["target_branch"]) == ("run/fake-run", "main")
    assert "| backend_coder | degraded | 3 | 21700 |" in mr["description"]


def test_no_env_or_unreachable_gitlab_warns_and_skips(runs_dir: Path) -> None:
    factory = MagicMock()
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe(seen.append)
    GitLabPublisher(bus, runs_dir, env={}, client_factory=factory).attach()
    bus.emit(Event(kind=EventType.run_started, run_id="fake-run"))
    bus.emit(stage_event(EventType.stage_passed, "web", "web_coder"))
    factory.assert_not_called()
    assert [e.kind for e in seen].count("publish_warning") == 1

    gl = MagicMock()
    gl.projects.get.side_effect = ConnectionError("connection refused")
    bus, seen = setup(runs_dir, ENV, gl)
    bus.emit(Event(kind=EventType.run_started, run_id="fake-run"))
    bus.emit(stage_event(EventType.stage_passed, "web", "web_coder"))
    bus.emit(Event(kind=EventType.run_finished, run_id="fake-run"))
    assert gl.projects.get.call_count == 1  # после ошибки прогон больше не публикуется
    warnings = [e for e in seen if e.kind == "publish_warning"]
    assert len(warnings) == 1
    assert "connection refused" in warnings[0].message


def test_creates_project_when_not_configured(runs_dir: Path) -> None:
    project = fake_project(branches=())
    gl = MagicMock()
    gl.projects.create.return_value = project
    bus, _ = setup(runs_dir, {k: v for k, v in ENV.items() if k != "GITLAB_PROJECT"}, gl)

    bus.emit(stage_event(EventType.stage_passed, "api", "api_designer"))  # resume: без run_started
    gl.projects.create.assert_called_once_with({"name": "specforge-fake-run"})
    gl.projects.get.assert_not_called()
    init, stage = commits(project)[:2]
    assert (init["branch"], init["actions"][0]["file_path"]) == ("main", "README.md")
    project.branches.create.assert_called_once_with({"branch": "run/fake-run", "ref": "main"})
    assert stage["actions"][0]["file_path"].startswith("docs/api/")
