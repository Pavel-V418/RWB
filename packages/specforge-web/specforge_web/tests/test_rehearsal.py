"""Репетиция кейса №2: каждый кейс из fixtures проходит веб-ветку без LLM (baseline) — генератор устойчив к чужому API."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from specforge_web.scripts.rehearse import cases, rehearse  # noqa: E402
from specforge_web.tools import ts_check  # noqa: E402


@pytest.mark.parametrize("case", cases(None), ids=lambda p: p.name)
def test_case_passes_offline(case, tmp_path):
    row = rehearse(case, tmp_path, None, build=False)
    assert row["passed"], row["errors"]
    assert all(c == 1.0 or c is None for c in row["coverage"].values()), row["coverage"]
    if ts_check.find_tsc() is None:
        pytest.skip("ts-check не выполнялся")
