"""Этап 07_tests без стенда и сканеров: отчёты, файлы CI продукта, работоспособность копии кода."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from specforge_mobile import qa

SCENARIO = qa.PACKAGE / "reference/case1/mock_route/scenario.json"


def _artifacts(root: Path) -> Path:
    art = root / "artifacts"
    (art / "01_requirements").mkdir(parents=True)
    (art / "01_requirements/requirements.json").write_text(json.dumps({"security": [
        {"id": "SEC-01", "category": "authn", "statement": "JWT"}]}), encoding="utf-8")
    (art / "02_system").mkdir()
    (art / "02_system/openapi.yaml").write_text("openapi: 3.1.0\npaths: {}\n", encoding="utf-8")
    return art


class QaStageTest(unittest.TestCase):
    def test_without_stand_writes_reports_and_ci(self):
        with tempfile.TemporaryDirectory() as tmp:
            art = _artifacts(Path(tmp))
            out = art / "07_tests"
            env = {"SPECFORGE_QA_STAND": "off", "SPECFORGE_QA_USERS": "operator=op.demo, admin=admin",
                   "E2E_SCENARIO": str(SCENARIO)}
            with mock.patch.dict(os.environ, env), mock.patch("shutil.which", return_value=None):
                res = qa.run(art, out, None, log=lambda _m: None)
            self.assertTrue(res["passed"], res)
            self.assertEqual(res["ran"], ["security"])
            self.assertTrue(any("SPECFORGE_QA_STAND=off" in n for n in res["notes"]))
            for f in ("security.json", "findings.json", "report.md", "qa_report.json",
                      "ci/tests.gitlab-ci.yml", "ci/security.gitlab-ci.yml", "e2e/route_e2e.py",
                      "e2e/scenario.json", "specforge_mobile/tools/security/run.py",
                      "specforge_mobile/tools/security/gitleaks.toml",
                      "specforge_mobile/android_agent/llm.py"):
                self.assertTrue((out / f).is_file(), f)
            self.assertFalse(list((out / "specforge_mobile").rglob("test_*.py")))
            tests_ci = (out / "ci/tests.gitlab-ci.yml").read_text(encoding="utf-8")
            self.assertIn('CONTRACT_USERS: "operator=op.demo admin=admin"', tests_ci)
            self.assertNotIn("{{", tests_ci)
            self.assertNotIn("artifacts/", (out / "ci/security.gitlab-ci.yml").read_text(encoding="utf-8"))

    def test_vendored_code_runs_in_published_layout(self):
        """reports/tests/ опубликованного репозитория: CI вызывает код с PYTHONPATH=reports/tests."""
        with tempfile.TemporaryDirectory() as tmp:
            art = _artifacts(Path(tmp))
            out = art / "07_tests"
            qa.publish_ci(out, [], None)
            env = dict(os.environ, PYTHONPATH=str(out))
            p = subprocess.run([sys.executable, "-m", "specforge_mobile.tools.security.run", "--help"],
                               cwd=tmp, env=env, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            # без сценария e2e-запускатель ничего не проверяет и не роняет стадию
            p = subprocess.run([sys.executable, str(out / "e2e/route_e2e.py"), "--base", "http://127.0.0.1:9"],
                               cwd=tmp, env={k: v for k, v in os.environ.items() if k != "E2E_SCENARIO"},
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("e2e пропущен", p.stdout)

    def test_stand_skipped_without_compose(self):
        with tempfile.TemporaryDirectory() as tmp:
            art = _artifacts(Path(tmp))
            with mock.patch.dict(os.environ, {"SPECFORGE_QA_STAND": "auto"}):
                wanted, why = qa.stand_wanted(art)
            self.assertFalse(wanted)
            self.assertIn("compose", why)


if __name__ == "__main__":
    unittest.main()
