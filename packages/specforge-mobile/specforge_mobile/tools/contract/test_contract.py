"""
Тесты контрактных проверок на фейковом API с заложенными дефектами (без сети и стенда):
    python -m unittest specforge_mobile.tools.contract.test_contract -v
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from specforge_mobile.tools.contract.run import main

SPEC = {
    "openapi": "3.1.0", "security": [{"bearerAuth": []}],
    "paths": {
        "/auth/tokens": {"post": {"operationId": "login", "x-roles": [], "security": [],
                                  "description": "Выдаёт JWT. Ограничен по частоте запросов.",
                                  "requestBody": {"content": {"application/json": {"schema": {
                                      "type": "object", "required": ["login", "password"],
                                      "properties": {"login": {"type": "string"},
                                                     "password": {"type": "string", "minLength": 8}}}}}},
                                  "responses": {"200": {}, "401": {}, "429": {}}}},
        "/items": {"get": {"operationId": "listItems", "x-roles": ["admin", "driver"], "x-requirements": ["FR-3"],
                           "responses": {"200": {}, "401": {}}}},
        "/items/{itemId}": {"get": {"operationId": "getItem", "x-roles": ["admin", "driver"],
                                    "parameters": [{"name": "itemId", "in": "path", "required": True,
                                                    "schema": {"type": "string", "format": "uuid"}}],
                                    "responses": {"200": {}, "401": {}, "404": {}}}},
        "/audit": {"get": {"operationId": "listAudit", "x-roles": ["admin"], "responses": {"200": {}, "403": {}}}},
        "/broken": {"get": {"operationId": "broken", "x-roles": ["driver"], "responses": {"200": {}}}},
        "/orders": {"post": {"operationId": "createOrder", "x-roles": ["driver"], "x-idempotent": True,
                             "parameters": [{"name": "Idempotency-Key", "in": "header", "required": True,
                                             "schema": {"type": "string"}}],
                             "requestBody": {"content": {"application/json": {"schema": {
                                 "type": "object", "required": ["qty"], "properties": {"qty": {"type": "integer"}}}}}},
                             "responses": {"201": {}, "400": {}}}},
    },
    "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}},
}
TOKENS = {"tok-driver": "driver", "tok-admin": "admin"}


class FakeApi(BaseHTTPRequestHandler):
    login_attempts = 0

    def log_message(self, *a):  # тишина в выводе тестов
        pass

    def reply(self, code, body=None, headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        data = json.dumps(body or {}).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def role(self):
        auth = self.headers.get("Authorization", "")
        return TOKENS.get(auth.removeprefix("Bearer "))

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/auth/tokens":
            FakeApi.login_attempts += 1
            if FakeApi.login_attempts > 15:
                return self.reply(429, headers={"Retry-After": "1"})
            pw = os.environ.get("FAKE_PW")
            if body.get("password") == pw:
                return self.reply(200, {"accessToken": f"tok-{body['login']}"})
            return self.reply(401)
        if self.path == "/orders":
            if not self.role():
                return self.reply(401)
            if not self.headers.get("Idempotency-Key"):
                return self.reply(400)
            return self.reply(201, {"id": "o1"})
        self.reply(404)

    def do_GET(self):
        role = self.role()
        if self.path == "/broken" and not role:
            return self.reply(500)                       # дефект: 500 вместо 401
        if not role:
            return self.reply(401)
        if self.path == "/items":
            return self.reply(200, [])
        if self.path.startswith("/items/"):
            return self.reply(200, {"id": "x"})          # дефект: любой id отдаётся (IDOR)
        if self.path == "/audit":
            return self.reply(200, [])                   # дефект: водитель видит журнал админа
        if self.path == "/broken":
            return self.reply(200, {})
        self.reply(404)


class ContractChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_finds_injected_defects_and_feeds_security_report(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "openapi.json").write_text(json.dumps(SPEC), encoding="utf-8")
            reqs = d / "requirements.json"
            reqs.write_text(json.dumps({"security": [
                {"id": "SEC-01", "category": "authn", "statement": "JWT"},
                {"id": "SEC-02", "category": "authz", "statement": "роли"},
                {"id": "SEC-06", "category": "rate_limiting", "statement": "429"},
                {"id": "SEC-07", "category": "secrets", "statement": "секреты"}]}), encoding="utf-8")
            os.environ.update({"FAKE_PW": "test-pw-123", "CONTRACT_PASSWORD_DRIVER": "test-pw-123",
                               "CONTRACT_PASSWORD_ADMIN": "test-pw-123"})
            FakeApi.login_attempts = 0
            out = d / "07_tests"
            code = main(["--openapi", str(d / "openapi.json"), "--base", self.base, "--out", str(out),
                         "--user", "driver=driver", "--user", "admin=admin", "--burst", "30",
                         "--requirements", str(reqs)])
            self.assertEqual(code, 1)
            data = json.loads((out / "contract.json").read_text(encoding="utf-8"))
            failed = {(r["check"], r["op"]) for r in data["results"] if r["status"] == "failed"}
            self.assertIn(("authz-object", "GET /items/{itemId}"), failed)          # IDOR
            self.assertIn(("authz-role", "GET /audit"), failed)                     # водитель видит admin
            self.assertIn(("authn-missing", "GET /broken"), failed)                 # 500 вместо 401
            self.assertIn(("no-5xx", "GET /broken"), failed)
            passed = {(r["check"], r["op"]) for r in data["results"] if r["status"] == "passed"}
            self.assertIn(("authn-missing", "GET /items"), passed)
            self.assertIn(("idem-missing-key", "POST /orders"), passed)
            self.assertIn(("idem-replay", "POST /orders"), passed)
            self.assertIn(("rate-limit", "POST /auth/tokens"), passed)
            self.assertNotIn(("authn-missing", "POST /auth/tokens"), passed | failed)  # анонимная — не проверяется

            # JUnit: теги в имени теста (участник 2)
            names = [c.get("name") for c in ET.parse(out / "contract-junit.xml").getroot().iter("testcase")]
            self.assertTrue(any(n.startswith("SEC-01 FR-3 authn-missing") for n in names), names[:5])

            # security-контур видит контракт как основную проверку authn/authz/rate_limiting
            from specforge_mobile.tools.security.run import main as sec_main
            sec_main(["--target", str(d), "--out", str(out), "--requirements", str(reqs), "--no-scan", "--no-llm",
                      "--no-fail"])
            md = (out / "report.md").read_text(encoding="utf-8")
            self.assertIn("| SEC-06 | rate_limiting | 429 | contract | ✅ нарушений не найдено |", md)
            self.assertRegex(md, r"\| SEC-02 \| authz \| роли \| contract \| ❌ открыто: \d")
            self.assertRegex(md, r"\| SEC-01 \| authn \| JWT \| contract \| ❌ открыто: 2 \(блокирует 2\)")
            self.assertIn("FAILED", md)   # подтверждённые провалы прав доступа блокируют
            # пароль нигде не сохранён
            for f in out.rglob("*"):
                if f.is_file():
                    self.assertNotIn("test-pw-123", f.read_text(encoding="utf-8", errors="ignore"), f)


if __name__ == "__main__":
    unittest.main()
