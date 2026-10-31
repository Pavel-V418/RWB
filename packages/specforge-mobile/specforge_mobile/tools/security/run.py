"""
Security-контур одной командой (из корня репозитория):

    python -m specforge_mobile.tools.security.run --target . --out artifacts/07_tests --requirements artifacts/01_requirements/requirements.json

Шаги: сканеры → разбор отчётов → категория и SEC-требование → триаж (правила + LLM) →
повторный скан против baseline (исправленные) → security.json, findings.json, report.md.
Код выхода: 1 — есть блокирующие находки (для CI), 0 — нет. --no-fail всегда 0.

LLM-триаж включается, если заданы LLM_BASE_URL и LLM_MODEL (см. agents/android_agent/llm.py), иначе
находки остаются «требует проверки» и не блокируют.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import classify, report, scan, triage
from .findings import load_reports


def _tool_of(source: str) -> str:
    """Имя сканера по файлу отчёта: сканер, не нашедший ничего, тоже считается «проверено»."""
    stem = source.rsplit(".", 1)[0]
    if stem.startswith("trivy-image"):
        return "trivy-image"
    return stem.split("-")[0]


def main(argv: list[str] | None = None, llm=None) -> int:
    """llm — готовый клиент с .chat(messages) (в платформе — мост к LLMClient); иначе из LLM_* окружения."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", type=Path, default=Path("."), help="что сканировать (корень репозитория)")
    ap.add_argument("--out", type=Path, required=True, help="папка результатов тестов (07_tests)")
    ap.add_argument("--requirements", type=Path, help="requirements.json с разделом security")
    ap.add_argument("--baseline", type=Path, help="findings.json прошлого скана (по умолчанию <out>/findings.json)")
    ap.add_argument("--tools", default="semgrep,gitleaks,trivy", help="какие сканеры запускать")
    ap.add_argument("--images", default=os.environ.get("SCAN_IMAGES", ""),
                    help="образы для trivy image через запятую (в CI: $API_IMAGE,$WEB_IMAGE)")
    ap.add_argument("--no-scan", action="store_true", help="не запускать сканеры, разобрать готовые <out>/raw/")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--no-fail", action="store_true")
    args = ap.parse_args(argv)

    t0 = time.monotonic()
    out, raw = args.out, args.out / "raw"
    out.mkdir(parents=True, exist_ok=True)   # первый запуск в чистой папке (например, с --no-scan)
    baseline = args.baseline or (out / "findings.json")
    baseline_copy = None
    if baseline.is_file():  # отчёт перезапишется — сохраняем прошлый для сравнения
        baseline_copy = out / "findings.prev.json"
        baseline_copy.write_text(baseline.read_text(encoding="utf-8"), encoding="utf-8")

    notes = [] if args.no_scan else scan.run_all(args.target, raw, set(args.tools.split(",")),
                                                   [i for i in args.images.split(",") if i.strip()])
    for n in notes:
        print(" ", n)
    findings, sources = load_reports(raw, args.target) if raw.is_dir() else ([], [])
    sec_reqs = classify.load_security_requirements(args.requirements)
    classify.classify(findings, sec_reqs)

    if args.no_llm:
        llm = None
    elif llm is None and os.environ.get("LLM_BASE_URL"):
        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from specforge_mobile.android_agent.llm import OpenAICompatLLM
        llm = OpenAICompatLLM.from_env()
    cache_path = out / "triage_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
    stats = triage.llm_triage(findings, args.target, llm, cache)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    classify.apply_policy(findings)
    fixed = report.merge_baseline(findings, baseline_copy)
    tools_run = sorted({f.tool for f in findings} | {_tool_of(s) for s in sources})
    s = report.write_outputs(out, findings, sec_reqs, tools_run,
                             {"scanNotes": notes, "sources": sources, "triage": stats,
                              "durationSec": round(time.monotonic() - t0, 1)})
    print(f"security: {s['status']} — находок {s['total']}, открыто {s['open']}, блокирует {s['blocking']}, "
          f"исправлено {fixed}, ложных {s['false_positive']}; LLM: вызовов {stats['calls']}, "
          f"токенов {stats['tokens']}, из кэша {stats['cached']} → {out / 'report.md'}")
    return 0 if args.no_fail or s["status"] == "PASSED" else 1


if __name__ == "__main__":
    sys.exit(main())
