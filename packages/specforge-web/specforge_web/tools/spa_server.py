#!/usr/bin/env python3
"""Мини-сервер собранной Angular-админки для UI-ревью и демо (без ng serve и nginx).

  python tools/spa_server.py out/admin/dist/app/browser --port 4200 --mock out/admin/openapi.yaml
  python tools/spa_server.py out/admin/dist/app/browser --port 4200 --api http://localhost:8080

Отдаёт статику, для неизвестных путей — index.html (SPA fallback), /api/* проксирует на --api
(Prism-мок: --strip /api/v1, реальный backend — без --strip). /hubs не проксируется: в режиме ревью
realtime работает от mock-events.json (environment.mock) или отключён.
"""
from __future__ import annotations

import argparse
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def csp_for(root: Path) -> str | None:
    """CSP из docker/nginx.conf.template собранного проекта (dist/app/browser → корень проекта) — одна политика
    для стенда и локального прогона: нарушения видны UI-ревьюеру ещё до Docker."""
    import os
    import re
    for base in [root, *root.resolve().parents][:5]:
        tpl = base / "docker" / "nginx.conf.template"
        if tpl.exists():
            m = re.search(r'Content-Security-Policy "([^"]+)"', tpl.read_text(encoding="utf-8"))
            if m:
                return m.group(1).replace("${APP_MAP_ORIGINS}", os.environ.get("APP_MAP_ORIGINS", "https://tile.openstreetmap.org"))
    return None


class Handler(SimpleHTTPRequestHandler):
    api: str | None = None
    strip: str = ""
    mock = None  # tools.mock_api.MockApi
    csp: str | None = None

    def end_headers(self):
        if self.csp and not self.path.startswith("/api"):
            self.send_header("Content-Security-Policy", self.csp)
        super().end_headers()

    def _mock(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) or None
        code, data, ctype = self.mock.bytes(self.command, self.path, body)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass

    def _proxy(self):
        path = self.path[len(self.strip):] if self.strip and self.path.startswith(self.strip) else self.path
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) or None
        req = urllib.request.Request(self.api.rstrip("/") + path, data=body, method=self.command,
                                     headers={k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                status, headers, data = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            status, headers, data = e.code, e.headers, e.read()
        except Exception as e:  # noqa: BLE001
            status, headers, data = 502, {}, f'{{"title":"Bad gateway","status":502,"detail":"{e}"}}'.encode()
        self.send_response(status)
        for k, v in (headers.items() if headers else []):
            if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _spa(self):
        target = Path(self.translate_path(self.path.split("?", 1)[0]))
        if not target.exists() or target.is_dir() and not (target / "index.html").exists():
            self.path = "/index.html"
        return super().do_GET()

    def do_GET(self):
        if self.path.startswith("/api"):
            return self._mock() if self.mock else self._proxy() if self.api else self.send_error(404)
        return self._spa()

    def do_POST(self):
        if self.path.startswith("/api"):
            return self._mock() if self.mock else self._proxy() if self.api else self.send_error(404)
        return self.send_error(405)

    do_PUT = do_PATCH = do_DELETE = do_POST


def serve(root: Path, port: int = 4200, api: str | None = None, strip: str = "", background: bool = False, mock_openapi: Path | None = None):
    mock = None
    if mock_openapi:
        import yaml
        from specforge_web.tools.mock_api import MockApi
        mock = MockApi(yaml.safe_load(Path(mock_openapi).read_text(encoding="utf-8")))
    handler = partial(type("H", (Handler,), {"api": api, "strip": strip, "mock": mock, "csp": csp_for(Path(root))}), directory=str(root))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    if background:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd
    print(f"http://127.0.0.1:{httpd.server_address[1]}  (static: {root}, api → {api or '—'})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Остановлено.")
        httpd.server_close()
    return httpd


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--port", type=int, default=4200)
    ap.add_argument("--api")
    ap.add_argument("--strip", default="")
    ap.add_argument("--mock", type=Path, help="openapi.yaml: встроенный mock-API вместо --api")
    a = ap.parse_args()
    serve(a.root, a.port, a.api, a.strip, mock_openapi=a.mock)
