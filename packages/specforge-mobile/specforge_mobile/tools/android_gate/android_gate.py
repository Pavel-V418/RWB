#!/usr/bin/env python3
"""
Шлюз сборки Android: собирает проект, прогоняет unit-тесты, пишет скриншоты Roborazzi
и возвращает GateResult в формате платформы (участник 1).

    python android_gate.py <android_project_dir> [--out gate_report.json] [--offline] [--skip-build]

GateResult:
  {passed, errors: [Diagnostic], tokens, attempts, durationSec, artifacts}
Diagnostic:
  {tool, file, line, column, severity, message, code?, category?, code_window?}

Категории ошибок:
  contract — изменён замороженный файл каркаса (агенту туда писать нельзя)
  compile  — ошибка компилятора Kotlin
  resource — ошибка ресурсов / манифеста (AAPT)
  test     — упавший unit-тест или скриншот-тест
  build    — прочие ошибки Gradle (зависимости, плагины, память)

Только стандартная библиотека Python 3.10+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlparse

GRADLE_TASKS = ["assembleDebug", "recordRoborazziDebug"]
FROZEN_MANIFEST = "frozen.sha256"
MAX_ERRORS = 30

# e: file:///C:/p/app/src/main/kotlin/A.kt:12:5 Unresolved reference 'x'.
KOTLIN_NEW = re.compile(r"^e: (file://\S+?):(\d+):(\d+) (.+)$")
# e: /p/app/src/main/kotlin/A.kt: (12, 5): Unresolved reference: x
KOTLIN_OLD = re.compile(r"^e: (.+?\.kts?): \((\d+), (\d+)\): (.+)$")
# ERROR: /p/app/src/main/res/layout/x.xml:12: AAPT: error: ...
AAPT = re.compile(r"^ERROR:\s*(.+?):(\d+):\s*AAPT: (?:error: )?(.+)$")
# /p/app/src/main/AndroidManifest.xml:10:5-40:19 Error:
MANIFEST = re.compile(r"^(.+AndroidManifest\.xml):(\d+):(\d+)(?:-\S+)? Error:\s*$")
# имя метода может содержать пробелы и кириллицу: `FR-1 повторное нажатие`
STACK_FRAME = re.compile(r"at ([\w.$]+)\.[^.(\n]+\(([\w.]+\.kt):(\d+)\)")


# ---------------------------------------------------------------- helpers
def rel(path: str, root: Path) -> str:
    if path.startswith("file:"):
        p = unquote(urlparse(path).path)
        if re.match(r"^/[A-Za-z]:/", p):          # file:///C:/... на Windows
            p = p[1:]
        path = p
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).as_posix()


def code_window(root: Path, file: str, line: int, radius: int = 3) -> str | None:
    f = root / file
    if not f.is_file() or line < 1:
        return None
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    lo, hi = max(1, line - radius), min(len(lines), line + radius)
    return "\n".join(f"{'>' if i == line else ' '}{i:4d} | {lines[i - 1]}" for i in range(lo, hi + 1))


def diag(tool: str, file: str, line: int, column: int, message: str, category: str, root: Path,
         code: str | None = None) -> dict:
    d = {"tool": tool, "file": file, "line": line, "column": column, "severity": "error",
         "message": message.strip(), "category": category}
    if code:
        d["code"] = code
    w = code_window(root, file, line)
    if w:
        d["code_window"] = w
    return d


# ---------------------------------------------------------------- парсеры
def parse_build_log(log: str, root: Path) -> list[dict]:
    out: list[dict] = []
    lines = log.splitlines()
    for i, raw in enumerate(lines):
        line = raw.rstrip()
        if m := KOTLIN_NEW.match(line):
            out.append(diag("kotlinc", rel(m[1], root), int(m[2]), int(m[3]), m[4], "compile", root))
        elif m := KOTLIN_OLD.match(line):
            out.append(diag("kotlinc", rel(m[1], root), int(m[2]), int(m[3]), m[4], "compile", root))
        elif m := AAPT.match(line):
            out.append(diag("aapt", rel(m[1], root), int(m[2]), 0, m[3], "resource", root))
        elif m := MANIFEST.match(line):
            msg = lines[i + 1].strip() if i + 1 < len(lines) else "ошибка манифеста"
            out.append(diag("manifest-merger", rel(m[1], root), int(m[2]), int(m[3]), msg, "resource", root))
    return dedupe(out)


def parse_what_went_wrong(log: str) -> str | None:
    """Блок «What went wrong» из Gradle — на случай ошибок без файла и строки."""
    m = re.search(r"\* What went wrong:\n(.+?)(?:\n\* Try:|\n\* Exception is:|\Z)", log, re.S)
    if not m:
        return None
    text = m[1].strip()
    return text[:1500]


def parse_junit(results_dir: Path, root: Path) -> tuple[list[dict], dict]:
    errors: list[dict] = []
    stats = {"tests": 0, "failures": 0, "skipped": 0}
    if not results_dir.is_dir():
        return errors, stats
    test_src = {p.name: p for p in root.glob("app/src/test/**/*.kt")}
    for xml_file in sorted(results_dir.rglob("TEST-*.xml")):
        try:
            suite = ET.parse(xml_file).getroot()
        except ET.ParseError:
            continue
        for case in suite.iter("testcase"):
            stats["tests"] += 1
            if case.find("skipped") is not None:
                stats["skipped"] += 1
            node = case.find("failure")
            if node is None:
                node = case.find("error")
            if node is None:
                continue
            stats["failures"] += 1
            cls = case.get("classname", "")
            name = case.get("name", "")
            body = (node.text or "") + "\n" + (node.get("message") or "")
            file, line = locate_frame(body, cls, test_src, root)
            first = (node.get("message") or body.strip().splitlines()[0] if body.strip() else "тест упал")[:500]
            errors.append(diag("junit", file, line, 0, f"{cls.split('.')[-1]} > {name}: {first}", "test", root,
                               code=node.get("type")))
    return errors, stats


def locate_frame(trace: str, cls: str, test_src: dict, root: Path) -> tuple[str, int]:
    """Сначала кадр стека из тестового класса, затем любой кадр из исходников проекта."""
    frames = STACK_FRAME.findall(trace)
    simple = cls.split(".")[-1]
    for frame_cls, fname, ln in frames:
        if frame_cls.split("$")[0] == cls and fname in test_src:
            return test_src[fname].relative_to(root).as_posix(), int(ln)
    all_src = {p.name: p for p in root.glob("app/src/**/*.kt")}
    for frame_cls, fname, ln in frames:
        if frame_cls.startswith("com.specforge") and fname in all_src:
            return all_src[fname].relative_to(root).as_posix(), int(ln)
    guess = test_src.get(f"{simple}.kt")
    return (guess.relative_to(root).as_posix() if guess else f"app/src/test/{simple}.kt"), 0


def dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for d in items:
        key = (d["file"], d["line"], d["column"], d["message"])
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


# ---------------------------------------------------------------- контракт
def frozen_violations(root: Path) -> list[dict]:
    manifest = root / FROZEN_MANIFEST
    if not manifest.is_file():
        return []
    out = []
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        digest, path = raw.split(maxsplit=1)
        f = root / path
        actual = hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else "missing"
        if actual != digest:
            out.append({"tool": "gate", "file": path, "line": 0, "column": 0, "severity": "error",
                        "category": "contract",
                        "message": "Файл каркаса заморожен и не должен меняться агентом. Верни исходную версию "
                                   "и реши задачу в feature/."})
    return out


def write_frozen_manifest(root: Path, patterns: list[str]) -> int:
    files = sorted({p for pat in patterns for p in root.glob(pat) if p.is_file()})
    lines = ["# sha256 замороженных файлов каркаса. Пересоздать: android_gate.py <dir> --freeze"]
    for f in files:
        lines.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(root).as_posix()}")
    (root / FROZEN_MANIFEST).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(files)


FROZEN_PATTERNS = [
    "settings.gradle.kts", "build.gradle.kts", "gradle/libs.versions.toml", "app/build.gradle.kts",
    "app/src/main/AndroidManifest.xml", "app/src/main/res/xml/*.xml",
    "app/src/main/kotlin/com/specforge/app/App.kt", "app/src/main/kotlin/com/specforge/app/MainActivity.kt",
    "app/src/main/kotlin/com/specforge/app/core/**/*.kt",
]


# ---------------------------------------------------------------- запуск
def run_gradle(root: Path, offline: bool, timeout: int) -> tuple[int, str]:
    wrapper = root / ("gradlew.bat" if os.name == "nt" else "gradlew")
    cmd = [str(wrapper), *GRADLE_TASKS, "--console=plain", "--continue", "--stacktrace"]
    if offline:
        cmd.append("--offline")
    if os.name != "nt":
        wrapper.chmod(wrapper.stat().st_mode | 0o111)
    try:
        p = subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return p.returncode, p.stdout + "\n" + p.stderr
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        return 124, out + f"\nGATE: превышен таймаут {timeout} с"


def evaluate(root: Path, exit_code: int, log: str) -> dict:
    errors = frozen_violations(root)
    errors += parse_build_log(log, root)
    test_errors, stats = parse_junit(root / "app/build/test-results", root)
    errors += test_errors
    if exit_code != 0 and not errors:
        wrong = parse_what_went_wrong(log) or log.strip()[-1500:] or "Gradle завершился с ошибкой"
        category = "build"
        if "OutOfMemoryError" in log or "Java heap space" in log:
            wrong = "Не хватило памяти Gradle (OutOfMemoryError). " + wrong
        errors.append({"tool": "gradle", "file": "", "line": 0, "column": 0, "severity": "error",
                       "category": category, "message": wrong})
    shots = sorted((root / "screenshots").glob("*.png")) if (root / "screenshots").is_dir() else []
    apk = next(iter(sorted((root / "app/build/outputs/apk/debug").glob("*.apk"))), None) \
        if (root / "app/build/outputs/apk/debug").is_dir() else None
    return {
        "passed": exit_code == 0 and not errors,
        "errors": errors[:MAX_ERRORS],
        "errorsTotal": len(errors),
        "tokens": 0,
        "attempts": 1,
        "tests": stats,
        "artifacts": {
            "apk": apk.relative_to(root).as_posix() if apk else None,
            "screenshots": [s.relative_to(root).as_posix() for s in shots],
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", type=Path)
    ap.add_argument("--out", type=Path, default=None, help="куда записать gate_report.json")
    ap.add_argument("--offline", action="store_true", help="gradle --offline (образ с прогретым кэшем)")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--skip-build", action="store_true", help="не запускать gradle, разобрать готовый лог")
    ap.add_argument("--log", type=Path, help="лог для --skip-build")
    ap.add_argument("--freeze", action="store_true", help="записать frozen.sha256 по текущему каркасу и выйти")
    args = ap.parse_args()
    root = args.project.resolve()

    if args.freeze:
        n = write_frozen_manifest(root, FROZEN_PATTERNS)
        print(f"заморожено файлов: {n}")
        return

    t0 = time.monotonic()
    if args.skip_build:
        log = args.log.read_text(encoding="utf-8", errors="replace") if args.log else ""
        code = 1 if re.search(r"BUILD FAILED|^e: ", log, re.M) else 0
    else:
        code, log = run_gradle(root, args.offline, args.timeout)
        (root / "build").mkdir(exist_ok=True)
        (root / "build/gate.log").write_text(log, encoding="utf-8")

    result = evaluate(root, code, log)
    result["durationSec"] = round(time.monotonic() - t0, 1)
    out = args.out or (root / "gate_report.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    status = "PASSED" if result["passed"] else f"FAILED ({result['errorsTotal']} ошибок)"
    print(f"android gate: {status}; тестов {result['tests']['tests']}, "
          f"скриншотов {len(result['artifacts']['screenshots'])}, {result['durationSec']} с → {out}")
    for e in result["errors"][:10]:
        loc = f"{e['file']}:{e['line']}" if e["file"] else "(без файла)"
        print(f"  [{e['category']}] {loc}  {e['message'][:160]}")
    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
