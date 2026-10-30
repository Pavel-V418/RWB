"""Индексация корпуса документации: infra/docs-corpus/<tech>/**/*.md|*.html → data/docs.db.

Чанки режутся по заголовкам; блок кода (``` или <pre>) всегда целиком в одном чанке, даже если
чанк из-за него выходит длиннее MAX_CHUNK_CHARS. Метаданные: tech (каталог), раздел (путь
заголовков), источник (путь файла). Шаблоны организаторов — infra/docs-corpus/org-standards/,
tech="org-standards".

    uv run python -m specforge.rag.ingest [корпус] [--db data/docs.db]
"""

import argparse
import re
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from specforge.paths import PROJECT_ROOT
from specforge.rag.embeddings import Embedder, describe, embedder_from_env
from specforge.rag.search import index_vectors
from specforge.rag.store import DocChunk, DocStore, default_db_path

DEFAULT_CORPUS = PROJECT_ROOT / "infra" / "docs-corpus"
SUFFIXES = (".md", ".markdown", ".html", ".htm")
MAX_CHUNK_CHARS = 2400
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")


def ingest_corpus(
    corpus: Path = DEFAULT_CORPUS,
    store: DocStore | None = None,
    embedder: Embedder | None = None,
) -> int:
    """Пересобрать индекс по корпусу; с эмбеддером — и векторы (только изменённых чанков).
    Возвращает число чанков."""
    store = store or DocStore()
    count = store.replace_all(iter_chunks(corpus))
    if embedder is not None:
        index_vectors(store, embedder)
    return count


def iter_chunks(corpus: Path) -> Iterator[DocChunk]:
    if not corpus.is_dir():
        raise FileNotFoundError(f"нет корпуса {corpus} — запустите infra/fetch_docs.sh")
    techs = (p for p in corpus.iterdir() if p.is_dir() and not p.name.startswith("."))
    for tech_dir in sorted(techs):  # .src — кэш клонов fetch_docs.sh
        for path in sorted(tech_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUFFIXES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if path.suffix.lower() in (".html", ".htm"):
                text = html_to_markdown(text)
            source = path.relative_to(corpus).as_posix()
            for section, body in chunk_markdown(text, default_title=path.stem):
                yield DocChunk(tech=tech_dir.name, section=section, source=source, text=body)


def chunk_markdown(text: str, default_title: str = "") -> list[tuple[str, str]]:
    """[(раздел, текст)]: новый чанк на каждом заголовке вне блока кода; длинный раздел
    делится по абзацам, но не внутри блока кода."""
    sections: list[tuple[str, list[str]]] = []
    path: list[str] = []
    current: list[str] = []
    in_code = False

    def flush() -> None:
        body = "\n".join(current).strip()
        if body:
            sections.append((" > ".join(path) or default_title, current.copy()))
        current.clear()

    for line in text.splitlines():
        if FENCE.match(line):
            in_code = not in_code
        heading = None if in_code else HEADING.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            path[:] = [*path[: level - 1], heading.group(2).strip()]
            continue
        current.append(line)
    flush()

    chunks: list[tuple[str, str]] = []
    for section, lines in sections:
        chunks += [(section, part) for part in _split_long(lines)]
    return chunks


def _split_long(lines: list[str]) -> list[str]:
    """Абзацы (и блоки кода целиком) складываются в части до MAX_CHUNK_CHARS."""
    blocks: list[str] = []
    block: list[str] = []
    in_code = False
    for line in lines:
        if FENCE.match(line):
            in_code = not in_code
        if not line.strip() and not in_code:
            if block:
                blocks.append("\n".join(block))
                block = []
            continue
        block.append(line)
    if block:
        blocks.append("\n".join(block))

    parts: list[str] = []
    current = ""
    for b in blocks:
        if current and len(current) + len(b) + 2 > MAX_CHUNK_CHARS:
            parts.append(current)
            current = b
        else:
            current = f"{current}\n\n{b}" if current else b
    if current:
        parts.append(current)
    return parts


class _HtmlToMarkdown(HTMLParser):
    """Минимальное HTML → Markdown: заголовки, абзацы, списки, <pre> как блок кода."""

    SKIP = frozenset({"script", "style", "nav", "header", "footer", "aside"})
    BLOCK = frozenset({"p", "div", "li", "tr", "br", "section", "article", "table", "ul", "ol"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.pre = 0
        self.heading: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "pre":
            self.pre += 1
            self.out.append("\n\n```\n")
        elif re.fullmatch(r"h[1-6]", tag):
            self.heading = int(tag[1])
            self.out.append("\n\n" + "#" * self.heading + " ")
        elif tag in self.BLOCK:
            self.out.append("\n\n" if tag != "li" else "\n- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag == "pre":
            self.pre = max(0, self.pre - 1)
            self.out.append("\n```\n\n")
        elif re.fullmatch(r"h[1-6]", tag):
            self.heading = None
            self.out.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self.pre:
            self.out.append(data)
        else:
            text = re.sub(r"\s+", " ", data)
            if self.heading is not None:
                text = text.strip()
            self.out.append(text)


def html_to_markdown(html: str) -> str:
    parser = _HtmlToMarkdown()
    parser.feed(html)
    parser.close()
    text = "".join(parser.out)
    lines = [line.rstrip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Индексация документации в SQLite FTS5.")
    parser.add_argument("corpus", nargs="?", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args(argv)
    load_dotenv(find_dotenv(usecwd=True))  # EMBED_* из .env
    store = DocStore(args.db or default_db_path())
    embedder = embedder_from_env()
    count = ingest_corpus(args.corpus, store, embedder)
    print(f"{count} чанков, tech: {', '.join(store.techs())} → {store.path}")
    print(f"эмбеддинги: {describe(embedder)}")


if __name__ == "__main__":
    main()
