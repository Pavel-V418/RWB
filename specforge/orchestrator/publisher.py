"""Публикация прогона в GitLab (python-gitlab) — подписчик EventBus, узлы графа не знают о нём.

- run_started (или первое событие прогона после resume) — ветка run/<run_id> в GITLAB_PROJECT
  от целевой ветки; без GITLAB_PROJECT создаётся проект specforge-<run_id>;
- stage_passed / stage_degraded / stage_cached — один коммит через commits API (create/update)
  с файлами artifacts/<stage>/ по config/repo_layout.yaml, сообщение `stage(<agent>): <summary>`;
- run_finished — коммит отчёта (telemetry.report) и verdict.json, затем один раз MR в target_branch
  с таблицей этапов. MR не обновляется и не сливается.

Нет GITLAB_URL / GITLAB_TOKEN или GitLab недоступен — событие publish_warning, публикация
прогона пропускается. Локальный git не трогается: артефакты и так лежат в runs/<run_id>.
"""

import base64
import hashlib
import os
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, Field, JsonValue

from specforge.core.artifacts import MANIFEST_FILE, RunWorkspace, artifact_files
from specforge.core.contracts import StageStatus
from specforge.core.events import Event, EventBus, EventType
from specforge.orchestrator.nodes import PIPELINE_FILE, PipelineSummary
from specforge.paths import PROJECT_ROOT

log = structlog.get_logger(__name__)

LAYOUT_PATH = PROJECT_ROOT / "config" / "repo_layout.yaml"
WARNING = "publish_warning"
PUBLISHED = "published"
STAGE_EVENTS = {EventType.stage_passed, EventType.stage_degraded, "stage_cached"}
COMMIT_SUMMARY_CHARS = 72


class RepoLayout(BaseModel):
    stages: dict[str, str] = Field(default_factory=dict)
    report: str = "docs/report/"
    target_branch: str = "main"

    @classmethod
    def load(cls, path: Path = LAYOUT_PATH) -> "RepoLayout":
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def dir_of(self, stage: str) -> str | None:
        target = self.stages.get(stage)
        return target.rstrip("/") + "/" if target else None


ClientFactory = Callable[[str, str], Any]
"""(url, token) → gitlab.Gitlab; подменяется в тестах."""


def _gitlab_client(url: str, token: str) -> Any:
    import gitlab  # noqa: PLC0415 — нужен только с GITLAB_*

    return gitlab.Gitlab(url, private_token=token, timeout=30)


class _RunRepo:
    """Состояние публикации одного прогона."""

    def __init__(self, project: Any, branch: str) -> None:
        self.project = project
        self.branch = branch
        self.existing: set[str] = set()
        self.published: dict[str, str] = {}
        """путь → sha256 опубликованного содержимого."""
        self.mr_created = False


class GitLabPublisher:
    def __init__(
        self,
        bus: EventBus,
        runs_dir: Path,
        *,
        env: Mapping[str, str] | None = None,
        layout: RepoLayout | None = None,
        client_factory: ClientFactory = _gitlab_client,
    ) -> None:
        self.bus = bus
        self.runs_dir = runs_dir
        self.env = os.environ if env is None else env
        self.layout = layout or RepoLayout.load()
        self.client_factory = client_factory
        self._runs: dict[str, _RunRepo] = {}
        self._disabled: set[str] = set()
        self._lock = threading.Lock()

    def attach(self) -> Callable[[], None]:
        return self.bus.subscribe(self)

    def __call__(self, event: Event) -> None:
        if not event.run_id:  # свои publish_warning / published отсеются ниже
            return
        if event.kind not in {EventType.run_started, EventType.run_finished, *STAGE_EVENTS}:
            return
        run_id = event.run_id
        with self._lock:
            if run_id in self._disabled:
                return
            try:
                repo = self._repo(run_id)
                if repo is None:
                    return
                if event.kind in STAGE_EVENTS and event.stage:
                    self._publish_stage(repo, run_id, event)
                elif event.kind == EventType.run_finished:
                    self._finish(repo, run_id)
            except Exception as exc:
                self._disabled.add(run_id)
                log.warning("publisher.failed", run_id=run_id, error=str(exc))
                self._warn(run_id, f"GitLab недоступен, публикация прогона пропущена: {exc}")

    # --- ветка ----------------------------------------------------------------------------------

    def _repo(self, run_id: str) -> _RunRepo | None:
        if run_id in self._runs:
            return self._runs[run_id]
        url, token = self.env.get("GITLAB_URL"), self.env.get("GITLAB_TOKEN")
        if not url or not token:
            self._disabled.add(run_id)
            self._warn(run_id, "публикация в GitLab выключена: не заданы GITLAB_URL / GITLAB_TOKEN")
            return None
        gl = self.client_factory(url, token)
        project_ref = self.env.get("GITLAB_PROJECT")
        if project_ref:
            project = gl.projects.get(project_ref)
        else:
            project = gl.projects.create({"name": f"specforge-{run_id}"})
        target = self.layout.target_branch
        branch = f"run/{run_id}"
        names = {b.name for b in project.branches.list(get_all=True)}
        if target not in names:  # пустой (новый) проект — первый коммит создаёт ветку
            project.commits.create(
                {
                    "branch": target,
                    "commit_message": "init: репозиторий прогонов SpecForge",
                    "actions": [_action("create", "README.md", _readme(run_id))],
                }
            )
        if branch not in names:
            project.branches.create({"branch": branch, "ref": target})
        repo = _RunRepo(project, branch)
        repo.existing = {
            item["path"]
            for item in project.repository_tree(ref=branch, recursive=True, get_all=True)
            if item.get("type") == "blob"
        }
        self._runs[run_id] = repo
        self._emit(run_id, f"ветка {branch}", {"branch": branch, "project": str(project.id)})
        return repo

    # --- коммиты --------------------------------------------------------------------------------

    def _publish_stage(self, repo: _RunRepo, run_id: str, event: Event) -> None:
        stage = event.stage or ""
        target = self.layout.dir_of(stage)
        if target is None:
            self._warn(run_id, f"этап {stage!r} нет в config/repo_layout.yaml — не опубликован")
            return
        files = self._stage_files(run_id, stage)
        lines = event.message.strip().splitlines()
        summary = lines[0] if lines else ("из кэша" if event.kind == "stage_cached" else "готово")
        message = f"stage({event.agent or stage}): {summary[:COMMIT_SUMMARY_CHARS]}"
        self._commit(repo, run_id, {target + rel: data for rel, data in files.items()}, message)
        input_dir = self.layout.dir_of("input")
        if input_dir and not any(p.startswith(input_dir) for p in repo.published):
            self._commit(
                repo,
                run_id,
                {input_dir + rel: data for rel, data in self._stage_files(run_id, "input").items()},
                "stage(input): ТЗ прогона",
            )

    def _stage_files(self, run_id: str, stage: str) -> dict[str, bytes]:
        root = RunWorkspace(self.runs_dir / run_id).stage_dir(stage)
        files = {p.relative_to(root).as_posix(): p.read_bytes() for p in artifact_files(root)}
        manifest = root / MANIFEST_FILE
        if manifest.is_file():
            files[MANIFEST_FILE] = manifest.read_bytes()
        return files

    def _commit(self, repo: _RunRepo, run_id: str, files: dict[str, bytes], message: str) -> bool:
        actions = []
        for path, data in sorted(files.items()):
            digest = hashlib.sha256(data).hexdigest()
            if repo.published.get(path) == digest:
                continue
            kind = "update" if path in repo.existing else "create"
            actions.append((path, digest, _action(kind, path, data)))
        if not actions:
            return False
        repo.project.commits.create(
            {
                "branch": repo.branch,
                "commit_message": message,
                "actions": [a for _, _, a in actions],
            }
        )
        for path, digest, _ in actions:
            repo.existing.add(path)
            repo.published[path] = digest
        self._emit(run_id, message, {"files": len(actions), "branch": repo.branch})
        return True

    # --- финал ----------------------------------------------------------------------------------

    def _finish(self, repo: _RunRepo, run_id: str) -> None:
        from specforge.telemetry.report import build_report  # noqa: PLC0415 — нет цикла импорта

        run_dir = self.runs_dir / run_id
        summary = PipelineSummary.model_validate_json(
            (run_dir / PIPELINE_FILE).read_text(encoding="utf-8")
        )
        report = build_report(run_dir)
        verdict = _verdict(summary)
        prefix = self.layout.report.rstrip("/") + "/"
        files = {prefix + p.name: p.read_bytes() for p in sorted(report.markdown.parent.iterdir())}
        files[prefix + "verdict.json"] = verdict.model_dump_json(indent=2).encode()
        self._commit(repo, run_id, files, f"report: {verdict.verdict} — {verdict.headline}")
        if repo.mr_created or self._mr_exists(repo):
            return
        mr = repo.project.mergerequests.create(
            {
                "source_branch": repo.branch,
                "target_branch": self.layout.target_branch,
                "title": f"SpecForge {run_id}: {verdict.verdict}",
                "description": _mr_description(summary, verdict, prefix),
                "remove_source_branch": False,
            }
        )
        repo.mr_created = True
        self._emit(
            run_id, f"MR !{getattr(mr, 'iid', '?')}", {"mr": str(getattr(mr, "web_url", ""))}
        )

    def _mr_exists(self, repo: _RunRepo) -> bool:
        found = repo.project.mergerequests.list(
            source_branch=repo.branch, target_branch=self.layout.target_branch, state="opened"
        )
        repo.mr_created = bool(found)
        return repo.mr_created

    def _warn(self, run_id: str, message: str) -> None:
        self.bus.emit(Event(kind=WARNING, run_id=run_id, message=message))

    def _emit(self, run_id: str, message: str, data: dict[str, JsonValue]) -> None:
        self.bus.emit(Event(kind=PUBLISHED, run_id=run_id, message=message, data=data))


class Verdict(BaseModel):
    run_id: str
    verdict: str
    """passed — все этапы зелёные; degraded — есть degraded; failed — есть failed."""
    headline: str
    counts: dict[str, int]


def _verdict(summary: PipelineSummary) -> Verdict:
    counts = summary.counts
    if counts.get(StageStatus.failed.value):
        verdict = "failed"
    elif counts.get(StageStatus.degraded.value):
        verdict = "degraded"
    else:
        verdict = "passed"
    headline = ", ".join(f"{k} {v}" for k, v in counts.items() if v) or "этапов нет"
    return Verdict(run_id=summary.run_id, verdict=verdict, headline=headline, counts=counts)


def _mr_description(summary: PipelineSummary, verdict: Verdict, report_dir: str) -> str:
    lines = [
        f"Прогон **{summary.run_id}** — verdict: **{verdict.verdict}** ({verdict.headline}).",
        "",
        "| Этап | Статус | Попытки | Токены |",
        "|---|---|---:|---:|",
    ]
    lines += [
        f"| {name} | {r.status.value} | {r.attempts} | {r.usage.total_tokens} |"
        for name, r in summary.stages.items()
    ]
    lines += ["", f"Отчёт: `{report_dir}report.md`. Создано SpecForge; MR не обновляется."]
    return "\n".join(lines)


def _action(kind: str, path: str, data: bytes | str) -> dict[str, str]:
    raw = data.encode() if isinstance(data, str) else data
    return {
        "action": kind,
        "file_path": path,
        "content": base64.b64encode(raw).decode(),
        "encoding": "base64",
    }


def _readme(run_id: str) -> str:
    return f"# Прогоны SpecForge\n\nСоздан для прогона {run_id}. Каждый прогон — ветка run/<id>.\n"


def attach_publisher(bus: EventBus, runs_dir: Path) -> GitLabPublisher:
    publisher = GitLabPublisher(bus, runs_dir)
    publisher.attach()
    return publisher
