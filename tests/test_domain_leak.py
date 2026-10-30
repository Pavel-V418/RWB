"""Принцип 3: в specforge/ и agents/ нет слов предметной области конкретного кейса.

Слова — tests/domain_leak_words.txt. Разрешены только в examples/ и tests/fixtures/ (они вне
проверяемых каталогов, а вложенные examples/ и fixtures/ внутри них тоже пропускаются).
"""

import re
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKED_DIRS = ("specforge", "agents")
CHECKED_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".txt", ".html", ".sql", ".sh"}
ALLOWED_PARTS = {"examples", "fixtures", "__pycache__"}


def words() -> list[str]:
    lines = (ROOT / "tests" / "domain_leak_words.txt").read_text("utf-8").splitlines()
    return [w.strip().lower() for w in lines if w.strip() and not w.lstrip().startswith("#")]


def find_leaks(roots: Iterable[Path], vocabulary: list[str], base: Path = ROOT) -> list[str]:
    pattern = re.compile("|".join(re.escape(w) for w in vocabulary), re.IGNORECASE)
    leaks = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in CHECKED_SUFFIXES:
                continue
            if ALLOWED_PARTS & set(path.relative_to(root).parts):
                continue
            for number, line in enumerate(
                path.read_text("utf-8", errors="replace").splitlines(), 1
            ):
                match = pattern.search(line)
                if match:
                    leaks.append(f"{path.relative_to(base).as_posix()}:{number}: {match.group(0)}")
    return leaks


def test_vocabulary_not_empty() -> None:
    assert len(words()) >= 10  # пустой словарь — проверка ничего не проверяет


def test_no_domain_words_in_platform() -> None:
    leaks = find_leaks([ROOT / d for d in CHECKED_DIRS], words())
    assert not leaks, "доменные слова в платформе:\n" + "\n".join(leaks)


def test_checker_finds_leak_and_skips_allowed_dirs(tmp_path: Path) -> None:
    pkg = tmp_path / "specforge"
    (pkg / "examples").mkdir(parents=True)
    (pkg / "tests" / "fixtures").mkdir(parents=True)
    (pkg / "nodes.py").write_text("# ok\nPRICE = 'Парковка у дома'\n", "utf-8")
    (pkg / "examples" / "case.md").write_text("парковочные зоны", "utf-8")
    (pkg / "tests" / "fixtures" / "zones.json").write_text('{"externalZoneId": 1}', "utf-8")
    (pkg / "image.png").write_bytes(b"parking")

    assert find_leaks([pkg], words(), base=tmp_path) == ["specforge/nodes.py:2: Парков"]
