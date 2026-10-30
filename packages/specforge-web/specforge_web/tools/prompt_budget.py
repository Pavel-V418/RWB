#!/usr/bin/env python3
"""Бюджет токенов веб-ветки без вызова модели (сухой прогон UX-агента и frontend-агента).

  python tools/prompt_budget.py --in fixtures/case1 [--json]

Подставная LLM отвечает черновиком (baseline), а мы считаем, какие промпты ушли бы в модель:
сколько вызовов, сколько токенов на вход и какая доля приходится на статичный префикс (system),
который vLLM переиспользует из prefix cache. Оценка токенов: ~3,5 символа на токен для смеси
русского и JSON (Qwen-токенизатор), погрешность ±15%.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import yaml  # noqa: E402

from specforge_web.agents.common.llm import ReplayLLM  # noqa: E402
from specforge_web.agents.ux_designer import agent as ux  # noqa: E402
from specforge_web.agents.ux_designer.baseline import baseline_spec  # noqa: E402

CHARS_PER_TOKEN = 3.5


def tok(s: str) -> int:
    return int(len(s) / CHARS_PER_TOKEN)


def budget(case: Path) -> dict:
    req = json.loads((case / "01_requirements" / "requirements.json").read_text(encoding="utf-8"))
    api = yaml.safe_load((case / "02_system" / "openapi.yaml").read_text(encoding="utf-8"))
    calls: list[dict] = []
    bases = {}

    def respond(messages, tag):
        plat = "android" if "android" in tag or "Платформа: android" in messages[-1]["content"] else "web"
        base = bases.setdefault(plat, baseline_spec(api, req, plat))
        sys_t, user_t = tok(messages[0]["content"]), tok(messages[-1]["content"] if isinstance(messages[-1]["content"], str) else "")
        if tag.startswith("ux.plan"):
            out = {"appTitle": base["app"]["title"], "screens": [
                {"id": s["id"], "title": s["title"], "archetype": s["archetype"], "route": s["route"], "roles": s["roles"], "requirements": s["requirements"],
                 "primaryOperationId": (s.get("data") or {}).get("primary", {}).get("operationId") or (s.get("form") or {}).get("submit", {}).get("operationId")}
                for s in base["screens"] if s["archetype"] not in ("login", "forbidden")]}
        else:
            sid = json.loads(re.search(r"## Экран\n(\{.*?\})\n", messages[-1]["content"], re.S).group(1))["id"]
            out = next(s for s in base["screens"] if s["id"] == sid)
        calls.append({"tag": tag, "system": sys_t, "user": user_t, "output": tok(json.dumps(out, ensure_ascii=False))})
        return out

    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        for d in ("01_requirements", "02_system"):
            shutil.copytree(case / d, t / d)
        ux.run(t, t / "03_ux", ReplayLLM(respond), log=lambda *_: None)
    tin = sum(c["system"] + c["user"] for c in calls)
    cached = sum(c["system"] for c in calls[1:])  # system повторяется и попадает в prefix cache начиная со 2-го вызова
    by: dict[str, dict] = {}
    for c in calls:
        b = by.setdefault(c["tag"], {"calls": 0, "input": 0, "output": 0})
        b["calls"] += 1
        b["input"] += c["system"] + c["user"]
        b["output"] += c["output"]
    return {"case": case.name, "calls": len(calls), "inputTokens": tin, "outputTokens": sum(c["output"] for c in calls),
            "cacheableShare": round(cached / tin, 2) if tin else 0, "byStage": by,
            "note": "frontend-агент: токены только на слоты; на кейсах без слотов — 0"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", type=Path, nargs="+", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows = [budget(c) for c in a.inp]
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            print(f"{r['case']}: вызовов {r['calls']}, вход ≈{r['inputTokens']:,} ток., выход ≈{r['outputTokens']:,} ток., из кэша ≈{r['cacheableShare']:.0%}".replace(",", " "))
            for tag, b in r["byStage"].items():
                print(f"   {tag:18} {b['calls']:3} выз.  вход ≈{b['input']:>7,}  выход ≈{b['output']:>6,}".replace(",", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
