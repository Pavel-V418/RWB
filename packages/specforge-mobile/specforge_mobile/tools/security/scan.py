"""
Запуск сканеров. Отсутствующий сканер пропускается (с пометкой), контур не падает.
Сырые отчёты → <out>/raw/: semgrep.sarif, gitleaks.sarif, trivy-fs.sarif, trivy-image-<имя>.sarif.
ZAP запускается в CI против стенда (нужен Docker и $STAND_URL) и кладёт raw/zap.json — здесь только читается.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

RULES_DIR = Path(__file__).parent / "semgrep-rules"
EXCLUDE = [".venv", "venv", "node_modules", "build", ".gradle", ".idea", "team_inputs", "artifacts/07_tests",
           "__pycache__", ".git", "dist", "bin", "obj",
           "07_tests", "llm"]  # в платформе цель — дерево артефактов: свои отчёты и сырые ответы модели не сканируем


def _run(cmd: list[str], timeout: int = 900) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)[-2000:]
    except subprocess.TimeoutExpired:
        return 124, f"таймаут {timeout} с"
    except OSError as e:
        return 127, str(e)


def semgrep(target: Path, raw: Path) -> str:
    exe = shutil.which("semgrep")
    if not exe:
        return "semgrep: не установлен — пропущен"
    configs = os.environ.get("SEMGREP_CONFIG", "p/default").split(",")
    cmd = [exe, "scan", "--sarif", "-o", str(raw / "semgrep.sarif"), "--metrics=off", "--quiet", "--timeout", "30"]
    for c in configs + ([str(RULES_DIR)] if RULES_DIR.is_dir() else []):
        cmd += ["--config", c.strip()]
    for e in EXCLUDE:
        cmd += ["--exclude", e]
    code, out = _run(cmd + [str(target)])
    return f"semgrep: код {code}" + ("" if code in (0, 1) else f" — {out[-300:]}")


def gitleaks(target: Path, raw: Path) -> str:
    exe = shutil.which("gitleaks")
    if not exe:
        return "gitleaks: не установлен — пропущен"
    report = raw / "gitleaks.sarif"
    cfg = Path(__file__).parent / "gitleaks.toml"
    common = ["--report-format", "sarif", "--report-path", str(report), "--redact", "--no-banner", "--exit-code", "0"]
    if cfg.is_file():
        common += ["--config", str(cfg)]
    code, out = _run([exe, "dir", str(target), *common])
    if code != 0 and "unknown command" in out:  # gitleaks < 8.19
        code, out = _run([exe, "detect", "--no-git", "--source", str(target), *common])
    return f"gitleaks: код {code}" + ("" if code == 0 else f" — {out[-300:]}")


def trivy_fs(target: Path, raw: Path) -> str:
    exe = shutil.which("trivy")
    if not exe:
        return "trivy fs: не установлен — пропущен"
    cmd = [exe, "fs", "--scanners", "vuln,misconfig", "--format", "sarif", "-o", str(raw / "trivy-fs.sarif"),
           "--exit-code", "0", "--quiet"]
    for e in EXCLUDE:
        cmd += ["--skip-dirs", e]
    code, out = _run(cmd + [str(target)], timeout=1800)
    return f"trivy fs: код {code}" + ("" if code == 0 else f" — {out[-300:]}")


def trivy_images(images: list[str], raw: Path) -> list[str]:
    exe = shutil.which("trivy")
    notes = []
    for img in images:
        if not exe:
            notes.append(f"trivy image {img}: trivy не установлен — пропущен")
            continue
        name = re.sub(r"[^\w.-]+", "_", img)[:60]
        code, out = _run([exe, "image", "--format", "sarif", "-o", str(raw / f"trivy-image-{name}.sarif"),
                          "--exit-code", "0", "--quiet", img], timeout=1800)
        notes.append(f"trivy image {img}: код {code}" + ("" if code == 0 else f" — {out[-300:]}"))
    return notes


def run_all(target: Path, raw: Path, tools: set[str], images: list[str]) -> list[str]:
    raw.mkdir(parents=True, exist_ok=True)
    notes = []
    if "semgrep" in tools:
        notes.append(semgrep(target, raw))
    if "gitleaks" in tools:
        notes.append(gitleaks(target, raw))
    if "trivy" in tools:
        notes.append(trivy_fs(target, raw))
        notes += trivy_images(images, raw)
    return notes
