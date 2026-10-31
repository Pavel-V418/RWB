"""
Тесты security-контура без сканеров и без LLM (секунды):
    python -m unittest specforge_mobile.tools.security.test_security -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from specforge_mobile.tools.security import classify, report, triage
from specforge_mobile.tools.security.findings import Finding, load_reports, norm_severity
from specforge_mobile.tools.security.run import main

REPO = Path(__file__).resolve().parents[3]

SEMGREP = {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "Semgrep OSS", "rules": [
    {"id": "python.lang.security.audit.formatted-sql-query", "defaultConfiguration": {"level": "error"},
     "properties": {"tags": ["CWE-89: Improper Neutralization of Special Elements used in an SQL Command"]}},
    {"id": "specforge.log-of-secret-or-token", "defaultConfiguration": {"level": "error"},
     "properties": {"tags": ["CWE-532: Insertion of Sensitive Information into Log File"]}},
    {"id": "tests.only", "defaultConfiguration": {"level": "warning"}, "properties": {"tags": ["CWE-89"]}}]}},
    "results": [
        {"ruleId": "python.lang.security.audit.formatted-sql-query", "level": "error",
         "message": {"text": "SQL-запрос собирается форматированием строки"},
         "locations": [{"physicalLocation": {"artifactLocation": {"uri": "app/db.py"}, "region": {"startLine": 3}}}]},
        {"ruleId": "specforge.log-of-secret-or-token", "level": "error", "message": {"text": "токен в логе"},
         "locations": [{"physicalLocation": {"artifactLocation": {"uri": "app/Auth.kt"}, "region": {"startLine": 2}}}]},
        {"ruleId": "tests.only", "level": "warning", "message": {"text": "sql в тесте"},
         "locations": [{"physicalLocation": {"artifactLocation": {"uri": "tests/test_db.py"}, "region": {"startLine": 1}}}]},
    ]}]}
GITLEAKS = {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "gitleaks", "rules": [{"id": "generic-api-key"}]}},
    "results": [
        {"ruleId": "generic-api-key", "message": {"text": "generic-api-key has detected secret for file app/config.py."},
         "locations": [{"physicalLocation": {"artifactLocation": {"uri": "app/config.py"}, "region": {"startLine": 1}}}]},
        {"ruleId": "generic-api-key", "message": {"text": "generic-api-key has detected secret for file .env.example."},
         "locations": [{"physicalLocation": {"artifactLocation": {"uri": "infra/.env.example"}, "region": {"startLine": 2}}}]},
    ]}]}
TRIVY = {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "Trivy", "rules": [
    {"id": "CVE-2024-0001", "properties": {"security-severity": "9.8", "tags": ["vulnerability", "CRITICAL"]}},
    {"id": "CVE-2024-0002", "properties": {"security-severity": "5.3"}}]}},
    "results": [
        {"ruleId": "CVE-2024-0001", "level": "error", "message": {"text": "Package: libfoo\nInstalled: 1.0\nFixed Version: 1.1"},
         "locations": [{"physicalLocation": {"artifactLocation": {"uri": "api:latest"}}}]},
        {"ruleId": "CVE-2024-0002", "level": "warning", "message": {"text": "Package: libbar"},
         "locations": [{"physicalLocation": {"artifactLocation": {"uri": "api:latest"}}}]}]}]}
ZAP = {"site": [{"@name": "http://stand", "alerts": [
    {"pluginid": "40012", "alert": "Cross Site Scripting (Reflected)", "riskcode": "3", "cweid": "79",
     "instances": [{"uri": "http://stand/api/v1/search?q=x", "method": "GET"}]},
    {"pluginid": "10038", "alert": "Content Security Policy (CSP) Header Not Set", "riskcode": "2", "cweid": "693",
     "instances": [{"uri": "http://stand/", "method": "GET"}]}]}]}
SEC_REQS = [{"id": "SEC-01", "category": "authn", "statement": "вход по JWT"},
            {"id": "SEC-03", "category": "privacy", "statement": "координаты не в логах"},
            {"id": "SEC-05", "category": "input_validation", "statement": "валидация ввода"},
            {"id": "SEC-07", "category": "secrets", "statement": "секреты в переменных окружения"}]


class FakeLLM:
    def __init__(self):
        self.prompts = []

    def chat(self, messages, **_):
        self.prompts.append(messages[-1]["content"])
        items = json.loads(messages[-1]["content"].split("Находки:\n", 1)[1])
        verdicts = [{"id": i["id"], "verdict": "false_positive" if i["location"].startswith("tests/") else "confirmed",
                     "reason": "проверено", "fix": "исправить"} for i in items]
        return type("R", (), {"text": json.dumps(verdicts), "tokens": 100})()


def make_workspace(d: Path) -> Path:
    raw = d / "out/raw"
    raw.mkdir(parents=True)
    for name, data in (("semgrep.sarif", SEMGREP), ("gitleaks.sarif", GITLEAKS),
                       ("trivy-image-api.sarif", TRIVY), ("zap.json", ZAP)):
        (raw / name).write_text(json.dumps(data), encoding="utf-8")
    (d / "app").mkdir()
    (d / "app/db.py").write_text("def q(x):\n    sql = 'x'\n    return f'SELECT * FROM t WHERE a={x}'\n", encoding="utf-8")
    (d / "app/Auth.kt").write_text("fun a(token: String) {\n  Log.d(\"T\", token)\n}\n", encoding="utf-8")
    (d / "app/config.py").write_text("API_KEY = 'sk-abcdef0123456789abcdef'\n", encoding="utf-8")
    return raw


class Parsing(unittest.TestCase):
    def test_all_formats_and_severity(self):
        with tempfile.TemporaryDirectory() as d:
            raw = make_workspace(Path(d))
            findings, sources = load_reports(raw, Path(d))
            by = {(f.tool, f.rule): f for f in findings}
            self.assertEqual(len(findings), 9)
            self.assertEqual(by[("semgrep", "python.lang.security.audit.formatted-sql-query")].cwe, ["CWE-89"])
            self.assertEqual(by[("trivy-image", "CVE-2024-0001")].severity, "critical")
            self.assertEqual(by[("gitleaks", "generic-api-key")].severity, "high")
            self.assertEqual(by[("zap", "40012")].severity, "high")
            self.assertIn("zap.json", sources)
        self.assertEqual(norm_severity("7.5"), "high")
        self.assertEqual(norm_severity("note"), "low")

    def test_fingerprint_survives_line_shift(self):
        a = Finding("semgrep", "r", "msg", "high", "a.py:10")
        b = Finding("semgrep", "r", "msg", "high", "a.py:42")
        self.assertEqual(a.id, b.id)


class Classification(unittest.TestCase):
    def test_category_and_req_by_category(self):
        with tempfile.TemporaryDirectory() as d:
            findings, _ = load_reports(make_workspace(Path(d)), Path(d))
            classify.classify(findings, SEC_REQS)
            by = {(f.tool, f.rule): f for f in findings}
            self.assertEqual(by[("semgrep", "python.lang.security.audit.formatted-sql-query")].req, ["SEC-05"])
            self.assertEqual(by[("semgrep", "specforge.log-of-secret-or-token")].req, ["SEC-03"])
            self.assertEqual(by[("gitleaks", "generic-api-key")].req, ["SEC-07"])
            self.assertEqual(by[("zap", "40012")].req, ["SEC-05"])
            self.assertEqual(by[("trivy-image", "CVE-2024-0001")].category, "dependencies")
            self.assertEqual(by[("zap", "10038")].category, "config")
            self.assertEqual(by[("zap", "10038")].req, ["SEC-03"])   # config → privacy (ближайшая)


class TriageAndPolicy(unittest.TestCase):
    def test_rules_llm_secrets_not_sent_and_blocking(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            findings, _ = load_reports(make_workspace(root), root)
            classify.classify(findings, SEC_REQS)
            llm, cache = FakeLLM(), {}
            stats = triage.llm_triage(findings, root, llm, cache, log=lambda _: None)
            classify.apply_policy(findings)
            by = {(f.tool, f.location.split(":")[0]): f for f in findings}

            sent = "\n".join(llm.prompts)
            self.assertNotIn("sk-abcdef0123456789abcdef", sent)          # секрет не ушёл в LLM
            self.assertIn("SELECT * FROM t", sent)                       # а код для обычной находки ушёл
            self.assertEqual(by[("gitleaks", "infra/.env.example")].status, "false_positive")   # правило
            self.assertEqual(by[("semgrep", "tests/test_db.py")].status, "false_positive")      # LLM
            self.assertTrue(by[("gitleaks", "app/config.py")].blocking)
            self.assertTrue(by[("semgrep", "app/db.py")].blocking)        # подтверждено, high
            crit = [f for f in findings if f.rule == "CVE-2024-0001"][0]
            medium_cve = [f for f in findings if f.rule == "CVE-2024-0002"][0]
            self.assertTrue(crit.blocking)
            self.assertFalse(medium_cve.blocking)
            self.assertEqual(stats["calls"], 1)

            # повторный триаж — из кэша, без вызовов LLM
            findings2, _ = load_reports(root / "out/raw", root)
            classify.classify(findings2, SEC_REQS)
            llm2 = FakeLLM()
            stats2 = triage.llm_triage(findings2, root, llm2, cache, log=lambda _: None)
            self.assertEqual(stats2["calls"], 0)
            self.assertGreater(stats2["cached"], 0)

    def test_llm_down_does_not_crash_or_block(self):
        class Down:
            def chat(self, *a, **k):
                raise RuntimeError("429")
        with tempfile.TemporaryDirectory() as d:
            findings, _ = load_reports(make_workspace(Path(d)), Path(d))
            classify.classify(findings, SEC_REQS)
            cache = {}
            triage.llm_triage(findings, Path(d), Down(), cache, log=lambda _: None)
            self.assertEqual(cache, {})   # временный сбой не должен попасть в кэш
            classify.apply_policy(findings)
            sql = [f for f in findings if f.rule.endswith("formatted-sql-query")][0]
            self.assertFalse(sql.blocking)
            self.assertTrue(sql.verdict_reason.startswith("требует проверки"))


class EndToEnd(unittest.TestCase):
    def test_cli_report_baseline_and_team_reader(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            make_workspace(root)
            reqs = root / "requirements.json"
            reqs.write_text(json.dumps({"security": SEC_REQS}), encoding="utf-8")
            out = root / "out"
            code = main(["--target", str(root), "--out", str(out), "--requirements", str(reqs),
                         "--no-scan", "--no-llm"])
            self.assertEqual(code, 1)   # секрет в app/config.py блокирует
            md = (out / "report.md").read_text(encoding="utf-8")
            self.assertIn("FAILED", md)
            self.assertIn("## По требованиям безопасности", md)
            self.assertIn("| SEC-07 | secrets |", md)
            # SEC-01 authn: ZAP/Semgrep не доказывают требование — нужен контрактный тест
            self.assertIn("| SEC-01 | authn | вход по JWT | zap, semgrep | ⚠️ сканеры чисто, нужна проверка: contract |", md)

            # «исправили» секрет и SQL: убираем результаты из сырых отчётов и сканируем снова
            g = json.loads((out / "raw/gitleaks.sarif").read_text(encoding="utf-8"))
            g["runs"][0]["results"] = g["runs"][0]["results"][1:]
            (out / "raw/gitleaks.sarif").write_text(json.dumps(g), encoding="utf-8")
            t = json.loads((out / "raw/trivy-image-api.sarif").read_text(encoding="utf-8"))
            t["runs"][0]["results"] = t["runs"][0]["results"][1:]
            (out / "raw/trivy-image-api.sarif").write_text(json.dumps(t), encoding="utf-8")
            code = main(["--target", str(root), "--out", str(out), "--requirements", str(reqs),
                         "--no-scan", "--no-llm"])
            data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
            self.assertEqual(code, 0)
            self.assertEqual(data["summary"]["fixed"], 2)
            self.assertIn("Исправлено после повторного скана", (out / "report.md").read_text(encoding="utf-8"))

            # совместимость с модулем участника 2 (если доступен)
            sys.path.insert(0, str(REPO / "team_inputs/specforge-analysis"))
            try:
                from specforge_analysis import team_inputs as ti_mod
            except Exception as e:  # нет pyyaml и т. п.
                self.skipTest(f"модуль участника 2 недоступен: {e}")
            ti = ti_mod.TeamInputs() if hasattr(ti_mod, "TeamInputs") else None
            tmp = root / "art/07_tests"
            tmp.mkdir(parents=True)
            (tmp / "security.json").write_text((out / "security.json").read_text(encoding="utf-8"), encoding="utf-8")
            ti_mod.read_security(root / "art", ti)
            self.assertEqual(len(ti.findings), data["summary"]["total"])
            self.assertTrue(any(f.reqs == ["SEC-07"] for f in ti.findings))
            self.assertTrue(any(f.status == "fixed" for f in ti.findings))


if __name__ == "__main__":
    unittest.main()
