"""
Сбор контекста для промптов: всё сжимается до сигнатур и выжимок, чтобы экономить токены.
Ничего не знает о домене кейса — читает то, что лежит в artifacts/ и в каркасе.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from specforge_mobile.tools.android_gate import screens as screens_mod

DEFAULT_STATES = screens_mod.DEFAULT_STATES
REQ_TAG = re.compile(r"\b(?:AC-)?N?FR-\d+(?:-\d+)?\b")
DECL = re.compile(r"^\s*(?:@Composable\s+)?(?:(?:public|internal|data|sealed|enum|abstract|open|suspend|override|"
                  r"inline|const|operator|value)\s+)*(?:class|interface|object|fun|val|var|typealias)\b")
FEATURE_MAIN = "app/src/main/kotlin/com/specforge/app/feature"
FEATURE_TEST = "app/src/test/kotlin/com/specforge/app/feature"


# ---------------------------------------------------------------- UX-спека
def load_screen_spec(input_dir: Path, explicit: Path | None = None) -> tuple[dict | list, list[dict]]:
    """Возвращает (сырой JSON спеки, нормализованный список экранов [{screenId, states, title}])."""
    candidates = [explicit] if explicit else [
        input_dir / "03_ux/screen-spec.android.json",
        input_dir / "03_ux/screen-spec.json",
    ]
    path = next((p for p in candidates if p and p.is_file()), None)
    if path is None:
        raise FileNotFoundError("Нет UX-спеки: ожидается 03_ux/screen-spec.android.json")
    return screens_mod.load_spec(path)  # тот же разбор, что у генератора скриншот-тестов


# ---------------------------------------------------------------- требования
def requirements_digest(input_dir: Path, limit: int = 7000) -> str:
    """Выжимка требований: целиком, если влезает, иначе строки с тегами FR/NFR/AC и заголовки."""
    root = input_dir / "01_requirements"
    if not root.is_dir():
        return "(требования не переданы)"
    files = sorted(p for p in root.rglob("*") if p.suffix in {".md", ".feature", ".yaml", ".yml", ".json", ".txt"})
    full = "\n\n".join(f"### {p.relative_to(root).as_posix()}\n{p.read_text(encoding='utf-8', errors='replace')}"
                       for p in files)
    if len(full) <= limit:
        return full or "(требования пусты)"
    picked = [ln.strip() for ln in full.splitlines()
              if REQ_TAG.search(ln) or ln.lstrip().startswith(("#", "Feature:", "Функция:", "Scenario", "Сценарий"))]
    return "\n".join(picked)[:limit]


# ---------------------------------------------------------------- Kotlin
def kotlin_outline(text: str, max_cont: int = 14) -> str:
    """Публичные объявления файла (без тел): package, class/fun/val с параметрами конструктора."""
    out, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("package "):
            out.append(ln)
        elif DECL.match(ln) and not re.match(r"^\s*private\b", ln):
            block = [ln.rstrip().rstrip("{").rstrip()]
            depth = ln.count("(") - ln.count(")")
            j = i
            while depth > 0 and j + 1 < len(lines) and len(block) < max_cont:
                j += 1
                block.append(lines[j].rstrip())
                depth += lines[j].count("(") - lines[j].count(")")
            out.extend(re.sub(r"@SerialName\([^)]*\)\s*|@Contextual\s*", "", b) for b in block)
            i = j
        i += 1
    return "\n".join(out)


def find_generated_dir(project: Path) -> Path | None:
    hits = sorted(project.glob("app/src/main/kotlin/**/core/network/generated"))
    return hits[0] if hits else None


def package_of(path: Path, project: Path) -> str:
    rel = path.relative_to(project / "app/src/main/kotlin")
    return ".".join(rel.parts)


def api_digest(project: Path, limit: int = 12000) -> str:
    """Сигнатуры сгенерированного клиента: эндпоинты (метод, путь, смысл) и модели."""
    gen = find_generated_dir(project)
    if gen is None:
        return "(клиент API не найден)"
    out = [f"Пакет клиента: {package_of(gen, project)}",
           "Сервисы создаются так: ApiClient(baseUrl = ..., okHttpClientBuilder = okHttp.newBuilder())"
           ".createService(XxxApi::class.java)"]
    for f in sorted((gen / "apis").glob("*.kt")):
        out.append(f"\ninterface {f.stem}:")
        lines = f.read_text(encoding="utf-8").splitlines()
        summary = ""
        for k, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith("* ") and not summary and k > 0 and lines[k - 1].strip().startswith("* ") \
                    and re.match(r"\*\s+(GET|POST|PUT|PATCH|DELETE)\b", lines[k - 1].strip()):
                summary = s[2:]
            if s.startswith("suspend fun"):
                out.append(f"  {s}" + (f"   // {summary}" if summary else ""))
                summary = ""
    out.append("\nМодели (поля конструктора):")
    for f in sorted((gen / "models").glob("*.kt")):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"^\s*(data class|class|enum class)\s+(\w+)", text, re.M):
            kind, name = m[1], m[2]
            if kind == "enum class":
                body = text[m.end():]
                entries = re.findall(r'^\s*(?:@SerialName\([^)]*\)\s*)?(\w+)\("', body[:body.find(";")], re.M)
                out.append(f"enum {name}: {', '.join(entries)}")
            elif kind == "data class":
                params = text[m.end():]
                params = params[params.find("(") + 1:]
                depth, end = 1, 0
                for end, ch in enumerate(params):
                    depth += (ch == "(") - (ch == ")")
                    if depth == 0:
                        break
                fields = re.findall(r"\bval\s+(\w+)\s*:\s*([^,\n]+?)\s*(?:=\s*[^,\n]+)?\s*(?:,|$)", params[:end], re.M)
                out.append(f"data class {name}(" + ", ".join(f"{n}: {t}" for n, t in fields) + ")")
    return "\n".join(out)[:limit]


def core_digest(project: Path) -> str:
    """Публичный API ядра (core/), которым обязан пользоваться агент."""
    core = project / "app/src/main/kotlin/com/specforge/app/core"
    parts = []
    for f in sorted(core.rglob("*.kt")):
        o = kotlin_outline(f.read_text(encoding="utf-8"))
        if o.strip():
            parts.append(f"// {f.relative_to(core).as_posix()}\n{o}")
    return "\n\n".join(parts)


def libraries(project: Path) -> str:
    toml = (project / "gradle/libs.versions.toml").read_text(encoding="utf-8")
    m = re.search(r"\[libraries\](.*?)(?:\n\[|\Z)", toml, re.S)
    names = re.findall(r"^([\w-]+)\s*=", m[1], re.M) if m else []
    return ", ".join(names)


def read_files(root: Path, rels: list[str]) -> str:
    """Файлы целиком в формате блоков FILE — как образец или для ремонта."""
    out = []
    for r in rels:
        p = root / r
        if p.is_file():
            out.append(f"<<<FILE {r}>>>\n{p.read_text(encoding='utf-8')}\n<<<END>>>")
    return "\n\n".join(out)


def feature_outline(project: Path) -> str:
    """Сигнатуры всего, что агент уже сгенерировал в feature/ (для следующих шагов и ремонта)."""
    parts = []
    for base in (FEATURE_MAIN, FEATURE_TEST):
        for f in sorted((project / base).rglob("*.kt")):
            parts.append(f"// {f.relative_to(project).as_posix()}\n{kotlin_outline(f.read_text(encoding='utf-8'))}")
    return "\n\n".join(parts) or "(пока пусто)"
