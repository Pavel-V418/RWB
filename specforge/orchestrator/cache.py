"""Кэш этапов между запусками.

Ключ этапа — sha256 от: AgentSpec (JSON), файлов промптов (`prompt_dir`), исходника модуля
агента и входных артефактов (файлы artifacts/ под `spec.inputs`). Если ключ совпал с прошлым
passed-результатом, этап не запускается: его выходы (файлы под `spec.outputs`) копируются из
кэша, StageResult берётся оттуда же с нулевым usage.

    <cache_dir>/<agent>/<key>/result.json
    <cache_dir>/<agent>/<key>/files/...

Агенты без `outputs` не кэшируются: неизвестно, какие файлы восстанавливать.
"""

import hashlib
import inspect
import shutil
import uuid
from pathlib import Path

import structlog

from specforge.core.agent_api import Agent
from specforge.core.artifacts import artifact_files, glob_files, sha256_file
from specforge.core.contracts import StageResult, StageStatus, Usage
from specforge.core.selectors import input_globs

log = structlog.get_logger(__name__)

RESULT_FILE = "result.json"
FILES_DIR = "files"


def agent_dir(agent: Agent) -> Path:
    return Path(inspect.getfile(type(agent))).resolve().parent


def stage_key(agent: Agent, artifacts_dir: Path) -> str:
    spec = agent.spec
    h = hashlib.sha256()
    h.update(b"spec\0" + spec.model_dump_json().encode())
    source = Path(inspect.getfile(type(agent)))
    h.update(b"\0source\0" + sha256_file(source).encode())
    prompts = agent_dir(agent) / spec.prompt_dir
    for path in artifact_files(prompts):
        h.update(f"\0prompt\0{path.relative_to(prompts).as_posix()}\0{sha256_file(path)}".encode())
    # код и промпты агента вне его каталога (адаптеры к пакетам packages/): правка — промах кэша
    for root in getattr(agent, "cache_sources", ()):
        for path in artifact_files(Path(root)):
            rel = path.relative_to(root).as_posix()
            h.update(f"\0source\0{Path(root).name}/{rel}\0{sha256_file(path)}".encode())
    for path in glob_files(artifacts_dir, input_globs(spec.inputs)):  # файл целиком
        rel = path.relative_to(artifacts_dir).as_posix()
        h.update(f"\0input\0{rel}\0{sha256_file(path)}".encode())
    return h.hexdigest()


class StageCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def lookup(self, agent: Agent, key: str) -> StageResult | None:
        entry = self.root / agent.spec.name / key
        path = entry / RESULT_FILE
        if not agent.spec.outputs or not path.is_file():
            return None
        try:
            result = StageResult.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            log.warning("stage_cache.corrupt", agent=agent.spec.name, key=key)
            return None
        if result.status != StageStatus.passed:
            return None
        if any(not (entry / FILES_DIR / a).is_file() for a in result.artifacts):
            return None
        return result

    def restore(self, agent: Agent, key: str, result: StageResult, stage_dir: Path) -> StageResult:
        """Скопировать выходы из кэша в каталог этапа; результат — с нулевым usage."""
        files = self.root / agent.spec.name / key / FILES_DIR
        for rel in result.artifacts:
            target = stage_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(files / rel, target)
        return result.model_copy(
            update={
                "usage": Usage(),
                "duration_s": 0.0,
                "summary": f"кэш {key[:12]}: {result.summary}"[:1000],
            }
        )

    def store(self, agent: Agent, key: str, result: StageResult, stage_dir: Path) -> None:
        if result.status != StageStatus.passed or not agent.spec.outputs:
            return
        outputs = [
            p.relative_to(stage_dir).as_posix() for p in glob_files(stage_dir, agent.spec.outputs)
        ]
        target = self.root / agent.spec.name / key
        staging = target.with_name(f".{key}.{uuid.uuid4().hex[:8]}")
        try:
            for rel in outputs:
                dest = staging / FILES_DIR / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(stage_dir / rel, dest)
            staging.mkdir(parents=True, exist_ok=True)
            cached = result.model_copy(update={"artifacts": outputs})
            (staging / RESULT_FILE).write_text(cached.model_dump_json(indent=2), "utf-8")
            if target.exists():
                shutil.rmtree(target)
            staging.rename(target)
        except OSError:
            log.exception("stage_cache.store_failed", agent=agent.spec.name)
            shutil.rmtree(staging, ignore_errors=True)
