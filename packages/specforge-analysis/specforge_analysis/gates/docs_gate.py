"""Шлюз документации: все обязательные страницы на месте, относительные ссылки и картинки не битые,
у каждого обязательного FR есть API/событие; незаполненные разделы соседей — предупреждения, не ошибки."""

from __future__ import annotations

import re
from pathlib import Path

from ..contract import Issue

LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")


def check(docs_dir: Path, required: list[str], tm: dict, pending_count: int, min_designed_pct: float = 100.0) -> list[Issue]:
    issues: list[Issue] = []
    for page in required:
        p = docs_dir / page
        if not p.exists() or not p.read_text(encoding="utf-8").strip():
            issues.append(Issue("E_DOC_MISSING", "error", "нет обязательной страницы", page))
    for p in docs_dir.rglob("*.md"):
        text = p.read_text(encoding="utf-8")
        for target in LINK_RE.findall(text):
            if re.match(r"^[a-z]+://", target) or target.startswith("mailto:"):
                continue
            if not (p.parent / target).resolve().exists():
                issues.append(Issue("E_DOC_LINK", "error", f"битая ссылка {target}", str(p.relative_to(docs_dir))))
    # обязательные (must) FR — без исключений; should/could без API — только предупреждение
    if tm.get("must_designed_pct", tm["fr_designed_pct"]) < min_designed_pct:
        issues.append(Issue("E_TRACE_DESIGN", "error",
                            f"спроектировано {tm.get('must_designed_pct')}% обязательных FR, требуется {min_designed_pct}%"))
    elif tm["fr_designed_pct"] < 100:
        issues.append(Issue("W_TRACE_DESIGN", "warning",
                            f"без API/событий: {', '.join(tm.get('fr_not_designed', []))} (не must)"))
    if tm.get("fr_with_tests_pct") is not None and tm["fr_with_tests_pct"] < 95:
        issues.append(Issue("W_TRACE_TESTS", "warning", f"тестами покрыто {tm['fr_with_tests_pct']}% FR (цель ≥ 95%)"))
    if pending_count:
        issues.append(Issue("W_DOC_PENDING", "warning",
                            f"разделов, ожидающих результатов других участников: {pending_count}"))
    return issues
