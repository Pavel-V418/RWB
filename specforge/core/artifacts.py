"""Workspace артефактов запуска.

    runs/<run_id>/
      artifacts/
        input/            — входное ТЗ (копия файла, переданного в `specforge run`)
        <stage>/          — output_dir агентов этапа (/work в песочнице)
          manifest.json   — список файлов этапа с sha256
      logs/               — полные логи команд и шлюзов
      pipeline.json       — итог конвейера (пишет узел finalize)

Служебные каталоги (`.specforge/` — git-снимки ремонта) и сам manifest.json в манифест
не попадают.
"""

import hashlib
import shutil
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

ARTIFACTS_DIR = "artifacts"
INPUT_STAGE = "input"
LOGS_DIR = "logs"
INPUT_DATA_DIR = "data"
"""Данные, приложенные к ТЗ (`run --data`): artifacts/input/data/."""
MANIFEST_FILE = "manifest.json"
SERVICE_DIRS = frozenset({".specforge"})
"""Каталоги этапа, которые не являются артефактами (= repair.snapshots.STATE_DIR)."""


class ManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    """Путь относительно каталога этапа, через `/`."""
    sha256: str
    size: int


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    agents: list[str] = Field(default_factory=list)
    """Агенты, писавшие в каталог этапа."""
    files: list[ManifestEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def digest(self) -> str:
        """Хэш содержимого этапа (пути + sha256), без времени создания."""
        h = hashlib.sha256()
        for entry in self.files:
            h.update(f"{entry.path}\0{entry.sha256}\n".encode())
        return h.hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def artifact_files(root: Path) -> list[Path]:
    """Файлы каталога без служебных каталогов и manifest.json, в стабильном порядке."""
    if not root.is_dir():
        return []
    found = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if rel.parts[0] in SERVICE_DIRS or rel.as_posix() == MANIFEST_FILE:
            continue
        if path.is_file():
            found.append(path)
    return sorted(found)


def glob_files(root: Path, patterns: Sequence[str]) -> list[Path]:
    """Файлы под `root`, подходящие под glob-шаблоны, без служебных каталогов."""
    found = {
        p
        for pattern in patterns
        for p in root.glob(pattern)
        if p.is_file()
        and p.relative_to(root).parts[0] not in SERVICE_DIRS
        and p.name != MANIFEST_FILE
    }
    return sorted(found)


def build_manifest(stage: str, root: Path, agents: Iterable[str] = ()) -> Manifest:
    return Manifest(
        stage=stage,
        agents=sorted(set(agents)),
        files=[
            ManifestEntry(
                path=p.relative_to(root).as_posix(), sha256=sha256_file(p), size=p.stat().st_size
            )
            for p in artifact_files(root)
        ],
    )


class RunWorkspace:
    """Каталоги одного запуска: runs/<run_id>/."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def for_run(cls, runs_dir: Path, run_id: str) -> "RunWorkspace":
        return cls((runs_dir / run_id).resolve())

    @property
    def artifacts_dir(self) -> Path:
        return self.root / ARTIFACTS_DIR

    @property
    def logs_dir(self) -> Path:
        return self.root / LOGS_DIR

    @property
    def input_dir(self) -> Path:
        return self.artifacts_dir / INPUT_STAGE

    def stage_dir(self, stage: str) -> Path:
        return self.artifacts_dir / stage

    def artifact_path(self, rel: str) -> Path:
        """Путь артефакта по пути относительно artifacts/; ValueError — если он выходит за
        artifacts/, указывает на служебный каталог, manifest.json или корень."""
        root = self.artifacts_dir.resolve()
        path = (root / rel).resolve()
        if not path.is_relative_to(root) or path == root:
            raise ValueError(f"путь {rel!r} вне artifacts/")
        parts = path.relative_to(root).parts
        if len(parts) < 2 or any(p in SERVICE_DIRS for p in parts) or path.name == MANIFEST_FILE:
            raise ValueError(f"путь {rel!r}: ожидается <этап>/<файл>, не служебный")
        return path

    def prepare(self, requirements: Path | None = None, data: Path | None = None) -> None:
        """Создать каталоги запуска, положить ТЗ в artifacts/input/ и приложенные к ТЗ данные
        (каталог) — в artifacts/input/data/."""
        for d in (self.artifacts_dir, self.logs_dir, self.input_dir):
            d.mkdir(parents=True, exist_ok=True)
        if requirements is not None:
            shutil.copy2(requirements, self.input_dir / requirements.name)
        if data is not None:
            shutil.copytree(data, self.input_dir / INPUT_DATA_DIR, dirs_exist_ok=True)

    def clean_outputs(self, stage: str, patterns: Sequence[str]) -> Path:
        """Удалить прежние выходы агента (файлы под его шаблоны outputs) перед запуском этапа.

        Файлы других агентов того же этапа не трогаются; каталог этапа создаётся при отсутствии.
        """
        root = self.stage_dir(stage)
        root.mkdir(parents=True, exist_ok=True)
        for path in glob_files(root, patterns):
            path.unlink()
        return root

    def write_manifest(self, stage: str, agent: str) -> Manifest:
        """Пересобрать manifest.json этапа по текущему содержимому каталога."""
        root = self.stage_dir(stage)
        previous = self.read_manifest(stage)
        agents = [*(previous.agents if previous else []), agent]
        manifest = build_manifest(stage, root, agents)
        root.mkdir(parents=True, exist_ok=True)
        (root / MANIFEST_FILE).write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        return manifest

    def read_manifest(self, stage: str) -> Manifest | None:
        path = self.stage_dir(stage) / MANIFEST_FILE
        if not path.is_file():
            return None
        return Manifest.model_validate_json(path.read_text(encoding="utf-8"))

    def verify(self, stage: str) -> list[str]:
        """Расхождения каталога этапа с manifest.json: изменённые, пропавшие и лишние файлы."""
        manifest = self.read_manifest(stage)
        if manifest is None:
            return [f"{stage}: нет {MANIFEST_FILE}"]
        actual = {e.path: e.sha256 for e in build_manifest(stage, self.stage_dir(stage)).files}
        expected = {e.path: e.sha256 for e in manifest.files}
        problems = [f"изменён {p}" for p in expected if p in actual and actual[p] != expected[p]]
        problems += [f"нет {p}" for p in expected if p not in actual]
        problems += [f"лишний {p}" for p in actual if p not in expected]
        return problems
