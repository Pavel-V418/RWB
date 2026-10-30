"""Веб-ветка конвейера целиком (участник 4): UX → frontend → сборка → UI-ревью → автоисправления → повтор.

  python -m agents.ui_pipeline --in artifacts [--build] [--review] [--offline] [--rounds 2]

Узлы по отдельности (для графа участника 1):
  agents.ux_designer.agent.run(artifacts, artifacts/03_ux, llm)
  agents.frontend.agent.run(artifacts, artifacts/05_web, llm, build=True)
  agents.vlm_reviewer.review.run(artifacts, artifacts/07_tests/ui, llm, base_url=...)
Итог: artifacts/05_web/ui-pipeline.json — метрики этапов (токены, время, попытки) для отчёта об эффективности.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from specforge_web.agents.common.llm import llm_from_env  # noqa: E402
from specforge_web.agents.frontend import agent as fe  # noqa: E402
from specforge_web.agents.ux_designer import agent as ux  # noqa: E402
from specforge_web.agents.vlm_reviewer import autofix, review  # noqa: E402


def stage(name: str, res) -> dict:
    return {"stage": name, "passed": res.passed, "tokensIn": res.tokens_in, "tokensOut": res.tokens_out, "tokensCached": res.tokens_cached,
            "llmCalls": res.llm_calls, "attempts": res.attempts, "durationSec": res.duration_sec,
            "errors": sum(1 for e in res.errors if e.severity == "error"), "warnings": sum(1 for e in res.errors if e.severity == "warning")}


def run(artifacts: Path, llm, build: bool = False, do_review: bool = False, rounds: int = 2, log=print) -> dict:
    t0 = time.time()
    report: dict = {"stages": []}
    if not (artifacts / "03_ux" / "screen-spec.web.json").exists() or (artifacts / "02_system" / "openapi.yaml").stat().st_mtime > (artifacts / "03_ux" / "screen-spec.web.json").stat().st_mtime:
        r = ux.run(artifacts, artifacts / "03_ux", llm, log=log)
        report["stages"].append(stage("03_ux", r))
        if not r.passed:
            return report | {"passed": False}
    feedback: dict = {}
    for rnd in range(rounds + 1):
        r = fe.run(artifacts, artifacts / "05_web", llm, build=build, log=log, feedback=feedback)
        report["stages"].append(stage(f"05_web#{rnd + 1}", r) | {"deterministicShare": r.metrics.get("deterministicShare"), "build": r.metrics.get("build")})
        if not r.passed or not do_review:
            break
        dist = artifacts / "05_web" / "admin" / "dist" / "app" / "browser"
        if not dist.exists():
            log("[ui] нет собранной админки (dist) — UI-ревью пропущено; запустите с --build")
            break
        from specforge_web.tools.spa_server import serve
        httpd = serve(dist, port=0, background=True, mock_openapi=artifacts / "02_system" / "openapi.yaml")
        base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            rv = review.run(artifacts, artifacts / "07_tests" / "ui", llm, base_url=base_url, log=log)
        finally:
            httpd.shutdown()
        report["stages"].append(stage(f"07_ui_review#{rnd + 1}", rv) | {"defects": rv.metrics["defects"]})
        if rv.passed or rnd == rounds:
            break
        fix = autofix.apply(artifacts, artifacts / "07_tests" / "ui")
        slots = json.loads((artifacts / "05_web" / "admin" / "slots.json").read_text(encoding="utf-8"))
        feedback = autofix.slot_feedback(json.loads((artifacts / "07_tests" / "ui" / "defects.json").read_text(encoding="utf-8")), slots)
        report.setdefault("autofix", []).append(fix | {"slotFeedback": {k: len(v) for k, v in feedback.items()}})
        if not fix["specPatches"] and not feedback:
            log("[ui] автоисправлений нет — остальные дефекты в отчёте")
            break
        log(f"[ui] автоисправления: {fix['specPatches']} · замечания слотам: {list(feedback)}")
    # документация интерфейса из спеки (+ скриншоты ревью, если были)
    from specforge_web.tools.ui_docs import generate_docs
    docs = generate_docs(artifacts, artifacts / "09_docs" / "ui", artifacts / "05_web" / "admin", artifacts / "07_tests" / "ui" / "screenshots")
    report["docs"] = [str(p.relative_to(artifacts)) for p in docs]
    report["passed"] = all(s["passed"] for s in report["stages"] if not s["stage"].startswith("07_ui_review")) and \
        (not do_review or any(s["stage"].startswith("07_ui_review") and s["passed"] for s in report["stages"]))
    report["totals"] = {k: sum(s[k] for s in report["stages"]) for k in ("tokensIn", "tokensOut", "tokensCached", "llmCalls")} | {"durationSec": round(time.time() - t0, 1)}
    out = artifacts / "05_web" / "ui-pipeline.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[ui] итог: {'PASSED' if report['passed'] else 'FAILED'} · токены {report['totals']['tokensIn']}+{report['totals']['tokensOut']} · {report['totals']['durationSec']} с")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SpecForge web branch")
    ap.add_argument("--in", dest="inp", type=Path, required=True)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--rounds", type=int, default=2)
    a = ap.parse_args(argv)
    rep = run(a.inp, None if a.offline else llm_from_env(), a.build, a.review, a.rounds)
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
