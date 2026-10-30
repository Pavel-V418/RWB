#!/usr/bin/env python3
"""kit-lint: проверка строгого соответствия UI Kit (PrimeNG 20).

Ловит то, что жюри называет «несоответствием UI Kit», и то, на чём LLM чаще всего ошибается:
  KIT_RAW_ELEMENT      сырой <button>/<select>/<table>/<dialog> вместо компонента кита
  KIT_MISSING_DIRECTIVE <input> без pInputText, <textarea> без pTextarea
  KIT_DEPRECATED       устаревший компонент (p-dropdown, p-calendar, ...) — с заменой
  KIT_UNKNOWN          p-* тег, которого нет в манифесте (галлюцинация)
  KIT_BAD_IMPORT       импорт устаревшего/неразрешённого модуля primeng/*
  KIT_RAW_COLOR        цвет литералом (#hex, rgb, hsl) вне файла токенов
  KIT_NO_TESTID        (warning) интерактивный элемент без data-testid

Использование:
  python tools/kit_lint.py <src_dir> [--manifest path] [--json]
Код возврата 0 — нет ошибок (предупреждения допустимы), 1 — есть ошибки.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path

DEFAULT_MANIFEST = Path(__file__).resolve().parent.parent / "design-system" / "primeng" / "kit-manifest.json"
COLOR_ALLOWED_FILES = {"tokens.scss", "preset.ts"}
INTERACTIVE_FOR_TESTID = {"p-button", "p-select", "p-multiselect", "p-datepicker", "p-inputnumber",
                          "p-password", "p-toggleswitch", "p-selectbutton", "p-fileupload"}

RE_INLINE_TEMPLATE = re.compile(r"template\s*:\s*`([\s\S]*?)`")
RE_IMPORT = re.compile(r"""from\s+['"]([^'"]+)['"]""")
RE_COLOR = re.compile(r"(#[0-9a-fA-F]{3,8}\b|\brgba?\s*\(|\bhsla?\s*\()")
RE_CSS_COMMENT = re.compile(r"/\*[\s\S]*?\*/")
RE_LINE_COMMENT = re.compile(r"(^|[^:])//.*$", re.M)


@dataclass
class Finding:
    file: str
    line: int
    code: str
    message: str
    fix: str = ""
    severity: str = "error"


class Manifest:
    def __init__(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.tags = {c["tag"].lower() for c in data["components"] if not c.get("deprecatedInProject")}
        self.project_banned = {c["tag"].lower(): c.get("purpose", "") for c in data["components"] if c.get("deprecatedInProject")}
        # Парсер HTML приводит теги к нижнему регистру: camelCase-синонимы валидных тегов не считаем устаревшими
        self.deprecated = {k.lower(): v for k, v in data["deprecated"].items() if k.lower() not in self.tags}
        self.deprecated_imports = data["deprecatedImports"]
        self.allowed_imports = {c["from"] for c in data["components"]} | {d["from"] for d in data["directives"]}
        self.allowed_imports |= {"primeng/config", "primeng/api", "primeng/ripple"}
        self.forbidden = data["forbiddenElements"]
        self.required_attrs = data["requiredAttributes"]


class _TemplateScanner(HTMLParser):
    def __init__(self, manifest: Manifest, file: str, line_offset: int, out: list[Finding]):
        super().__init__(convert_charrefs=True)
        self.m, self.file, self.line_offset, self.out = manifest, file, line_offset, out

    def _add(self, code, msg, fix="", severity="error"):
        line = self.getpos()[0] + self.line_offset
        self.out.append(Finding(self.file, line, code, msg, fix, severity))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        names = {a[0].lower() for a in attrs}
        attr_map = {a[0].lower(): (a[1] or "") for a in attrs}

        if tag in self.m.forbidden:
            if not (tag == "button" and "pbutton" in names):
                self._add("KIT_RAW_ELEMENT", f"<{tag}> запрещён правилами UI Kit", f"используй {self.m.forbidden[tag]}")
        if tag in self.m.required_attrs:
            rule = self.m.required_attrs[tag]
            if attr_map.get("type", "").lower() not in [t.lower() for t in rule.get("exceptTypes", [])]:
                if not any(a.lower() in names for a in rule["anyOf"]):
                    self._add("KIT_MISSING_DIRECTIVE", f"<{tag}> без директивы {'/'.join(rule['anyOf'])}", rule["hint"])
        if tag.startswith("p-"):
            if tag in self.m.deprecated:
                self._add("KIT_DEPRECATED", f"<{tag}> устарел в PrimeNG 20", f"замени на {self.m.deprecated[tag]}")
            elif tag in self.m.project_banned:
                self._add("KIT_DEPRECATED", f"<{tag}> не используется в проекте", self.m.project_banned[tag])
            elif tag not in self.m.tags:
                self._add("KIT_UNKNOWN", f"<{tag}> нет в kit-manifest.json", "выбери компонент из манифеста")
            if tag in INTERACTIVE_FOR_TESTID and "data-testid" not in names and "[attr.data-testid]" not in names:
                self._add("KIT_NO_TESTID", f"<{tag}> без data-testid", 'добавь data-testid="<screen>-<element>"', "warning")
        style = attr_map.get("style", "")
        if style and RE_COLOR.search(style):
            self._add("KIT_RAW_COLOR", "цвет литералом в style", "используй var(--sf-*) / var(--p-*) или severity")


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def lint_template(text: str, file: str, m: Manifest, line_offset: int = 0) -> list[Finding]:
    out: list[Finding] = []
    scanner = _TemplateScanner(m, file, line_offset, out)
    scanner.feed(text)
    scanner.close()
    return out


def lint_ts(text: str, file: str, m: Manifest) -> list[Finding]:
    out: list[Finding] = []
    for mt in RE_INLINE_TEMPLATE.finditer(text):
        out += lint_template(mt.group(1), file, m, _line_of(text, mt.start(1)) - 1)
    for mt in RE_IMPORT.finditer(text):
        mod = mt.group(1)
        line = _line_of(text, mt.start())
        for bad, good in m.deprecated_imports.items():
            if mod == bad or mod.startswith(bad + "/"):
                out.append(Finding(file, line, "KIT_BAD_IMPORT", f"импорт '{mod}' запрещён", f"используй {good}"))
                break
        else:
            if mod.startswith("primeng/") and mod not in m.allowed_imports:
                out.append(Finding(file, line, "KIT_BAD_IMPORT", f"модуль '{mod}' не входит в kit-manifest.json",
                                   "добавь компонент в манифест или выбери другой"))
    if Path(file).name not in COLOR_ALLOWED_FILES:
        out += _lint_colors_in_styles(text, file)
    return out


def _lint_colors_in_styles(text: str, file: str) -> list[Finding]:
    """Цвета в styles: [...] внутри .ts."""
    out = []
    for mt in re.finditer(r"styles\s*:\s*\[([\s\S]*?)\]", text):
        for c in RE_COLOR.finditer(mt.group(1)):
            out.append(Finding(file, _line_of(text, mt.start(1) + c.start()), "KIT_RAW_COLOR",
                               f"цвет литералом '{c.group(0)}'", "используй var(--sf-*) / var(--p-*)"))
    return out


def lint_style(text: str, file: str) -> list[Finding]:
    if Path(file).name in COLOR_ALLOWED_FILES:
        return []
    clean = RE_CSS_COMMENT.sub(lambda mt: re.sub(r"[^\n]", " ", mt.group(0)), text)
    clean = RE_LINE_COMMENT.sub(lambda mt: mt.group(1) + " " * (len(mt.group(0)) - len(mt.group(1))), clean)
    out = []
    for c in RE_COLOR.finditer(clean):
        out.append(Finding(file, _line_of(clean, c.start()), "KIT_RAW_COLOR", f"цвет литералом '{c.group(0).strip()}'",
                           "используй var(--sf-*) / var(--p-*) из tokens.scss"))
    return out


def lint_dir(src: Path, manifest_path: Path = DEFAULT_MANIFEST) -> list[Finding]:
    m = Manifest(manifest_path)
    findings: list[Finding] = []
    for p in sorted(src.rglob("*")):
        if not p.is_file() or "node_modules" in p.parts or "dist" in p.parts or ".angular" in p.parts:
            continue
        rel = str(p.relative_to(src))
        if p.suffix == ".html":
            findings += lint_template(p.read_text(encoding="utf-8"), rel, m)
        elif p.suffix == ".ts" and not p.name.endswith(".spec.ts") and "generated" not in p.parts:
            findings += lint_ts(p.read_text(encoding="utf-8"), rel, m)
        elif p.suffix in (".scss", ".css"):
            findings += lint_style(p.read_text(encoding="utf-8"), rel)
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", type=Path)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    findings = lint_dir(args.src, args.manifest)
    errors = [f for f in findings if f.severity == "error"]
    if args.json:
        print(json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=2))
    else:
        for f in findings:
            print(f"{f.file}:{f.line}: {f.severity} {f.code}: {f.message}" + (f" → {f.fix}" if f.fix else ""))
        print(f"kit-lint: {len(errors)} errors, {len(findings) - len(errors)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
