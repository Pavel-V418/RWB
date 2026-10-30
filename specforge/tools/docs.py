"""Документы: текст требований из .md / .txt / .pdf / .docx и поиск по документации (RAG).

- `extract_text` — детерминированно, без LLM;
- `search_docs(query, tech?)` — топ-5 фрагментов из data/docs.db с источником, не больше
  SEARCH_TOKEN_LIMIT токенов; инструмент агента `search_docs`.
"""

from pathlib import Path

from specforge.llm import ToolSpec
from specforge.rag.store import DocStore, default_db_path
from specforge.tools.base import Args, Tool, ToolEnv, ToolError, ToolResult, arg_str

SEARCH_TOP_K = 5
SEARCH_TOKEN_LIMIT = 1500
CHARS_PER_TOKEN = 4
"""Та же оценка, что в compaction.estimate_tokens."""

SUPPORTED_SUFFIXES = (".md", ".txt", ".pdf", ".docx")


class DocumentError(ValueError):
    pass


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".md", ".txt"):
        return path.read_text(encoding="utf-8")
    if suffix == ".pdf":
        return _pdf_text(path)
    if suffix == ".docx":
        return _docx_text(path)
    raise DocumentError(
        f"{path.name}: формат {suffix or '(без расширения)'} не поддерживается; "
        f"ожидается {', '.join(SUPPORTED_SUFFIXES)}"
    )


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader  # noqa: PLC0415 — тяжёлый импорт только для pdf
    from pypdf.errors import PdfReadError  # noqa: PLC0415

    try:
        reader = PdfReader(path)
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, OSError) as exc:
        raise DocumentError(f"{path.name}: не читается как PDF: {exc}") from exc
    return "\n\n".join(p.strip() for p in pages if p.strip())


def _docx_text(path: Path) -> str:
    import docx  # noqa: PLC0415 — тяжёлый импорт только для docx

    try:
        document = docx.Document(str(path))
    except Exception as exc:  # python-docx бросает разные ошибки для битых файлов
        raise DocumentError(f"{path.name}: не читается как DOCX: {exc}") from exc
    blocks: list[str] = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                blocks.append(" | ".join(cells))
    return "\n\n".join(blocks)


# --- поиск по документации ----------------------------------------------------------------------

_stores: dict[Path, DocStore] = {}


def _store(path: Path | None) -> DocStore:
    key = (path or default_db_path()).resolve()
    if key not in _stores:
        if not key.is_file():
            raise ToolError(
                f"индекс документации {key} не найден: infra/fetch_docs.sh и "
                "python -m specforge.rag.ingest"
            )
        _stores[key] = DocStore(key)
    return _stores[key]


def search_docs(
    query: str,
    tech: str | None = None,
    *,
    db_path: Path | None = None,
    limit_tokens: int = SEARCH_TOKEN_LIMIT,
) -> str:
    """Топ-5 фрагментов документации с источником, всего не больше limit_tokens токенов."""
    if not query.strip():
        raise ToolError("пустой запрос")
    hits = _store(db_path).search(query, tech=tech or None, limit=SEARCH_TOP_K)
    if not hits:
        where = f" в {tech}" if tech else ""
        return f"Ничего не найдено{where} по запросу {query!r}. Переформулируйте короче."
    budget = limit_tokens * CHARS_PER_TOKEN
    parts: list[str] = []
    for i, hit in enumerate(hits, 1):
        header = f"[{i}] {hit.tech} · {hit.section} · источник: {hit.source}\n"
        room = max(budget - sum(len(p) + 2 for p in parts) - len(header), 0)
        if parts and room < 200:  # первый фрагмент — всегда, хотя бы усечённый
            break
        body = hit.text if len(hit.text) <= room else hit.text[: room - 1] + "…"
        parts.append(header + body)
    return "\n\n".join(parts)


def _search_docs_tool(env: ToolEnv, args: Args) -> ToolResult:
    del env  # поиск не трогает песочницу
    query = arg_str(args, "query")
    tech = args.get("tech")
    if tech is not None and not isinstance(tech, str):
        raise ToolError("аргумент 'tech' должен быть строкой")
    content = search_docs(query, tech)
    return ToolResult(content=content, stub=f"[search_docs {query!r}: {len(content)} симв.]")


SEARCH_DOCS = Tool(
    spec=ToolSpec(
        name="search_docs",
        description=(
            "Поиск по документации технологий (BM25). Возвращает до 5 фрагментов с источником. "
            "Запрос — ключевые слова, можно по-русски или по-английски."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Ключевые слова"},
                "tech": {
                    "type": "string",
                    "description": "Технология (каталог корпуса): angular, dotnet, postgis, "
                    "kafka, android, org-standards, …",
                },
            },
            "required": ["query"],
        },
    ),
    fn=_search_docs_tool,
)
