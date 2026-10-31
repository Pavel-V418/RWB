#!/usr/bin/env python3
"""
e2e демо-сценарий участника 5 (mock-маршрут, сессия, журнал событий, уведомления, права, аудит).

Агент qa кладёт этот файл в 07_tests/e2e/ (в опубликованном репозитории — reports/tests/e2e/), рядом —
копию кода в 07_tests/specforge_mobile/ и сценарий кейса e2e/scenario.json. CI (стадия e2e, задача
stand:smoke участника 3) запускает:
    python3 reports/tests/e2e/route_e2e.py --base $STAND_URL

Сам сценарий — specforge_mobile/tools/e2e/run.py; этот файл только находит его и подставляет пути:
  --out           по умолчанию папка тестов (родитель e2e/): e2e-junit.xml, e2e.json, raw/e2e.sarif;
  --scenario      по умолчанию e2e/scenario.json рядом с этим файлом (иначе E2E_SCENARIO);
  --requirements  requirements.json анализа, если найден (теги SEC).
Пароли — только из окружения: CONTRACT_PASSWORD_DRIVER, CONTRACT_PASSWORD_ADMIN.
Код выхода: 1 — есть проваленные шаги; 0 без сценария (нечего проверять).
"""
import os
import sys
from pathlib import Path

here = Path(__file__).resolve()
tests_dir = here.parents[1]
root = next((p for p in here.parents if (p / "specforge_mobile/tools/e2e/run.py").is_file()), None)
if root is None:
    print("route_e2e: не найден specforge_mobile/tools/e2e/run.py ни в одной родительской папке", file=sys.stderr)
    sys.exit(2)
sys.path.insert(0, str(root))

from specforge_mobile.tools.e2e.run import main  # noqa: E402

args = sys.argv[1:]
if "--scenario" not in args and not os.environ.get("E2E_SCENARIO"):
    scenario = here.parent / "scenario.json"
    if not scenario.is_file():
        print("route_e2e: нет сценария (e2e/scenario.json или E2E_SCENARIO) — e2e пропущен")
        sys.exit(0)
    args += ["--scenario", str(scenario)]
if "--out" not in args:
    args += ["--out", str(tests_dir)]
if "--requirements" not in args:
    for req in (tests_dir.parent / "01_requirements/requirements.json",          # дерево артефактов
                tests_dir.parents[1] / "analysis/requirements/requirements.json"):  # опубликованный репозиторий
        if req.is_file():
            args += ["--requirements", str(req)]
            break
sys.exit(main(args))
