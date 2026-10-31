#!/usr/bin/env python3
"""
e2e демо-сценарий участника 5 (mock-маршрут, сессия, журнал событий, уведомления, права, аудит).

CI (стадия e2e, участник 3) запускает каждый 07_tests/e2e/*.py так:
    python3 07_tests/e2e/route_e2e.py --base $STAND_URL

Сам сценарий — tools/e2e/run.py; этот файл только находит его и подставляет пути:
  --out           по умолчанию папка 07_tests, где лежит этот файл (e2e-junit.xml, e2e.json, raw/e2e.sarif);
  --requirements  по умолчанию ../01_requirements/requirements.json, если он есть (теги SEC).
Пароли — только из окружения: CONTRACT_PASSWORD_DRIVER, CONTRACT_PASSWORD_ADMIN.
Другой сценарий — переменная E2E_SCENARIO. Код выхода 1 — есть проваленные шаги.
"""
import sys
from pathlib import Path

here = Path(__file__).resolve()
tests_dir = here.parents[1]
root = next((p for p in here.parents if (p / "tools/e2e/run.py").is_file()), None)
if root is None:
    print("route_e2e: не найден tools/e2e/run.py ни в одной родительской папке", file=sys.stderr)
    sys.exit(2)
sys.path.insert(0, str(root))

from tools.e2e.run import main  # noqa: E402

args = sys.argv[1:]
if "--out" not in args:
    args += ["--out", str(tests_dir)]
if "--requirements" not in args:
    req = tests_dir.parent / "01_requirements" / "requirements.json"
    if req.is_file():
        args += ["--requirements", str(req)]
sys.exit(main(args))
