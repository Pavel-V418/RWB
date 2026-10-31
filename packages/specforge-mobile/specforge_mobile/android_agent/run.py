"""
Запуск Android-агента из командной строки (из корня репозитория):

    python -m specforge_mobile.android_agent.run --in artifacts --out artifacts/06_android

LLM настраивается переменными окружения LLM_BASE_URL, LLM_API_KEY, LLM_MODEL (см. llm.py).
Код выхода: 0 — шлюз пройден, 1 — нет.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agent import DEFAULT_TEMPLATE, run
from .context import load_screen_spec
from .llm import OpenAICompatLLM
from .replay import ReplayLLM


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="input_dir", type=Path, required=True, help="папка artifacts/")
    ap.add_argument("--out", dest="output_dir", type=Path, required=True, help="обычно artifacts/06_android")
    ap.add_argument("--spec", type=Path, help="UX-спека, если не в 03_ux/screen-spec.android.json")
    ap.add_argument("--template", type=Path, help="каркас Android (по умолчанию android/ репозитория)")
    ap.add_argument("--api-client", type=Path, help="папка сгенерированного Kotlin-клиента другого кейса")
    ap.add_argument("--max-repairs", type=int, default=3)
    ap.add_argument("--offline", action="store_true", help="gradle --offline (прогретый кэш)")
    ap.add_argument("--replay", action="store_true",
                    help="без LLM: отвечать эталоном из каркаса (проверка механики агента и сборки)")
    ap.add_argument("--resume", action="store_true",
                    help="не генерировать заново: только шлюз и ремонт уже сгенерированного <out>/project")
    args = ap.parse_args()

    if args.replay:
        _, screens = load_screen_spec(args.input_dir, args.spec)
        llm = ReplayLLM(args.template or DEFAULT_TEMPLATE, [s["screenId"] for s in screens])
    else:
        llm = OpenAICompatLLM.from_env()
    result = run(args.input_dir, args.output_dir, llm, template=args.template,
                 spec=args.spec, api_client=args.api_client, max_repairs=args.max_repairs, offline=args.offline,
                 resume=args.resume)
    status = "ПРОЙДЕН" if result["passed"] else f"НЕ ПРОЙДЕН ({len(result['errors'])} ошибок)"
    print(f"\nAndroid-агент: {status}; попыток {result['attempts']}, токенов {result['tokens']}, "
          f"{result['durationSec']} с → {args.output_dir / 'gate_report.json'}")
    for e in result["errors"][:10]:
        print(f"  [{e.get('category')}] {e.get('file')}:{e.get('line')}  {str(e.get('message'))[:160]}")
    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
