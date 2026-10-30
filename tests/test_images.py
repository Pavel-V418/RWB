"""Образы песочниц infra/images/: согласованы с build.sh и doctor, версии закреплены, есть git и
каталоги кэшей DockerSandbox; pg-up. Живая сборка — только с SPECFORGE_TEST_IMAGES=1."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from specforge.doctor import SANDBOX_IMAGES
from specforge.sandbox.docker_sandbox import CACHE_VOLUMES

IMAGES = Path(__file__).resolve().parent.parent / "infra" / "images"
DOCKERFILES = sorted(IMAGES.glob("*/Dockerfile"))


def build_tags() -> dict[str, str]:
    text = (IMAGES / "build.sh").read_text(encoding="utf-8")
    return dict(re.findall(r'\[(\w+)\]="([^"]+)"', text))


def test_build_script_and_doctor_know_every_image() -> None:
    names = {p.parent.name for p in DOCKERFILES}
    assert names == {"dotnet", "node", "postgis", "devops", "android"}
    tags = build_tags()
    assert set(tags) == names
    assert set(tags.values()) == set(SANDBOX_IMAGES)


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.parent.name)
def test_dockerfile_pins_base_and_has_sandbox_basics(dockerfile: Path) -> None:
    text = dockerfile.read_text(encoding="utf-8")
    base = re.search(r"^FROM\s+(\S+)", text, re.MULTILINE)
    assert base is not None
    assert ":" in base.group(1)
    assert "latest" not in base.group(1)
    joined = re.sub(r"\\\n\s*", " ", text)  # продолжения строк RUN
    assert re.search(r"apt-get install[^\n]*\bgit\b", joined), "git нужен для снимков ремонта"
    assert "coreutils" in text, "timeout -k в exec песочницы"
    for path, _env in CACHE_VOLUMES.values():
        assert path in text, f"нет каталога кэша {path}"
    assert "WORKDIR /work" in text


def test_pg_up_script() -> None:
    script = (IMAGES / "postgis" / "pg-up").read_text(encoding="utf-8")
    assert script.startswith("#!/bin/sh\n")
    assert "set -eu" in script
    assert "pg_isready" in script  # идемпотентен
    assert "CREATE EXTENSION IF NOT EXISTS postgis" in script
    assert "\r" not in script


@pytest.mark.skipif(
    os.environ.get("SPECFORGE_TEST_IMAGES") != "1" or shutil.which("docker") is None,
    reason="живая проверка образов: SPECFORGE_TEST_IMAGES=1 и собранные образы",
)
@pytest.mark.parametrize("tag", SANDBOX_IMAGES)
def test_built_image_runs_sandbox_commands(tag: str) -> None:
    script = "command -v git && command -v timeout && test -w /cache/npm && echo ok"
    result = subprocess.run(
        ["docker", "run", "--rm", tag, "sh", "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("ok")
