"""Нормализация входа: md / txt / pdf / docx -> единый Markdown-текст + поиск цитат по строкам.

LLM получает текст без номеров строк (экономия токенов) и возвращает дословные цитаты.
Номер строки находит код: так ссылки на источник проверяемы и не зависят от модели.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

SUPPORTED = (".md", ".txt", ".pdf", ".docx")


def find_input(input_dir: str | Path) -> Path:
    d = Path(input_dir)
    if d.is_file():
        return d
    preferred = sorted(p for p in d.iterdir() if p.stem.lower().startswith("requirements")
                       and p.suffix.lower() in SUPPORTED)
    others = sorted(p for p in d.iterdir() if p.suffix.lower() in SUPPORTED)
    if preferred:
        return preferred[0]
    if others:
        return others[0]
    raise FileNotFoundError(f"в {d} нет файла требований ({', '.join(SUPPORTED)})")


def _read_pdf(path: Path) -> str:
    import pdfplumber

    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n\n".join(pages)


def _read_docx(path: Path) -> str:
    """Абзацы и таблицы в порядке документа: таблица остаётся рядом с текстом, который её вводит,
    и номера строк цитат из таблицы указывают на её настоящее место."""
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = docx.Document(str(path))
    out: list[str] = []

    def para(p: Paragraph) -> None:
        style = (p.style.name or "").lower() if p.style is not None else ""
        text = p.text.strip()
        if not text:
            out.append("")
        elif style.startswith("heading") or style.startswith("заголовок"):
            level = re.sub(r"\D", "", style) or "2"
            out.append("#" * min(int(level), 6) + " " + text)
        elif "list" in style or "спис" in style or p._p.pPr is not None and p._p.pPr.numPr is not None:
            out.append("- " + text)
        else:
            out.append(text)

    def table(t: Table) -> None:
        out.append("")
        for i, row in enumerate(t.rows):
            cells = [c.text.strip().replace("\n", " ").replace("|", "/") for c in row.cells]
            out.append("| " + " | ".join(cells) + " |")
            if i == 0:
                out.append("|" + " --- |" * len(cells))
        out.append("")

    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para(Paragraph(child, doc))
        elif tag == "tbl":
            table(Table(child, doc))
    return "\n".join(out)


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(" ", " ").replace("​", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)            # перенос слова в PDF
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"^[•●▪◦]\s*", "- ", text, flags=re.M)     # маркеры списков из PDF
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def load_requirements(path: str | Path) -> str:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in (".md", ".txt"):
        raw = p.read_text(encoding="utf-8")
    elif suffix == ".pdf":
        raw = _read_pdf(p)
    elif suffix == ".docx":
        raw = _read_docx(p)
    else:
        raise ValueError(f"неподдерживаемый формат: {suffix}")
    return clean_text(raw)


# --------------------------------------------------------------------------- поиск цитат

_PUNCT = re.compile(r"[«»\"'“”„()\[\]:;,.!?…—–\-•*_#|]")


def _norm(s: str) -> str:
    s = s.lower().replace("ё", "е")
    s = _PUNCT.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class QuoteMatch:
    line: int
    exact: bool
    score: float


class SourceIndex:
    """Ищет цитату в исходном тексте и возвращает номер строки (с 1)."""

    def __init__(self, text: str):
        self.lines = text.split("\n")
        self.norm_lines = [_norm(l) for l in self.lines]
        # склеенный текст + карта символ -> строка, чтобы находить цитаты через перенос строки
        parts, owners = [], []
        for i, nl in enumerate(self.norm_lines):
            if nl:
                parts.append(nl)
                owners.append(i)
        self._joined = ""
        self._starts: list[tuple[int, int]] = []
        for nl, owner in zip(parts, owners):
            self._starts.append((len(self._joined), owner))
            self._joined += nl + " "

    def _line_at(self, pos: int) -> int:
        line = self._starts[0][1] if self._starts else 0
        for start, owner in self._starts:
            if start > pos:
                break
            line = owner
        return line + 1

    def locate(self, quote: str | None) -> QuoteMatch | None:
        if not quote:
            return None
        q = _norm(quote)
        if len(q) < 3:
            return None
        pos = self._joined.find(q)
        if pos >= 0:
            return QuoteMatch(self._line_at(pos), True, 1.0)
        # нечёткий поиск: доля слов цитаты в окне из 3 строк. Числа (сроки, пороги, суммы) должны
        # совпасть все: «30 минут» вместо «10 минут» — это искажение требования, а не перефразировка.
        words = set(q.split())
        nums = {w for w in words if w.isdigit()}
        best = (0.0, -1)
        for i in range(len(self.norm_lines)):
            window = " ".join(self.norm_lines[i : i + 3])
            if not window:
                continue
            ws = set(window.split())
            if not nums <= ws:
                continue
            score = len(words & ws) / max(len(words), 1)
            if score > best[0]:
                best = (score, i)
        if best[0] >= 0.75:
            # строка окна, где совпадений больше всего (а не первая строка окна)
            i0 = best[1]
            line = max(range(i0, min(i0 + 3, len(self.norm_lines))),
                       key=lambda j: (len(words & set(self.norm_lines[j].split())), -j))
            return QuoteMatch(line + 1, False, round(best[0], 2))
        return None

    def numbered(self) -> str:
        return "\n".join(f"{i + 1:04d}| {l}" for i, l in enumerate(self.lines))
