#!/usr/bin/env python3
"""template-check: быстрая проверка Angular-шаблонов без компилятора Angular.

  TPL_UNBALANCED_TAG     незакрытый / лишний закрывающий тег
  TPL_UNBALANCED_BLOCK   несбалансированные блоки @if/@for/@switch { … }
  TPL_UNKNOWN_MEMBER     выражение ссылается на имя, которого нет в классе компонента
                         (включая унаследованные от ScreenBase) и в локальных переменных шаблона

Не заменяет `ng build`, но ловит ошибки генератора и LLM-слотов за миллисекунды,
до дорогой сборки — и даёт ремонтнику точное место.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
JS_NAMES = {"true", "false", "null", "undefined", "this", "$event", "$any", "$index", "$first", "$last", "$even", "$odd",
            "$count", "String", "Number", "Math", "JSON", "Array", "Object", "Boolean", "Date", "typeof", "in", "of", "as",
            "track", "let", "else", "if", "void", "new"}
RE_MEMBER = re.compile(r"^\s*(?:protected|public|private)?\s*(?:override\s+)?(?:readonly\s+)?(?:get\s+)?([A-Za-z_$][\w$]*)\s*(?:[=(:<!?])", re.M)
RE_IDENT = re.compile(r"(?<![\w$.'\"?])(?<!\?\.)([A-Za-z_$][\w$]*)")
RE_STRING = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`[^`]*`")


@dataclass
class Finding:
    file: str
    line: int
    code: str
    message: str
    severity: str = "error"


def class_members(ts: str, extra: set[str]) -> set[str]:
    m = re.search(r"export\s+(?:abstract\s+)?class", ts)
    body = ts[m.start():] if m else ts
    names = set(RE_MEMBER.findall(body)) | extra
    return names


class _Scanner(HTMLParser):
    def __init__(self, file: str, known: set[str], out: list[Finding]):
        super().__init__(convert_charrefs=False)
        self.file, self.known, self.out = file, known, out
        self.stack: list[tuple[str, int]] = []
        self.locals: list[set[str]] = [set()]

    def _scope(self) -> set[str]:
        s: set[str] = set()
        for x in self.locals:
            s |= x
        return s

    def check_expr(self, expr: str, line: int) -> None:
        clean = RE_STRING.sub("''", expr)
        clean = re.sub(r"\{\s*[A-Za-z_$][\w$]*\s*:", "{", clean)  # ключи объектных литералов
        clean = re.sub(r",\s*[A-Za-z_$][\w$]*\s*:", ",", clean)
        clean = re.sub(r"\(\s*([A-Za-z_$][\w$]*)\s*\)\s*=>", "", clean)
        for name in RE_IDENT.findall(clean):
            if name in JS_NAMES or name in self.known or name in self._scope():
                continue
            self.out.append(Finding(self.file, line, "TPL_UNKNOWN_MEMBER", f"'{name}' не объявлен в компоненте (выражение: {expr.strip()[:80]})"))

    def handle_startendtag(self, tag, attrs):
        self._attrs(attrs, self.getpos()[0])

    def _attrs(self, attrs, line: int) -> set[str]:
        local: set[str] = set()
        for k, v in attrs:
            if k.startswith("let-"):
                local.add(k[4:])
            elif k.startswith("#"):
                self.known.add(k[1:])
            elif v is not None and (k.startswith("[") or k.startswith("(") or k.startswith("*")):
                self.check_expr(v, line)
        return local

    def handle_starttag(self, tag, attrs):
        line = self.getpos()[0]
        local = self._attrs(attrs, line)
        if tag not in VOID:
            self.stack.append((tag, line))
            self.locals.append(local)

    def handle_endtag(self, tag):
        line = self.getpos()[0]
        if tag in VOID:
            return
        if not self.stack or self.stack[-1][0] != tag:
            open_tags = [t for t, _ in self.stack]
            if tag in open_tags:
                while self.stack and self.stack[-1][0] != tag:
                    t, l = self.stack.pop()
                    self.locals.pop()
                    self.out.append(Finding(self.file, l, "TPL_UNBALANCED_TAG", f"<{t}> не закрыт"))
            else:
                self.out.append(Finding(self.file, line, "TPL_UNBALANCED_TAG", f"лишний </{tag}>"))
                return
        self.stack.pop()
        self.locals.pop()

    def handle_data(self, data):
        line = self.getpos()[0]
        for m in re.finditer(r"\{\{(.*?)\}\}", data, re.S):
            self.check_expr(m.group(1), line + data[: m.start()].count("\n"))
        for m in re.finditer(r"@(?:if|else if|switch|case)\s*\((.*?)\)\s*\{", data, re.S):
            expr = m.group(1)
            alias = re.search(r";\s*as\s+([A-Za-z_$][\w$]*)", expr)
            if alias:
                self.locals[-1].add(alias.group(1))
                expr = expr[: alias.start()]
            self.check_expr(expr, line + data[: m.start()].count("\n"))
        for m in re.finditer(r"@for\s*\(\s*([A-Za-z_$][\w$]*)\s+of\s+(.*?);\s*track\s+(.*?)\)\s*\{", data, re.S):
            self.locals[-1].add(m.group(1))
            self.check_expr(m.group(2), line)


def block_balance(html: str) -> int:
    """Баланс { } блоков control flow (без {{ }} и без скобок в атрибутах/строках)."""
    text = re.sub(r"\{\{.*?\}\}", "", html, flags=re.S)
    text = re.sub(r'="[^"]*"', '=""', text)
    return text.count("{") - text.count("}")


def check_component(ts_path: Path, html_path: Path, base_members: set[str]) -> list[Finding]:
    out: list[Finding] = []
    ts = ts_path.read_text(encoding="utf-8")
    html = html_path.read_text(encoding="utf-8")
    extra = base_members if "extends ScreenBase" in ts else set()
    known = class_members(ts, extra) | set(re.findall(r"\s#([A-Za-z_$][\w$]*)", html))  # template refs могут быть объявлены ниже
    sc = _Scanner(str(html_path), known, out)
    sc.feed(html)
    sc.close()
    for t, l in sc.stack:
        out.append(Finding(str(html_path), l, "TPL_UNBALANCED_TAG", f"<{t}> не закрыт"))
    bal = block_balance(html)
    if bal:
        out.append(Finding(str(html_path), 1, "TPL_UNBALANCED_BLOCK", f"дисбаланс фигурных скобок блоков: {bal:+d}"))
    return out


def check_dir(src: Path) -> list[Finding]:
    base = src / "app" / "shared" / "screen" / "screen-base.ts"
    base_members = class_members(base.read_text(encoding="utf-8"), set()) if base.exists() else set()
    out: list[Finding] = []
    for html in sorted(src.rglob("*.html")):
        if html.name == "index.html":
            continue
        ts = html.with_suffix(".ts")
        if ts.exists():
            out += check_component(ts, html, base_members)
    return out


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    src = Path(args[0]) if args else Path("src")
    findings = check_dir(src)
    if "--json" in args:
        print(json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=2))
    else:
        for f in findings:
            print(f"{f.file}:{f.line}: {f.code}: {f.message}")
        print(f"template-check: {len(findings)} errors")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
