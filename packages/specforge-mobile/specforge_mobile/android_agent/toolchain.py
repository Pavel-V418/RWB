"""
Где собирать Android-проект: в Docker-образе песочницы, локальным Gradle или нигде.

    SPECFORGE_ANDROID_GATE   auto (по умолчанию) | docker | local | none
    SPECFORGE_ANDROID_IMAGE  образ сборки, по умолчанию specforge/android:jdk21-api36

auto: образ есть в Docker → docker; иначе JDK и Android SDK на хосте → local; иначе None —
собрать нечем, агент пишет код без шлюза и честно сообщает об этом (без ремонтов вслепую).
В образе нет сети: Gradle запускается с --offline на прогретом кэше (infra/android/Dockerfile).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import agent as _agent

DEFAULT_IMAGE = "specforge/android:jdk21-api36"
CONTAINER_DIR = "/work"


def image() -> str:
    return os.environ.get("SPECFORGE_ANDROID_IMAGE", DEFAULT_IMAGE)


def docker_image_available(name: str | None = None) -> bool:
    if not shutil.which("docker"):
        return False
    try:
        p = subprocess.run(["docker", "image", "inspect", name or image()], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return p.returncode == 0


def local_toolchain_available() -> bool:
    java = os.environ.get("JAVA_HOME") or shutil.which("java")
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    return bool(java and sdk and Path(sdk).is_dir())


def choose() -> str | None:
    """'docker' | 'local' | None (собрать нечем)."""
    mode = os.environ.get("SPECFORGE_ANDROID_GATE", "auto").strip().lower()
    if mode == "none":
        return None
    if mode in ("docker", "local"):
        return mode
    if docker_image_available():
        return "docker"
    if local_toolchain_available():
        return "local"
    return None


def docker_gate(project: Path, *, timeout: int = 1800, memory: str = "3g") -> dict:
    """Тот же Gradle, что в android_gate.run_gradle, но в образе песочницы: без сети, с лимитами,
    не от root. Пути контейнера (/work/...) в логе переводятся в пути хоста для разбора ошибок."""
    project = Path(project).resolve()
    gate = _agent._load_gate()
    wrapper = project / "gradlew"
    if wrapper.exists():
        wrapper.chmod(wrapper.stat().st_mode | 0o111)
    user = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else None
    cmd = ["docker", "run", "--rm", "--network", "none", "--memory", memory, "--pids-limit", "512",
           *(["-u", user] if user else []), "-v", f"{project}:{CONTAINER_DIR}", "-w", CONTAINER_DIR, image(),
           "./gradlew", "--no-daemon", "--offline", *gate.GRADLE_TASKS, "--console=plain", "--continue",
           "--stacktrace"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
        code, log = p.returncode, p.stdout + "\n" + p.stderr
    except subprocess.TimeoutExpired as e:
        code, log = 124, ((e.stdout or "") if isinstance(e.stdout, str) else "") + \
            f"\nGATE: превышен таймаут {timeout} с"
    log = log.replace(f"{CONTAINER_DIR}/", f"{project.as_posix()}/")
    (project / "build").mkdir(exist_ok=True)
    (project / "build/gate.log").write_text(log, encoding="utf-8")
    return gate.evaluate(project, code, log)


def no_toolchain_gate(_project: Path) -> dict:
    """Шлюз, когда собрать нечем: код сгенерирован, но не проверен."""
    return {"passed": False, "errors": [{
        "tool": "gradle", "file": "", "line": 0, "column": 0, "severity": "error", "category": "toolchain",
        "message": "Сборка не запускалась: нет Docker-образа " + image() + " и нет JDK + Android SDK на хосте "
                   "(SPECFORGE_ANDROID_GATE=docker|local). Код сгенерирован без проверки компилятором."}]}
