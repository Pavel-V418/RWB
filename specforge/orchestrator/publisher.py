"""Публикация прогона в GitLab (python-gitlab) — подписчик EventBus, узлы графа не знают о нём.

- run_started (или первое событие прогона после resume) — ветка run/<run_id> в GITLAB_PROJECT
  от целевой ветки; без GITLAB_PROJECT создаётся проект specforge-<run_id>;
- stage_passed / stage_degraded / stage_cached — один коммит через commits API (create/update)
  с файлами artifacts/<stage>/ по config/repo_layout.yaml, сообщение `stage(<agent>): <summary>`;
- run_finished — коммит отчёта (telemetry.report), verdict.json с вердиктом приёмщика и его
  verdict.json / summary.md (docs/report/acceptance/), затем один раз MR в target_branch
  с таблицей этапов и блокерами приёмки, плюс логи шлюзов (docs/report/logs/, длинные — хвостом,
  SPECFORGE_PUBLISH_LOG_BYTES). MR не обновляется и не сливается.

Раскладка (RepoLayout) — каталог этапа, "." — корень, `root` — файлы верхнего уровня этапа,
которые идут в корень (.gitlab-ci.yml, docker-compose.yml); её же использует приёмщик.

Нет GITLAB_URL / GITLAB_TOKEN или GitLab недоступен — событие publish_warning, публикация
прогона пропускается. Локальный git не трогается: артефакты и так лежат в runs/<run_id>.
"""

import base64
import fnmatch
import hashlib
import json
import os
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, Field, JsonValue

from specforge.core.artifacts import LOGS_DIR, MANIFEST_FILE, RunWorkspace, artifact_files
from specforge.core.contracts import StageStatus
from specforge.core.events import Event, EventBus, EventType
from specforge.orchestrator.nodes import PIPELINE_FILE, PipelineSummary
from specforge.orchestrator.registry import ACCEPTANCE_STAGE
from specforge.paths import PROJECT_ROOT

log = structlog.get_logger(__name__)

LAYOUT_PATH = PROJECT_ROOT / "config" / "repo_layout.yaml"
WARNING = "publish_warning"
PUBLISHED = "published"
STAGE_EVENTS = {EventType.stage_passed, EventType.stage_degraded, "stage_cached"}
COMMIT_SUMMARY_CHARS = 72
ACCEPTANCE_VERDICT = "verdict.json"
DEFAULT_LOG_MAX_BYTES = 256_000
"""Лог шлюза больше этого публикуется хвостом (полный остаётся в runs/<id>/logs/)."""
MAX_BLOCKERS = 20


ROOT_DIRS = frozenset({"", ".", "./", "/"})
"""Значения dir, означающие корень репозитория."""


class StageTarget(BaseModel):
    """Куда попадают файлы этапа: каталог `dir` ("." — корень репозитория) и файлы верхнего
    уровня этапа по шаблонам `root` — в корень (.gitlab-ci.yml, docker-compose.yml, README.md)."""

    dir: str = "."
    root: list[str] = Field(default_factory=list)


class RepoLayout(BaseModel):
    stages: dict[str, str | StageTarget] = Field(default_factory=dict)
    report: str = "docs/report/"
    target_branch: str = "main"

    @classmethod
    def load(cls, path: Path = LAYOUT_PATH) -> "RepoLayout":
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def target(self, stage: str) -> StageTarget | None:
        value = self.stages.get(stage)
        return StageTarget(dir=value) if isinstance(value, str) else value

    def dir_of(self, stage: str) -> str | None:
        """Префикс каталога этапа в репозитории: "" — корень, None — этапа нет в раскладке."""
        target = self.target(stage)
        if target is None:
            return None
        return "" if target.dir.strip() in ROOT_DIRS else target.dir.strip().rstrip("/") + "/"

    def place(self, stage: str, rel: str) -> str | None:
        """Путь файла этапа (rel — относительно artifacts/<stage>/) в репозитории."""
        target, prefix = self.target(stage), self.dir_of(stage)
        if target is None or prefix is None:
            return None
        if "/" not in rel and any(fnmatch.fnmatchcase(rel, g) for g in target.root):
            return rel
        return prefix + rel

    def manifest_path(self, stage: str) -> str | None:
        """manifest.json этапа: в каталоге этапа, у этапа в корне — .specforge/<stage>.manifest.json
        (иначе манифесты корневых этапов перетирали бы друг друга)."""
        prefix = self.dir_of(stage)
        if prefix is None:
            return None
        return prefix + MANIFEST_FILE if prefix else f".specforge/{stage}.{MANIFEST_FILE}"


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
        lines = event.message.strip().splitlines()
        summary = lines[0] if lines else ("из кэша" if event.kind == "stage_cached" else "готово")
        message = f"stage({event.agent or stage}): {summary[:COMMIT_SUMMARY_CHARS]}"
        self._commit(repo, run_id, self._stage_files(run_id, stage), message)
        input_files = self._stage_files(run_id, "input")
        if input_files and not any(p in repo.published for p in input_files):
            self._commit(repo, run_id, input_files, "stage(input): ТЗ прогона")

    def _stage_files(self, run_id: str, stage: str) -> dict[str, bytes]:
        """Файлы этапа по путям в репозитории (repo_layout); этапа нет в раскладке — пусто."""
        root = RunWorkspace(self.runs_dir / run_id).stage_dir(stage)
        files: dict[str, bytes] = {}
        for p in artifact_files(root):
            path = self.layout.place(stage, p.relative_to(root).as_posix())
            if path is not None:
                files[path] = p.read_bytes()
        manifest, target = root / MANIFEST_FILE, self.layout.manifest_path(stage)
        if manifest.is_file() and target is not None:
            files[target] = manifest.read_bytes()
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
        acceptance = _load_acceptance(run_dir)
        verdict = _verdict(summary, acceptance)
        prefix = self.layout.report.rstrip("/") + "/"
        files = {prefix + p.name: p.read_bytes() for p in sorted(report.markdown.parent.iterdir())}
        if acceptance is not None:
            # этап acceptance мог не опубликоваться (нет в repo_layout или failed) — вердикт
            # приёмщика кладём рядом с отчётом всегда
            source = RunWorkspace(run_dir).stage_dir(ACCEPTANCE_STAGE)
            for name in (ACCEPTANCE_VERDICT, "summary.md"):
                if (source / name).is_file():
                    files[f"{prefix}acceptance/{name}"] = (source / name).read_bytes()
            verdict.acceptance = f"{prefix}acceptance/{ACCEPTANCE_VERDICT}"
        files.update(gate_logs(run_dir, prefix + "logs/", self._log_max_bytes()))
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

    def _log_max_bytes(self) -> int:
        return int(self.env.get("SPECFORGE_PUBLISH_LOG_BYTES", DEFAULT_LOG_MAX_BYTES))

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
    """passed — все этапы зелёные и приёмщик (если есть) считает продукт готовым;
    degraded — есть degraded или приёмщик нашёл блокеры; failed — есть failed."""
    headline: str
    counts: dict[str, int]
    ready: bool | None = None
    """Вердикт приёмщика (agents/acceptance); None — приёмщика в прогоне не было."""
    blockers: list[str] = Field(default_factory=list)
    regressions: list[str] = Field(default_factory=list)
    acceptance: str | None = None
    """Путь полного verdict.json приёмщика в репозитории."""


def _verdict(summary: PipelineSummary, acceptance: Mapping[str, Any] | None = None) -> Verdict:
    counts = summary.counts
    if counts.get(StageStatus.failed.value):
        verdict = "failed"
    elif counts.get(StageStatus.degraded.value):
        verdict = "degraded"
    else:
        verdict = "passed"
    headline = ", ".join(f"{k} {v}" for k, v in counts.items() if v) or "этапов нет"
    result = Verdict(run_id=summary.run_id, verdict=verdict, headline=headline, counts=counts)
    if acceptance is None:
        return result
    ready = bool(acceptance.get("ready"))
    blockers = [str(b) for b in acceptance.get("blockers") or []]
    if not ready and verdict == "passed":
        result.verdict = "degraded"
    result.ready = ready
    result.blockers = blockers[:MAX_BLOCKERS]
    result.regressions = [str(r) for r in acceptance.get("regressions") or []][:MAX_BLOCKERS]
    result.headline += "; приёмка: " + (
        "готово" if ready else f"не готово ({len(blockers)} блокер(ов))"
    )
    return result


def _load_acceptance(run_dir: Path) -> dict[str, Any] | None:
    path = RunWorkspace(run_dir).stage_dir(ACCEPTANCE_STAGE) / ACCEPTANCE_VERDICT
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


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
    if verdict.ready is not None:
        lines += ["", f"**Приёмка:** {'готово' if verdict.ready else 'не готово'}."]
        lines += [f"- {b}" for b in verdict.blockers]
        if verdict.acceptance:
            lines.append(f"Полный вердикт: `{verdict.acceptance}`.")
    lines += ["", f"Отчёт: `{report_dir}report.md`. Создано SpecForge; MR не обновляется."]
    return "\n".join(lines)


def gate_logs(run_dir: Path, prefix: str, max_bytes: int) -> dict[str, bytes]:
    """Логи шлюзов прогона (<agent>.gate.log, в т.ч. повторы приёмщика) — результаты проверок
    воспроизводимы по репозиторию; длинные — хвостом с пометкой."""
    files: dict[str, bytes] = {}
    logs = run_dir / LOGS_DIR
    for path in sorted(logs.glob("*.gate.log")) if logs.is_dir() else []:
        data = path.read_bytes()
        if len(data) > max_bytes:
            note = f"[обрезано: показаны последние {max_bytes} из {len(data)} байт]\n"
            data = note.encode() + data[-max_bytes:]
        files[prefix + path.name] = data
    return files


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
