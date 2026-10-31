"""Выбор шлюза Android (docker / local / нечем) и разбор лога Gradle из контейнера — без Docker и Gradle."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from specforge_mobile.android_agent import toolchain


class ChooseTest(unittest.TestCase):
    def test_explicit_modes(self):
        for mode, expected in (("none", None), ("docker", "docker"), ("local", "local")):
            with mock.patch.dict(os.environ, {"SPECFORGE_ANDROID_GATE": mode}):
                self.assertEqual(toolchain.choose(), expected)

    def test_auto_prefers_docker_then_local(self):
        with mock.patch.dict(os.environ, {"SPECFORGE_ANDROID_GATE": "auto"}):
            with mock.patch.object(toolchain, "docker_image_available", return_value=True):
                self.assertEqual(toolchain.choose(), "docker")
            with mock.patch.object(toolchain, "docker_image_available", return_value=False), \
                    mock.patch.object(toolchain, "local_toolchain_available", return_value=True):
                self.assertEqual(toolchain.choose(), "local")
            with mock.patch.object(toolchain, "docker_image_available", return_value=False), \
                    mock.patch.object(toolchain, "local_toolchain_available", return_value=False):
                self.assertIsNone(toolchain.choose())

    def test_no_toolchain_gate_is_explicit_failure(self):
        res = toolchain.no_toolchain_gate(Path("."))
        self.assertFalse(res["passed"])
        self.assertEqual(res["errors"][0]["category"], "toolchain")


class DockerGateTest(unittest.TestCase):
    def test_container_paths_mapped_to_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            src = project / "app/src/main/kotlin/com/specforge/app/feature/x"
            src.mkdir(parents=True)
            (src / "XScreen.kt").write_text("package x\n\nfun a() = b\n", encoding="utf-8")
            (project / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
            log = ("e: file:///work/app/src/main/kotlin/com/specforge/app/feature/x/XScreen.kt:3:11 "
                   "Unresolved reference 'b'.\nBUILD FAILED\n")
            done = subprocess.CompletedProcess(args=[], returncode=1, stdout=log, stderr="")
            with mock.patch("subprocess.run", return_value=done) as run:
                res = toolchain.docker_gate(project)
            cmd = run.call_args.args[0]
            self.assertEqual(cmd[:2], ["docker", "run"])
            self.assertIn("--offline", cmd)
            self.assertIn("none", cmd)  # --network none
            self.assertFalse(res["passed"])
            err = res["errors"][0]
            self.assertEqual(err["file"], "app/src/main/kotlin/com/specforge/app/feature/x/XScreen.kt")
            self.assertEqual(err["line"], 3)
            self.assertTrue((project / "build/gate.log").is_file())


if __name__ == "__main__":
    unittest.main()
