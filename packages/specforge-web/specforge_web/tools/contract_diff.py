#!/usr/bin/env python3
"""Сверка фронтенда с реальным backend (шаг 10): работает ли админка против того API, который реально поднят.

  python tools/contract_diff.py --spec artifacts/03_ux/screen-spec.web.json \
      --api http://localhost:8080/swagger/v1/swagger.json [--was artifacts/02_system/openapi.yaml] [--json]

1. Спека экранов проверяется шлюзом UX против фактического OpenAPI: каждая операция, параметр и поле,
   которые использует интерфейс, должны существовать у backend.
2. Если задан --was (OpenAPI, по которому генерировался клиент): список операций, у которых изменились
   метод, путь, параметры или тип ответа, — их клиент нужно перегенерировать.
Код возврата 1, если интерфейс сломается на этом backend.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

from specforge_web.agents.ux_designer.gate import run_gate  # noqa: E402
from specforge_web.generators.angular.openapi_ts import TsGen  # noqa: E402


def load_api(src: str) -> dict:
    if src.startswith(("http://", "https://")):
        with urllib.request.urlopen(src, timeout=20) as r:
            text = r.read().decode("utf-8")
    else:
        text = Path(src).read_text(encoding="utf-8")
    return json.loads(text) if text.lstrip().startswith("{") else yaml.safe_load(text)


def normalize(api: dict) -> dict:
    """OpenAPI 3.0 (Swashbuckle) → поведение как у 3.1 для наших проверок: nullable → type [..., 'null']."""
    def walk(n):
        if isinstance(n, dict):
            if n.get("nullable") is True and isinstance(n.get("type"), str):
                n["type"] = [n["type"], "null"]
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(api)
    return api


def op_changes(was: dict, now: dict) -> list[dict]:
    a, b = TsGen(was).ops, TsGen(now).ops
    out = []
    for op, m in a.items():
        n = b.get(op)
        if not n:
            out.append({"operationId": op, "change": "удалена у backend"})
            continue
        diffs = []
        if (m.method, m.path) != (n.method, n.path):
            diffs.append(f"{m.method.upper()} {m.path} → {n.method.upper()} {n.path}")
        lost = sorted(set(m.query_params + m.path_params) - set(n.query_params + n.path_params))
        if lost:
            diffs.append(f"нет параметров: {', '.join(lost)}")
        if m.response_type != n.response_type or m.response_kind != n.response_kind:
            diffs.append(f"ответ {m.response_kind}:{m.response_type} → {n.response_kind}:{n.response_type}")
        if m.body_type != n.body_type:
            diffs.append(f"тело {m.body_type} → {n.body_type}")
        if diffs:
            out.append({"operationId": op, "change": "; ".join(diffs)})
    out += [{"operationId": op, "change": "новая у backend"} for op in b if op not in a]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", type=Path, required=True)
    ap.add_argument("--api", required=True, help="OpenAPI backend: файл или URL")
    ap.add_argument("--was", help="OpenAPI, по которому генерировался клиент")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    spec = json.loads(a.spec.read_text(encoding="utf-8"))
    api = normalize(load_api(a.api))
    res = run_gate(spec, api, None)
    changes = op_changes(normalize(load_api(a.was)), api) if a.was else []
    broken = [e for e in res.errors if e.severity == "error"]
    if a.json:
        print(json.dumps({"compatible": not broken, "errors": [e.message for e in broken],
                          "warnings": [e.message for e in res.errors if e.severity == "warning"], "operationChanges": changes}, ensure_ascii=False, indent=2))
    else:
        print("Интерфейс совместим с backend" if not broken else f"Интерфейс НЕ совместим с backend: {len(broken)} проблем")
        for e in broken:
            print(f"  ✗ {e.message}")
        for c in changes:
            print(f"  ~ {c['operationId']}: {c['change']}")
        if changes:
            print("→ перегенерируйте админку: python -m agents.frontend.agent --in artifacts --out artifacts/05_web --offline")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
