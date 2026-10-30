"""Принцип 3: в specforge/ и agents/ нет слов предметной области конкретного кейса."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKED_DIRS = ("specforge", "agents")
CHECKED_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".txt"}


def _words() -> list[str]:
    lines = (ROOT / "tests" / "domain_leak_words.txt").read_text("utf-8").splitlines()
    return [w.strip().lower() for w in lines if w.strip() and not w.lstrip().startswith("#")]


def test_no_domain_words_in_platform() -> None:
    words = _words()
    if not words:
        return
    pattern = re.compile("|".join(re.escape(w) for w in words), re.IGNORECASE)
    leaks = [
        f"{path.relative_to(ROOT)}: {match.group(0)}"
        for d in CHECKED_DIRS
        for path in (ROOT / d).rglob("*")
        if path.suffix in CHECKED_SUFFIXES and "__pycache__" not in path.parts
        for match in [pattern.search(path.read_text("utf-8", errors="replace"))]
        if match
    ]
    assert not leaks, "доменные слова в платформе:\n" + "\n".join(leaks)
