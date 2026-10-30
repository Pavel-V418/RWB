"""Раскладка итогового репозитория: корень и файлы в корне, вердикт приёмщика в отчёте и MR,
проверка раскладки в doctor."""

import base64
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from agents.acceptance.checks import _restore_root_files, assemble_repo
from specforge.core.contracts import AgentSpec
from specforge.core.events import Event, EventBus, EventType
from specforge.doctor import DoctorDeps, check_repo_layout
from specforge.orchestrator.publisher import LAYOUT_PATH, GitLabPublisher, RepoLayout, StageTarget
from tests.test_publisher import ENV, FIXTURE, commits, fake_project

ROOT_FILES = [".gitlab-ci.yml", "docker-compose.yml"]
LAYOUT = RepoLayout(
    stages={
        "input": "docs/tz/",
        "infra": StageTarget(dir="infra/", root=ROOT_FILES),
        "ci": ".",
        "acceptance": "docs/report/acceptance/",
    }
)


def write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_place_root_and_manifests() -> None:
    assert LAYOUT.place("infra", ".gitlab-ci.yml") == ".gitlab-ci.yml"
    assert LAYOUT.place("infra", "k8s/docker-compose.yml") == "infra/k8s/docker-compose.yml"
    assert LAYOUT.place("infra", "Dockerfile") == "infra/Dockerfile"
    assert LAYOUT.place("ci", "Makefile") == "Makefile"
    assert LAYOUT.place("nope", "a.txt") is None
    assert (LAYOUT.dir_of("ci"), LAYOUT.dir_of("infra"), LAYOUT.dir_of("nope")) == (
        "",
        "infra/",
        None,
    )
    assert LAYOUT.manifest_path("infra") == "infra/manifest.json"
    assert LAYOUT.manifest_path("ci") == ".specforge/ci.manifest.json"


def test_publisher_puts_root_files_and_acceptance_verdict(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    shutil.copytree(FIXTURE, runs / "fake-run")
    art = runs / "fake-run" / "artifacts"
    write(art / "infra" / ".gitlab-ci.yml", "stages: [build]")
    write(art / "infra" / "docker-compose.yml")
    write(art / "infra" / "k8s" / "app.yaml")
    write(art / "ci" / "Makefile")
    write(art / "ci" / "manifest.json", "{}")
    verdict = {"ready": False, "blockers": ["регрессия: backend_coder"], "regressions": ["backend"]}
    write(art / "acceptance" / "verdict.json", json.dumps(verdict, ensure_ascii=False))
    write(art / "acceptance" / "summary.md", "Не готово.")

    project = fake_project()
    gl = MagicMock()
    gl.projects.get.return_value = project
    bus = EventBus()
    GitLabPublisher(
        bus, runs, env=ENV, layout=LAYOUT, client_factory=lambda url, token: gl
    ).attach()

    def stage(name: str) -> Event:
        return Event(kind=EventType.stage_passed, run_id="fake-run", stage=name, agent=name)

    bus.emit(Event(kind=EventType.run_started, run_id="fake-run"))
    bus.emit(stage("infra"))
    bus.emit(stage("ci"))
    infra, _input, ci = commits(project)
    assert {a["file_path"] for a in infra["actions"]} == {
        ".gitlab-ci.yml",
        "docker-compose.yml",
        "infra/k8s/app.yaml",
    }
    assert {a["file_path"] for a in ci["actions"]} == {"Makefile", ".specforge/ci.manifest.json"}

    bus.emit(Event(kind=EventType.run_finished, run_id="fake-run"))
    files = {a["file_path"]: a for a in commits(project)[-1]["actions"]}
    assert "docs/report/acceptance/verdict.json" in files
    assert "docs/report/acceptance/summary.md" in files
    ours = json.loads(base64.b64decode(files["docs/report/verdict.json"]["content"]))
    assert ours["ready"] is False
    assert ours["blockers"] == ["регрессия: backend_coder"]
    assert ours["acceptance"] == "docs/report/acceptance/verdict.json"
    assert "приёмка: не готово (1 блокер(ов))" in ours["headline"]
    mr = project.mergerequests.create.call_args.args[0]
    assert "**Приёмка:** не готово." in mr["description"]
    assert "- регрессия: backend_coder" in mr["description"]


def test_acceptance_assembles_root_and_restores_it_for_gates(tmp_path: Path) -> None:
    art = tmp_path / "artifacts"
    write(art / "infra" / "docker-compose.yml", "services: {}")
    write(art / "infra" / "Dockerfile")
    write(art / "ci" / "Makefile")
    write(art / "other" / "a.txt")
    specs = {
        n: AgentSpec(name=n, stage=n, kind="tool")  # type: ignore[call-arg]
        for n in ("infra", "ci", "other")
    }
    repo = tmp_path / "repo"
    placed = assemble_repo(art, repo, LAYOUT, list(specs), specs)

    assert (repo / "docker-compose.yml").is_file()
    assert (repo / "infra" / "Dockerfile").is_file()
    assert (repo / "Makefile").is_file()
    assert (repo / "_unmapped" / "other" / "a.txt").is_file()
    assert placed == {"infra": "infra", "ci": "", "other": "_unmapped/other"}

    workspace = tmp_path / "work"
    shutil.copytree(repo / placed["infra"], workspace)
    _restore_root_files(repo, workspace, LAYOUT, "infra")
    assert (workspace / "docker-compose.yml").read_text(encoding="utf-8") == "services: {}"
    assert not (workspace / "Makefile").exists()  # чужой корневой файл не тянем


def test_doctor_reports_unmapped_stages() -> None:
    def registry(*stages: str) -> Any:
        return SimpleNamespace(
            specs={s: AgentSpec(name=s, stage=s, kind="tool") for s in stages}  # type: ignore[call-arg]
        )

    deps = DoctorDeps(layout_factory=lambda: LAYOUT)
    bad = check_repo_layout(registry("infra", "qa"), deps)
    assert bad.status == "FAIL"
    assert "qa" in bad.detail
    ok = check_repo_layout(registry("infra", "ci"), deps)
    assert ok.status == "OK"
    assert "в корне: ci" in ok.detail


def test_project_layout_maps_root_files() -> None:
    layout = RepoLayout.load(LAYOUT_PATH)
    for stage in ("infra", "devops"):
        assert layout.place(stage, ".gitlab-ci.yml") == ".gitlab-ci.yml"
        assert layout.place(stage, "docker-compose.yml") == "docker-compose.yml"
    assert layout.place("docs", "README.md") == "README.md"
    assert layout.place("e2e", "run.sh") == "e2e/run.sh"
    assert layout.dir_of("acceptance") == "docs/report/acceptance/"


def test_gate_logs_published_with_tail(tmp_path: Path) -> None:
    from specforge.orchestrator.publisher import gate_logs  # noqa: PLC0415

    write(tmp_path / "logs" / "backend.gate.log", "ok")
    write(tmp_path / "logs" / "acceptance_web.gate.log", "x" * 50 + "TAIL")
    write(tmp_path / "logs" / "backend-001-dotnet.log", "команда агента — не шлюз")
    files = gate_logs(tmp_path, "docs/report/logs/", max_bytes=10)
    assert sorted(files) == [
        "docs/report/logs/acceptance_web.gate.log",
        "docs/report/logs/backend.gate.log",
    ]
    assert files["docs/report/logs/backend.gate.log"] == b"ok"
    tail = files["docs/report/logs/acceptance_web.gate.log"].decode()
    assert tail.startswith("[обрезано: показаны последние 10 из 54 байт]\n")
    assert tail.endswith("xxxxxxTAIL")
