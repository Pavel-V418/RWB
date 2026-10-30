#!/usr/bin/env python3
"""ts-check: быстрая проверка типов сгенерированного Angular-проекта без node_modules.

Подменяет @angular/core, rxjs, @angular/common/http, @angular/router, @angular/forms
минимальными типами (tools/ts-shims) и запускает tsc --noEmit. Внешние UI-пакеты (primeng,
maplibre, signalr) становятся any, а связи «страница ↔ ApiService ↔ модели ↔ хелперы»
проверяются по-настоящему. Секунды вместо минут `ng build` — пре-шлюз для frontend-агента.

  python tools/ts_check.py <project_dir> [--json]
Требует tsc (TSC=путь или в PATH / /opt/npm-tools).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SHIMS = Path(__file__).resolve().parent / "ts-shims"
# Шум от пакетов, которые остались без типов (any) — не ошибки нашего кода.
IGNORED = {"TS2307", "TS18046", "TS7016"}
RE_ERR = re.compile(r"^(?P<file>[^(]+)\((?P<line>\d+),(?P<col>\d+)\): error (?P<code>TS\d+): (?P<msg>.*)$")


def find_tsc() -> str | None:
    for c in (os.environ.get("TSC"), shutil.which("tsc"), "/opt/npm-tools/node_modules/typescript/bin/tsc"):
        if c and Path(c).exists():
            return c
    return None


def run(project: Path) -> list[dict]:
    tsc = find_tsc()
    if not tsc:
        raise SystemExit("tsc не найден (задайте TSC)")
    src = (project / "src").resolve()
    cfg = {
        "compilerOptions": {
            "strict": True, "noImplicitAny": False, "noImplicitOverride": True, "noImplicitReturns": True,
            "skipLibCheck": True, "experimentalDecorators": True, "useDefineForClassFields": False,
            "target": "ES2022", "module": "ES2022", "moduleResolution": "bundler", "lib": ["ES2022", "dom"],
            "noEmit": True, "types": [],
            "paths": {
                "@angular/core": [str(SHIMS / "angular-core.d.ts")],
                "@angular/core/rxjs-interop": [str(SHIMS / "rxjs-interop.d.ts")],
                "@angular/platform-browser": [str(SHIMS / "platform-browser.d.ts")],
                "primeng/api": [str(SHIMS / "primeng-api.d.ts")],
                "rxjs": [str(SHIMS / "rxjs.d.ts")],
                "@angular/common/http": [str(SHIMS / "angular-common-http.d.ts")],
                "@angular/common": [str(SHIMS / "angular-common.d.ts")],
                "@angular/router": [str(SHIMS / "angular-router.d.ts")],
                "@angular/forms": [str(SHIMS / "angular-forms.d.ts")],
                "@playwright/test": [str(SHIMS / "playwright-test.d.ts")],
            },
        },
        # e2e тоже: сценарии генерируются, ошибка генератора в тесте должна ловиться до запуска Playwright
        "include": [str(src / "**" / "*.ts"), str((project / "e2e").resolve() / "**" / "*.ts"), str(SHIMS / "node-process.d.ts")],
        "exclude": [str(src / "**" / "*.spec.ts"), str(src / "testing" / "**")],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        cfg_path = f.name
    proc = subprocess.run([tsc, "-p", cfg_path], capture_output=True, text=True)
    os.unlink(cfg_path)
    out = []
    for line in proc.stdout.splitlines():
        m = RE_ERR.match(line.strip())
        if m and m["code"] not in IGNORED:
            file = m["file"]
            try:
                file = str(Path(file).resolve().relative_to(project.resolve()))
            except ValueError:
                pass
            out.append({"file": file, "line": int(m["line"]), "code": m["code"], "message": m["msg"]})
    return out


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    project = Path(args[0]) if args else Path(".")
    errs = run(project)
    if "--json" in args:
        print(json.dumps(errs, ensure_ascii=False, indent=2))
    else:
        for e in errs:
            print(f"{e['file']}:{e['line']}: {e['code']}: {e['message']}")
        print(f"ts-check: {len(errs)} errors")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
