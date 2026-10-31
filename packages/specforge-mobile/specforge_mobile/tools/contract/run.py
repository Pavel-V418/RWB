"""
Контрактные тесты одной командой (из корня репозитория):

    python -m specforge_mobile.tools.contract.run --openapi reference/case1/02_system/openapi.yaml --base http://localhost:8080 ^
        --out artifacts/07_tests --user driver=driver.demo --user admin=admin ^
        --requirements artifacts/01_requirements/requirements.json

Пароли — только из окружения: CONTRACT_PASSWORD_<РОЛЬ> (например CONTRACT_PASSWORD_DRIVER),
или готовые токены CONTRACT_TOKEN_<РОЛЬ>. В файлы они не пишутся.

Выходы в --out:
  contract-junit.xml   — для матрицы трассируемости (теги SEC-n/FR-n в имени теста)
  raw/contract.sarif   — провалы как находки для security-контура (tools/security)
  contract.json        — все результаты
Код выхода 1 — есть проваленные проверки.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from . import checks, spec

RATE_HINT = re.compile(r"частот|rate.?limit|throttl|429 при превышении", re.I)


def sec_ids(requirements: Path | None) -> dict[str, list[str]]:
    """категория → [SEC-n] из requirements.json (номера меняются от прогона к прогону)."""
    if not requirements or not requirements.is_file():
        return {}
    data = json.loads(requirements.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for r in data.get("security", []) if isinstance(data, dict) else []:
        out.setdefault(str(r.get("category", "")).lower(), []).append(r["id"])
    return out


def tags_of(res: checks.Result, secs: dict[str, list[str]]) -> list[str]:
    return secs.get(checks.SEC_CATEGORY.get(res.category, ""), []) + res.reqs


def write_junit(path: Path, results: list[checks.Result], secs: dict[str, list[str]]) -> None:
    cases = []
    for r in results:
        tags = " ".join(tags_of(r, secs))
        name = f"{tags + ' ' if tags else ''}{r.check}: {r.title} — {r.op}"
        inner = ""
        if r.status == "failed":
            inner = f"<failure message={quoteattr(r.message)}>{escape(r.message)}</failure>"
        elif r.status == "skipped":
            inner = f"<skipped message={quoteattr(r.message)}/>"
        cases.append(f'  <testcase classname="contract.{r.category}" name={quoteattr(name)} time="{r.duration}">'
                     f"{inner}</testcase>")
    failed = sum(r.status == "failed" for r in results)
    skipped = sum(r.status == "skipped" for r in results)
    path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                    f'<testsuite name="contract" tests="{len(results)}" failures="{failed}" skipped="{skipped}">\n'
                    + "\n".join(cases) + "\n</testsuite>\n", encoding="utf-8")


def write_sarif(path: Path, results: list[checks.Result], secs: dict[str, list[str]], spec_uri: str) -> None:
    rules, items = {}, []
    for r in results:
        if r.status != "failed" or r.op == "*":   # итоговая строка дублирует находки по операциям
            continue
        cwe, sev = checks.CATEGORY[r.category]
        sev = r.severity or sev
        rule_id = f"contract.{r.check}" + ("-nodata" if r.severity else "")
        rules[rule_id] = {"id": rule_id, "shortDescription": {"text": r.title},
                          "properties": {"security-severity": sev, "tags": [cwe, "security"]}}
        items.append({"ruleId": rule_id, "level": "error",
                      "message": {"text": f"{r.op}: {r.title} — {r.message}"},
                      "locations": [{"physicalLocation": {"artifactLocation": {"uri": spec_uri}}}],
                      "properties": {"req": secs.get(checks.SEC_CATEGORY.get(r.category, ""), []),
                                     "operation": r.op}})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": "2.1.0", "runs": [{"tool": {"driver": {
        "name": "contract", "rules": list(rules.values())}}, "results": items}]}, ensure_ascii=False, indent=1),
        encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--openapi", required=True, help="файл openapi.yaml/json или URL swagger.json стенда")
    ap.add_argument("--base", required=True, help="адрес стенда, например http://localhost:8080")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--user", action="append", default=[], help="роль=логин (пароль из CONTRACT_PASSWORD_<РОЛЬ>)")
    ap.add_argument("--id", action="append", default=[], help="известные path-параметры: vehicleId=...")
    ap.add_argument("--rate-op", action="append", default=[],
                    help="operationId для проверки 429 (по умолчанию — операции, где в описании есть лимит частоты)")
    ap.add_argument("--burst", type=int, default=40)
    ap.add_argument("--requirements", type=Path)
    args = ap.parse_args(argv)

    t0 = time.monotonic()
    doc = spec.load(args.openapi)
    ops = spec.operations(doc)
    client = checks.Client(args.base)

    tokens: dict[str, str] = {}
    login_op = checks.find_login(ops)
    for item in args.user:
        role, user = item.split("=", 1)
        env = role.upper().replace("-", "_")
        if os.environ.get(f"CONTRACT_TOKEN_{env}"):
            tokens[role] = os.environ[f"CONTRACT_TOKEN_{env}"]
        elif os.environ.get(f"CONTRACT_PASSWORD_{env}") and login_op:
            tokens[role] = checks.login(client, doc, login_op, user, os.environ[f"CONTRACT_PASSWORD_{env}"])
        else:
            print(f"  роль {role}: нет CONTRACT_PASSWORD_{env}/CONTRACT_TOKEN_{env} — проверки ролей без неё")
    ids = dict(i.split("=", 1) for i in args.id)
    rate_ops = set(args.rate_op) or {o.op_id for o in ops if RATE_HINT.search(o.text)}

    results = checks.run_checks(doc, ops, client, tokens, ids, rate_ops, args.burst)
    secs = sec_ids(args.requirements)
    args.out.mkdir(parents=True, exist_ok=True)
    write_junit(args.out / "contract-junit.xml", results, secs)
    write_sarif(args.out / "raw/contract.sarif", results, secs, str(args.openapi).replace("\\", "/"))
    (args.out / "contract.json").write_text(json.dumps(
        {"base": args.base, "durationSec": round(time.monotonic() - t0, 1), "roles": sorted(tokens),
         "results": [r.__dict__ for r in results]}, ensure_ascii=False, indent=1), encoding="utf-8")

    failed = [r for r in results if r.status == "failed"]
    print(f"contract: проверок {len(results)}, провалено {len(failed)}, "
          f"пропущено {sum(r.status == 'skipped' for r in results)} → {args.out / 'contract-junit.xml'}")
    for r in failed[:15]:
        print(f"  ✗ [{r.category}] {r.op}: {r.title} — {r.message}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
