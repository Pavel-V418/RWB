"""Офлайн-тест DevOps-агента: фейковая LLM, настоящий Docker и маленький стенд вместо продукта (ключ не тратится).

    .venv/bin/python -m unittest tests.test_devops_agent
"""

import json
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

from specforge_backend.backend.llm import Usage
from specforge_backend.devops.agent import run

COMPOSE = """name: devops-agent-test-{port}
services:
  backend:
    image: python:3.13-alpine
    command: ["python", "-m", "http.server", "8080", "--directory", "/www"]
    volumes: ["./www:/www:ro"]
    ports: ["${{BACKEND_PORT}}:{container_port}"]
    healthcheck:
      test: ["CMD", "wget", "-q", "-O-", "http://127.0.0.1:8080/health/ready"]
      interval: 2s
      retries: 10
    mem_limit: 64m
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_stand(root: Path, container_port: int) -> int:
    infra = root / "08_infra"
    for path in ("health/ready", "swagger/v1/swagger.json", "metrics"):
        (infra / "www" / path).parent.mkdir(parents=True, exist_ok=True)
        (infra / "www" / path).write_text("ok", encoding="utf-8")
    port = free_port()
    (infra / "docker-compose.yml").write_text(COMPOSE.format(port=port, container_port=container_port), encoding="utf-8")
    (infra / ".env").write_text(f"BACKEND_PORT={port}\nPOSTGRES_PASSWORD=super-secret-value\n", encoding="utf-8")
    return port


def answer(payload: dict) -> str:
    return "Разбор:\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


class FakeLlm:
    def __init__(self, answers: list[str]):
        self.answers, self.prompts, self.usage = answers, [], Usage()

    def chat(self, system: str, user: str, max_tokens: int = 10000, temperature: float = 0.2) -> str:
        self.prompts.append(user)
        self.usage.calls += 1
        return self.answers.pop(0)


def down(root: Path) -> None:
    subprocess.run(["docker", "compose", "--project-directory", str(root / "08_infra"), "down", "-v"], capture_output=True)


class DevopsAgentTests(unittest.TestCase):
    def test_repairs_wrong_port_mapping_and_masks_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            port = make_stand(root, container_port=9999)  # приложение слушает 8080 — снаружи недоступно
            fixed = COMPOSE.format(port=port, container_port=8080)
            llm = FakeLlm([answer({"layer": "infra", "diagnosis": "порт контейнера 9999, а сервер слушает 8080",
                                   "files": {"08_infra/docker-compose.yml": fixed}})])
            try:
                result = run(root, root / "08_infra", llm, generate=False, wait_timeout=60, log=lambda *_: None)
            finally:
                down(root)
            self.assertTrue(result.passed, result.errors)
            self.assertEqual(result.attempts, 1)
            self.assertNotIn("super-secret-value", llm.prompts[0])
            gate = json.loads((root / "08_infra" / "gate.json").read_text(encoding="utf-8"))
            self.assertEqual(gate, {"gate": "stand", "passed": True, "errors": []})

    def test_app_layer_writes_feedback_and_policy_rejects_privileged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            port = make_stand(root, container_port=9999)
            privileged = COMPOSE.format(port=port, container_port=8080).replace("mem_limit", "privileged: true\n    mem_limit")
            llm = FakeLlm([
                answer({"layer": "infra", "diagnosis": "нужны права", "files": {"08_infra/docker-compose.yml": privileged}}),
                answer({"layer": "app", "diagnosis": "backend падает при старте: NullReferenceException в Program.cs"}),
            ])
            try:
                result = run(root, root / "08_infra", llm, generate=False, wait_timeout=60, log=lambda *_: None)
            finally:
                down(root)
            self.assertFalse(result.passed)
            self.assertIn("отклонено политикой", result.diagnosis[0]["diagnosis"])
            self.assertNotIn("privileged", (root / "08_infra" / "docker-compose.yml").read_text(encoding="utf-8"))
            feedback = (root / "08_infra" / "devops-feedback.txt").read_text(encoding="utf-8")
            self.assertIn("NullReferenceException", feedback)
            self.assertNotIn("super-secret-value", feedback)


if __name__ == "__main__":
    unittest.main()
