"""CLI блока анализа.

    specforge-analysis requirements --input input/ --out artifacts/01_requirements
    specforge-analysis requirements --input case.md --out out/ --llm fake:golden/<кейс>/llm

Коды выхода: 0 — шлюз пройден; 1 — шлюз не пройден; 3 — нужны ответы на вопросы (интерактивный режим).
rehearse: 0 — все запущенные кейсы прошли все этапы; 1 — есть провал; 2 — ни один кейс не запускался.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import stage_requirements
from .llm import Metrics, make_llm


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="specforge-analysis")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("requirements", help="этап 1: анализ требований")
    r.add_argument("--input", required=True, help="файл требований или папка (requirements.* и answers.yaml)")
    r.add_argument("--out", required=True, help="папка артефактов, обычно artifacts/01_requirements")
    r.add_argument("--mode", choices=["auto", "interactive"], default="auto",
                   help="auto — умолчания на вопросы; interactive — остановиться и ждать answers.yaml")
    r.add_argument("--no-critic", action="store_true", help="без вызова критика (минус 1 вызов LLM)")
    r.add_argument("--llm", default=None, help="'fake:<папка фикстур>' для прогона без модели")
    r.add_argument("--dump-prompts", action="store_true", help="сохранить промпты в <out>/_prompts")

    s = sub.add_parser("system", help="этап 2: системный анализ и контракты")
    s.add_argument("--input", required=True, help="папка этапа 1, обычно artifacts/01_requirements")
    s.add_argument("--out", required=True, help="папка артефактов, обычно artifacts/02_system")
    s.add_argument("--no-db-check", action="store_true", help="не применять DDL к PostgreSQL")
    s.add_argument("--llm", default=None, help="'fake:<папка фикстур>' для прогона без модели")
    s.add_argument("--dump-prompts", action="store_true")

    dc = sub.add_parser("docs", help="этап документации: сайт MkDocs из артефактов всей команды")
    dc.add_argument("--artifacts", default="artifacts", help="корень артефактов (01_requirements, 02_system, …)")
    dc.add_argument("--out", default=None, help="по умолчанию <artifacts>/09_docs")
    dc.add_argument("--repo", default=None, help="корень репозитория (docker-compose.yml, README-run.md)")
    dc.add_argument("--llm", default=None)
    dc.add_argument("--dump-prompts", action="store_true")

    rh = sub.add_parser("rehearse", help="репетиция: этапы 1–2 по всем кейсам + scorecard.md")
    rh.add_argument("--cases", default="cases", help="папка с кейсами (requirements.* + expected.yaml)")
    rh.add_argument("--out", default="runs", help="куда писать результаты")
    rh.add_argument("--llm", default=None, help="'fake' — эталонные фикстуры golden/<кейс>/llm")
    rh.add_argument("--only", nargs="*", help="имена кейсов")
    rh.add_argument("--no-db-check", action="store_true")

    sch = sub.add_parser("schema", help="выгрузить JSON Schema requirements.json")
    sch.add_argument("--out", required=True)

    args = ap.parse_args(argv)

    if args.cmd == "rehearse":
        from . import rehearse
        scores = rehearse.run(args.cases, args.out, args.llm, check_db=not args.no_db_check, only=args.only)
        print((Path(args.out) / "scorecard.md").read_text(encoding="utf-8"))
        return rehearse.exit_code(scores)

    if args.cmd == "schema":
        from .schemas import Requirements
        Path(args.out).write_text(json.dumps(Requirements.model_json_schema(), ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        return 0

    if args.cmd == "docs" and not args.out:
        args.out = str(Path(args.artifacts) / "09_docs")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    metrics = Metrics(sink=out / "metrics.jsonl")
    if metrics.sink.exists():
        metrics.sink.unlink()
    llm = make_llm(args.llm, metrics, dump_dir=out / "_prompts" if args.dump_prompts else None)
    if args.cmd == "system":
        from . import stage_system
        res = stage_system.run(args.input, out, llm, check_db=not args.no_db_check)
    elif args.cmd == "docs":
        from . import stage_docs
        res = stage_docs.run(args.artifacts, out, llm, repo_root=args.repo)
    else:
        res = stage_requirements.run(args.input, out, llm, mode=args.mode, use_critic=not args.no_critic)
    (out / "gate_result.json").write_text(json.dumps(res.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    status = "PASSED" if res.passed else "FAILED"
    print(f"[{res.stage}] {status}: {len(res.errors)} ошибок, {len(res.warnings)} предупреждений, "
          f"LLM-вызовов {res.llm_calls}, токены in/out {res.tokens['input']}/{res.tokens['output']}, "
          f"{res.duration_s} с")
    for i in res.errors:
        print("  " + str(i))
    if stage_requirements.needs_answers(res):
        print(f"  -> ответьте на вопросы: {out / 'questions.md'}, шаблон {out / 'answers.template.yaml'}")
        return 3
    return 0 if res.passed else 1


if __name__ == "__main__":
    sys.exit(main())
