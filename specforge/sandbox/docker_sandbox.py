"""Песочница в Docker: один контейнер на этап из AgentSpec.sandbox_image.

- workspace смонтирован в /work;
- кэши пакетных менеджеров — именованные volumes, общие для всех запусков;
- лимиты CPU/RAM/сеть — из env (SANDBOX_CPUS, SANDBOX_MEMORY, SANDBOX_NETWORK);
- close() возвращает файлам /work владельца хоста и удаляет контейнер.
"""

import os
import shlex
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import docker
import docker.errors
from docker.models.containers import Container

from specforge.sandbox.base import WORKDIR, BaseSandbox, SandboxError

# имя кэша → (путь в контейнере, переменная окружения, которую читает инструмент)
CACHE_VOLUMES: dict[str, tuple[str, str]] = {
    "nuget": ("/cache/nuget", "NUGET_PACKAGES"),
    "npm": ("/cache/npm", "npm_config_cache"),
    "gradle": ("/cache/gradle", "GRADLE_USER_HOME"),
    "pip": ("/cache/pip", "PIP_CACHE_DIR"),
    "trivy": ("/cache/trivy", "TRIVY_CACHE_DIR"),
}
"""Каталоги созданы в образах infra/images/ с правами 1777: пустой volume при первом монтировании
получает их содержимое (прогретые пакеты, база Trivy)."""
CACHE_VOLUME_PREFIX = "specforge-cache-"
KILL_GRACE_S = 5


class DockerSandbox(BaseSandbox):
    def __init__(
        self,
        image: str,
        workspace: Path,
        *,
        logs_dir: Path | None = None,
        log_prefix: str = "",
        cpus: float | None = None,
        memory: str | None = None,
        network: str | None = None,
        labels: Mapping[str, str] | None = None,
        client: Any = None,
    ) -> None:
        super().__init__(workspace, logs_dir=logs_dir, log_prefix=log_prefix)
        self.image = image
        self.cpus = cpus if cpus is not None else float(os.environ.get("SANDBOX_CPUS", "4"))
        self.memory = memory or os.environ.get("SANDBOX_MEMORY", "8g")
        self.network = network or os.environ.get("SANDBOX_NETWORK", "bridge")
        try:
            self._client = client or docker.from_env()
            self._container: Container | None = self._client.containers.run(
                image,
                command=["sleep", "infinity"],
                name=f"specforge-{self.log_prefix}-{uuid.uuid4().hex[:6]}",
                detach=True,
                working_dir=WORKDIR,
                volumes={
                    str(workspace.resolve()): {"bind": WORKDIR, "mode": "rw"},
                    **{
                        CACHE_VOLUME_PREFIX + name: {"bind": path, "mode": "rw"}
                        for name, (path, _) in CACHE_VOLUMES.items()
                    },
                },
                environment={env: path for path, env in CACHE_VOLUMES.values()},
                nano_cpus=int(self.cpus * 1e9),
                mem_limit=self.memory,
                network=self.network,
                labels={"specforge": "sandbox", **(labels or {})},
            )
        except docker.errors.DockerException as exc:
            raise SandboxError(f"не удалось запустить контейнер {image}: {exc}") from exc

    def _run(
        self, command: str, workdir: Path, timeout_s: float | None
    ) -> tuple[int, str, str, bool]:
        container = self._require_container()
        inner = f"sh -c {shlex.quote(command)}"
        if timeout_s is not None:
            inner = f"timeout -k {KILL_GRACE_S} {max(1, int(timeout_s))} {inner}"
        rel = self.display(workdir)
        try:
            res = container.exec_run(
                ["sh", "-c", inner],
                workdir=WORKDIR if rel == "." else f"{WORKDIR}/{rel}",
                demux=True,
            )
        except docker.errors.DockerException as exc:
            raise SandboxError(f"exec в контейнере не удался: {exc}") from exc
        output: Any = res.output  # при demux=True — (stdout | None, stderr | None)
        stdout, stderr = output if isinstance(output, tuple) else (output, None)
        code = int(res.exit_code if res.exit_code is not None else -1)
        timed_out = timeout_s is not None and code in (124, 137)
        return code, _decode(stdout), _decode(stderr), timed_out

    def close(self) -> None:
        container, self._container = self._container, None
        if container is None:
            return
        try:
            getuid, getgid = getattr(os, "getuid", None), getattr(os, "getgid", None)
            if getuid and getgid:  # файлы из контейнера создаются root — вернуть владельца хоста
                container.exec_run(["chown", "-R", f"{getuid()}:{getgid()}", WORKDIR])
            container.remove(force=True)
        except docker.errors.DockerException as exc:
            raise SandboxError(f"не удалось удалить контейнер: {exc}") from exc

    def _require_container(self) -> Container:
        if self._container is None:
            raise SandboxError("песочница уже закрыта")
        return self._container


def _decode(data: bytes | None) -> str:
    return data.decode("utf-8", errors="replace") if data else ""
