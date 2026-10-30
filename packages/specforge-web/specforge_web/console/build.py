"""Сборка веб-консоли конвейера нашим же генератором: спека консоли + console-api.yaml → Angular-проект.

  python -m console.build --out console/app        # затем: cd console/app && npm install && npm run build
  python -m console.demo_server --dist console/app/dist/app/browser   # демо без оркестратора
  Docker: ORCHESTRATOR_URL=http://<оркестратор>:<порт> — nginx пересылает /console-api/* туда (SSE без буферизации)

Слоты (живой граф, метрики, форма запуска) написаны вручную в console/slots и копируются ДО генерации —
генератор считает их реализованными и не трогает.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

from specforge_web.agents.ux_designer.gate import run_gate  # noqa: E402
from specforge_web.generators.angular.generate import generate  # noqa: E402

HERE = Path(__file__).resolve().parent
API = ROOT / "contracts" / "console" / "console-api.yaml"


CONSOLE_API_LOCATION = """  location /console-api/ {
    proxy_pass ${ORCHESTRATOR_URL};
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;
    client_max_body_size 12m;
  }
"""


def build(out: Path) -> dict:
    spec = json.loads((HERE / "spec" / "screen-spec.web.json").read_text(encoding="utf-8"))
    req = json.loads((HERE / "spec" / "requirements.json").read_text(encoding="utf-8"))
    api = yaml.safe_load(API.read_text(encoding="utf-8"))
    res = run_gate(spec, api, req)
    if not res.passed:
        raise SystemExit("спека консоли не прошла шлюз UX:\n" + "\n".join(e.message for e in res.blocking))
    slots_dir = out / "src" / "app" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    for f in (HERE / "slots").iterdir():
        shutil.copy2(f, slots_dir / f.name)
    summary = generate(spec, api, out)
    env = out / "src" / "environments" / "environment.ts"
    env.write_text(env.read_text(encoding="utf-8").replace("apiBaseUrl: '/api/v1'", "apiBaseUrl: '/console-api/v1'")
                   .replace("realtime: 'signalr'", "realtime: 'off'"), encoding="utf-8")
    # контейнер консоли: /console-api → оркестратор участника 1 (тот же origin — CSP connect-src 'self' не мешает);
    # SSE без буферизации, загрузка ТЗ до 10 МБ (по умолчанию nginx пускает только 1 МБ)
    nginx = out / "docker" / "nginx.conf.template"
    conf = nginx.read_text(encoding="utf-8")
    if "location /console-api/" not in conf:
        conf = conf.replace("  location /api/ {", CONSOLE_API_LOCATION + "\n  location /api/ {", 1)
        nginx.write_text(conf, encoding="utf-8")
    docker = out / "Dockerfile"
    df = docker.read_text(encoding="utf-8")
    if "ORCHESTRATOR_URL" not in df:
        df = df.replace("ENV BACKEND_URL=http://backend:8080 \\\n    APP_REALTIME=signalr",
                        "ENV BACKEND_URL=http://backend:8080 \\\n    ORCHESTRATOR_URL=http://orchestrator:8765 \\\n    APP_REALTIME=off")
        docker.write_text(df, encoding="utf-8")
    proxy = out / "proxy.conf.json"
    proxy.write_text(json.dumps({"/console-api": {"target": "http://localhost:8765", "secure": False, "changeOrigin": True}}, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=HERE / "app")
    a = ap.parse_args()
    s = build(a.out)
    print(json.dumps({"pages": [p["id"] for p in s["pages"]], "slotsImplemented": s["slotsImplemented"], "slotsTotal": s["slotsTotal"]}, ensure_ascii=False))
