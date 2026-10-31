"""
Этап 07_tests целиком: security-контур, контракт и e2e на стенде, файлы для CI продукта.

    run(artifacts, out, llm) -> dict{passed, errors, notes, tokens, ran}

1. Security: сканеры (semgrep, gitleaks, trivy — те, что есть на хосте) по дереву артефактов →
   находки → SEC-требования → триаж (правила + LLM) → security.json, findings.json, report.md.
2. Стенд (если есть Docker и compose-файл 08_infra): docker compose up --wait → контрактные тесты из
   OpenAPI → e2e по сценарию (если задан) → docker compose down.
3. CI продукта: ci/*.gitlab-ci.yml, e2e/route_e2e.py и копия кода инструментов (specforge_mobile/) —
   в опубликованном репозитории это reports/tests/…, их подключает .gitlab-ci.yml участника 3.

Настройки (окружение):
  SPECFORGE_QA_STAND   auto (по умолчанию) | on | off — поднимать ли стенд
  STAND_URL            адрес стенда, по умолчанию http://127.0.0.1:8080
  SPECFORGE_QA_USERS   учётки контракта «роль=логин» через запятую; пароли — CONTRACT_PASSWORD_<РОЛЬ>
  E2E_SCENARIO         сценарий e2e (scenario.json); без него e2e не запускается
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]          # packages/specforge-mobile
CODE = Path(__file__).resolve().parent                  # specforge_mobile/
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
VENDOR_SUFFIXES = {".py", ".toml", ".yaml", ".yml"}


# ---------------------------------------------------------------- CI продукта
def publish_ci(out: Path, users: list[str], scenario: Path | None) -> list[str]:
    """Файлы для CI продукта в 07_tests/: ci/, e2e/, копия specforge_mobile/ (без тестов)."""
    written = []
    (out / "ci").mkdir(parents=True, exist_ok=True)
    for name in ("tests.gitlab-ci.yml", "security.gitlab-ci.yml"):
        text = (PACKAGE / "ci" / name).read_text(encoding="utf-8")
        text = text.replace("{{CONTRACT_USERS}}", " ".join(users))
        (out / "ci" / name).write_text(text, encoding="utf-8")
        written.append(f"ci/{name}")
    (out / "e2e").mkdir(exist_ok=True)
    shutil.copy2(PACKAGE / "ci/e2e/route_e2e.py", out / "e2e/route_e2e.py")
    written.append("e2e/route_e2e.py")
    if scenario and scenario.is_file():
        shutil.copy2(scenario, out / "e2e/scenario.json")
        written.append("e2e/scenario.json")
    vendor = out / "specforge_mobile"
    for f in sorted(CODE.rglob("*")):
        rel = f.relative_to(CODE)
        if (f.is_file() and f.suffix in VENDOR_SUFFIXES and "__pycache__" not in rel.parts
                and not f.name.startswith("test_")):
            dest = vendor / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
    written.append("specforge_mobile/")
    return written


# ---------------------------------------------------------------- стенд
def compose_file(artifacts: Path) -> Path | None:
    infra = artifacts / "08_infra"
    return next((infra / n for n in COMPOSE_FILES if (infra / n).is_file()), None)


def stand_wanted(artifacts: Path) -> tuple[bool, str]:
    mode = os.environ.get("SPECFORGE_QA_STAND", "auto").strip().lower()
    if mode == "off":
        return False, "стенд выключен (SPECFORGE_QA_STAND=off)"
    if compose_file(artifacts) is None:
        return False, "нет compose-файла в 08_infra — контракт и e2e на стенде не запускались"
    if not shutil.which("docker"):
        return False, "нет Docker — контракт и e2e на стенде не запускались"
    return True, ""


def _compose(artifacts: Path, *args: str, timeout: int = 900) -> tuple[int, str]:
    cf = compose_file(artifacts)
    assert cf is not None
    cmd = ["docker", "compose", "-f", str(cf), "--project-directory", str(cf.parent), *args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)[-3000:]
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def users_from_env() -> list[str]:
    raw = os.environ.get("SPECFORGE_QA_USERS", "")
    return [u.strip() for u in raw.replace(";", ",").split(",") if "=" in u]


# ---------------------------------------------------------------- этап
def _err(tool: str, message: str, file: str = "") -> dict:
    return {"tool": tool, "file": file, "line": 0, "message": message}


def run(artifacts: Path, out: Path, llm=None, *, log: Callable[[str], None] = print) -> dict:
    from .tools.contract.run import main as contract_main
    from .tools.security.run import main as security_main

    artifacts, out = Path(artifacts).resolve(), Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    errors: list[dict] = []
    notes: list[str] = []
    ran: list[str] = []
    requirements = artifacts / "01_requirements/requirements.json"
    users = users_from_env()
    scenario = Path(os.environ["E2E_SCENARIO"]) if os.environ.get("E2E_SCENARIO") else None
    tokens_before = getattr(getattr(llm, "usage", None), "total", 0)

    # 1. security — до копирования кода инструментов в out (его сканировать не нужно)
    args = ["--target", str(artifacts), "--out", str(out), "--no-fail"]
    if requirements.is_file():
        args += ["--requirements", str(requirements)]
    if llm is None:
        args.append("--no-llm")
    security_main(args, llm=llm)
    ran.append("security")
    summary = {}
    if (out / "security.json").is_file():
        summary = json.loads((out / "security.json").read_text(encoding="utf-8")).get("summary", {})
    if summary.get("status") not in (None, "PASSED"):
        errors.append(_err("security", f"security: {summary.get('status')} — блокирующих находок "
                                       f"{summary.get('blocking', '?')}, открыто {summary.get('open', '?')}",
                           "report.md"))

    # 2. стенд: контракт и e2e
    wanted, why = stand_wanted(artifacts)
    if not wanted:
        notes.append(why)
    else:
        base = os.environ.get("STAND_URL", "http://127.0.0.1:8080")
        code, text = _compose(artifacts, "up", "-d", "--build", "--wait")
        if code != 0:
            errors.append(_err("stand", f"стенд не поднялся: {text[-800:]}"))
        else:
            try:
                openapi = artifacts / "02_system/openapi.yaml"
                cargs = ["--openapi", str(openapi), "--base", base, "--out", str(out)]
                for u in users:
                    cargs += ["--user", u]
                if requirements.is_file():
                    cargs += ["--requirements", str(requirements)]
                if not users:
                    notes.append("SPECFORGE_QA_USERS не задан — контракт без проверок ролей")
                ran.append("contract")
                if contract_main(cargs) != 0:
                    errors.append(_err("contract", "есть проваленные контрактные проверки", "contract-junit.xml"))
                if scenario and scenario.is_file():
                    from .tools.e2e.run import main as e2e_main
                    ran.append("e2e")
                    eargs = ["--base", base, "--scenario", str(scenario), "--out", str(out)]
                    if requirements.is_file():
                        eargs += ["--requirements", str(requirements)]
                    if e2e_main(eargs) != 0:
                        errors.append(_err("e2e", "есть проваленные шаги e2e", "e2e-junit.xml"))
                else:
                    notes.append("E2E_SCENARIO не задан — e2e не запускался")
            finally:
                _compose(artifacts, "down", timeout=300)

    # 3. CI продукта
    publish_ci(out, users, scenario)
    tokens = getattr(getattr(llm, "usage", None), "total", 0) - tokens_before
    result = {"passed": not errors, "errors": errors, "notes": notes, "ran": ran, "tokens": tokens}
    (out / "qa_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for n in notes:
        log(f"  {n}")
    return result
