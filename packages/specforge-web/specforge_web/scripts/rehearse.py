"""Репетиция веб-ветки на всех кейсах из fixtures/ (шаг 11 плана): UX → frontend → быстрые шлюзы [→ ng build].

  python -m scripts.rehearse                 # без LLM (baseline) — проверка устойчивости генератора
  python -m scripts.rehearse --llm           # с моделью из SPECFORGE_LLM_*
  python -m scripts.rehearse --build --only synthetic-booking
Кейс = папка fixtures/<name> с 01_requirements/requirements.json и 02_system/openapi.yaml.
Итог: таблица и out/rehearsal/report.json (экраны, покрытие FR, режим UX, токены, ошибки).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from specforge_web.agents.common.llm import llm_from_env  # noqa: E402
from specforge_web.agents.frontend import agent as fe  # noqa: E402
from specforge_web.agents.ux_designer import agent as ux  # noqa: E402


def cases(only: list[str] | None) -> list[Path]:
    out = sorted(p for p in (ROOT / "fixtures").iterdir() if (p / "02_system" / "openapi.yaml").exists() and (p / "01_requirements" / "requirements.json").exists())
    return [p for p in out if not only or p.name in only]


def rehearse(case: Path, out: Path, llm, build: bool) -> dict:
    art = out / case.name
    shutil.rmtree(art, ignore_errors=True)
    art.mkdir(parents=True)
    for d in ("01_requirements", "02_system"):
        shutil.copytree(case / d, art / d)
    t0 = time.time()
    quiet = lambda *_: None  # noqa: E731
    r = ux.run(art, art / "03_ux", llm, log=quiet)
    row = {"case": case.name, "ux": r.passed, "screens": {k: v.get("screens") for k, v in r.metrics.items()},
           "coverage": {k: v.get("coverage") for k, v in r.metrics.items()}, "uxMode": {k: v.get("mode") for k, v in r.metrics.items()},
           "confident": {k: v.get("coverageConfident") for k, v in r.metrics.items()},
           "weak": {k: v.get("weakRequirements") for k, v in r.metrics.items() if v.get("weakRequirements")},
           "fallbackScreens": sum(len(v.get("fallbackScreens", [])) for v in r.metrics.values()),
           "tokens": r.tokens_in + r.tokens_out, "errors": [e.message for e in r.errors if e.severity == "error"][:5]}
    if r.passed and (art / "03_ux" / "screen-spec.web.json").exists():
        f = fe.run(art, art / "05_web", llm, build=build, log=quiet)
        row |= {"fe": f.passed, "build": f.metrics.get("build"), "deterministicShare": f.metrics.get("deterministicShare"),
                "tokens": row["tokens"] + f.tokens_in + f.tokens_out}
        row["errors"] += [f"{e.file}:{e.line} {e.message}" for e in f.errors if e.severity == "error"][:5]
    row["sec"] = round(time.time() - t0, 1)
    row["passed"] = row["ux"] and row.get("fe", True)
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "rehearsal")
    a = ap.parse_args(argv)
    llm = llm_from_env() if a.llm else None
    rows = [rehearse(c, a.out, llm, a.build) for c in cases(a.only)]
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "report.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{'кейс':24} {'итог':6} {'экраны':18} {'покрытие':18} {'уверенно':18} {'UX':18} {'без LLM':8} {'токены':>8} {'сек':>5}")
    for r in rows:
        print(f"{r['case']:24} {'OK' if r['passed'] else 'FAIL':6} {json.dumps(r['screens']):18} {json.dumps(r['coverage']):18} "
              f"{json.dumps(r['confident']):18} {','.join(set(r['uxMode'].values())):18} {str(r.get('deterministicShare', '—')):8} {r['tokens']:>8} {r['sec']:>5}")
        for plat, ids in r.get("weak", {}).items():
            print(f"    ? {plat}: привязка не уверена — {', '.join(ids)} (проверить вручную)")
        for e in r["errors"]:
            print(f"    ! {e[:180]}")
    return 0 if all(r["passed"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
