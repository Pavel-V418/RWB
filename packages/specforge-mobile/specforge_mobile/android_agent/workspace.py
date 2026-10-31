"""
Рабочая копия Android-проекта: подготовка из каркаса, запись файлов от LLM с проверкой путей,
откат замороженных файлов. Решения здесь детерминированные — без LLM.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from .context import FEATURE_MAIN, FEATURE_TEST

ALLOWED = [
    re.compile(rf"^{re.escape(FEATURE_MAIN)}/[\w/]+\.kt$"),
    re.compile(rf"^{re.escape(FEATURE_TEST)}/[\w/]+\.kt$"),
    re.compile(r"^app/src/main/res/values/strings(_\w+)?\.xml$"),
]
COPY_IGNORE = shutil.ignore_patterns("build", ".gradle", ".idea", "*.iml", "screenshots", "gate_report.json",
                                     ".kotlin", "captures")
FILE_BLOCK = re.compile(r"<<<FILE\s+([^>\n]+?)\s*>>>[ \t]*\n(.*?)\n<<<END>>>", re.S)
FENCE = re.compile(r"^\s*```[\w-]*\s*\n(.*?)\n\s*```\s*$", re.S)
EMPTY_STRINGS = ('<?xml version="1.0" encoding="utf-8"?>\n'
                 '<!-- Общие тексты. Тексты экранов — в strings_<screenId>.xml. -->\n<resources>\n</resources>\n')


def prepare(template: Path, project: Path, api_client: Path | None = None) -> None:
    """Копия каркаса без feature/ — агент пишет слой экранов с нуля."""
    if project.exists():
        shutil.rmtree(project)
    shutil.copytree(template, project, ignore=COPY_IGNORE)
    for rel in (FEATURE_MAIN, FEATURE_TEST):
        shutil.rmtree(project / rel, ignore_errors=True)
        (project / rel).mkdir(parents=True)
    # скриншот-тест эталона — от его спеки; для нового кейса генерируется заново перед шлюзом
    shutil.rmtree(project / "app/src/test/kotlin/com/specforge/app/generated", ignore_errors=True)
    values = project / "app/src/main/res/values"
    for f in values.glob("strings*.xml"):
        f.unlink()
    (values / "strings.xml").write_text(EMPTY_STRINGS, encoding="utf-8")
    if api_client:  # клиент другого кейса от участника 3 — заменяет клиент каркаса
        for old in project.glob("app/src/main/kotlin/**/core/network/generated"):
            shutil.rmtree(old)
        dest = _client_dest(api_client, project)
        shutil.copytree(api_client, dest)


def _client_dest(api_client: Path, project: Path) -> Path:
    """Пакет клиента берём из первого .kt (package ...core.network.generated...)."""
    for f in api_client.rglob("*.kt"):
        m = re.search(r"^package\s+([\w.]+?\.core\.network\.generated)\b", f.read_text(encoding="utf-8"), re.M)
        if m:
            return project / "app/src/main/kotlin" / Path(*m[1].split("."))
    raise ValueError(f"В {api_client} не найден пакет *.core.network.generated")


def parse_files(text: str, existing: set[str] | None = None) -> tuple[dict[str, str], list[str]]:
    """Блоки <<<FILE path>>> ... <<<END>>> → {path: content}. Второе значение — предупреждения."""
    files, warnings = {}, []
    for path, body in FILE_BLOCK.findall(text):
        path = path.strip().strip("`").replace("\\", "/")
        path = re.sub(r"^(\./|android/)", "", path)
        if m := FENCE.match(body):
            body = m[1]
        files[path] = body.rstrip() + "\n"
    opened = len(re.findall(r"<<<FILE\s", text))
    if opened > len(files):
        warnings.append(f"{opened - len(files)} блок(ов) FILE без <<<END>>> — ответ обрезан, файл(ы) пропущены")
    if not files:  # модель забыла обёртку FILE — пробуем ```kotlin блоки с package
        files = infer_files(text, existing)
        if files:
            warnings.append(f"нет блоков FILE, пути выведены из package: {list(files)}")
    return files, warnings


KOTLIN_BLOCK = re.compile(r"```(?:kotlin|kt)?[ \t]*\n(.*?)\n```", re.S)
TOP_DECL = re.compile(r"^(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:data|sealed|abstract|open|enum|internal)\s+)*"
                      r"(?:class|interface|object)\s+(\w+)", re.M)


def infer_files(text: str, existing: set[str] | None = None) -> dict[str, str]:
    """
    Путь Kotlin-файла по package и объявлению; тесты (@Test) — в src/test.
    Если одно из объявлений совпадает с уже существующим файлом — пишем в него (без дубликатов).
    """
    blocks = KOTLIN_BLOCK.findall(text) or ([text] if text.lstrip().startswith("package ") else [])
    files = {}
    for body in blocks:
        pkg = re.search(r"^package\s+([\w.]+)", body, re.M)
        names = TOP_DECL.findall(body)
        if not pkg or not names:
            continue
        src = "test" if re.search(r"@Test\b", body) else "main"
        paths = [f"app/src/{src}/kotlin/{pkg[1].replace('.', '/')}/{n}.kt" for n in names]
        path = next((p for p in paths if existing and p in existing), paths[0])
        files[path] = body.rstrip() + "\n"
    return files


def is_allowed(path: str) -> bool:
    return ".." not in path and any(p.match(path) for p in ALLOWED)


def write_files(project: Path, files: dict[str, str]) -> tuple[list[str], list[str]]:
    """Пишет только разрешённые контрактом пути. Возвращает (записанные, отклонённые)."""
    written, rejected = [], []
    for path, body in files.items():
        if not is_allowed(path):
            rejected.append(path)
            continue
        dest = project / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")
        written.append(path)
    return written, rejected


def restore_frozen(template: Path, project: Path) -> list[str]:
    """Откатывает изменённые замороженные файлы к версии каркаса. Возвращает список откатанных."""
    manifest = project / "frozen.sha256"
    restored = []
    if not manifest.is_file():
        return restored
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        digest, rel = raw.split(maxsplit=1)
        f = project / rel
        actual = hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else None
        if actual != digest and (template / rel).is_file():
            f.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(template / rel, f)
            restored.append(rel)
    return restored


def clean_outputs(project: Path) -> None:
    """Старые результаты тестов и скриншоты не должны попасть в отчёт новой попытки."""
    shutil.rmtree(project / "app/build/test-results", ignore_errors=True)
    shutil.rmtree(project / "screenshots", ignore_errors=True)
