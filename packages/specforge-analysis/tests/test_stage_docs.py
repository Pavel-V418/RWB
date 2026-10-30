import json
import shutil
from pathlib import Path

import pytest

from specforge_analysis import stage_docs, stage_requirements, stage_system
from specforge_analysis.llm import FakeLLM, Metrics

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "cases" / "case1_parking"
FIX = ROOT / "golden" / "case1_parking" / "llm"
SAMPLES = Path(__file__).parent / "fixtures" / "team_samples"


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory):
    root = tmp_path_factory.mktemp("artifacts")
    stage_requirements.run(CASE, root / "01_requirements", FakeLLM(FIX, metrics=Metrics()))
    stage_system.run(root / "01_requirements", root / "02_system", FakeLLM(FIX, metrics=Metrics()), check_db=False)
    return root


def _copy(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst)
    return dst


def test_docs_without_team_inputs_pass_with_pending(artifacts, tmp_path):
    root = _copy(artifacts, tmp_path / "a")
    res = stage_docs.run(root, root / "09_docs", FakeLLM(FIX, metrics=Metrics()))
    assert res.passed, [str(e) for e in res.errors]
    docs = root / "09_docs" / "docs"
    assert "Ожидается: результаты тестов" in (docs / "test-report.md").read_text(encoding="utf-8")
    assert "Ожидается: инструкция по запуску" in (docs / "run.md").read_text(encoding="utf-8")
    assert any(w.code == "W_DOC_PENDING" for w in res.warnings)
    assert (docs / "adr" / "ADR-001.md").exists()
    assert "contracts/openapi.yaml" in (docs / "system-analysis.md").read_text(encoding="utf-8")


def test_docs_pick_up_team_results(artifacts, tmp_path):
    root = _copy(artifacts, tmp_path / "b")
    for sub in SAMPLES.iterdir():
        shutil.copytree(sub, root / sub.name, dirs_exist_ok=True)
    (root / "01_requirements" / "metrics.jsonl").write_text(
        '{"name": "extract", "input_tokens": 100, "output_tokens": 50, "duration_s": 1.0, "attempt": 1}\n',
        encoding="utf-8")
    res = stage_docs.run(root, root / "09_docs", FakeLLM(FIX, metrics=Metrics()))
    assert res.passed, [str(e) for e in res.errors]
    docs = root / "09_docs" / "docs"
    report = json.loads((root / "09_docs" / "gate_report.json").read_text(encoding="utf-8"))
    assert report["pending_sections"] == 0
    assert report["team_inputs"]["tests"] == 5 and report["team_inputs"]["screens"] == 2

    tests = (docs / "test-report.md").read_text(encoding="utf-8")
    assert "есть падения: 1" in tests and "expected 409 got 201" in tests
    sec = (docs / "security-report.md").read_text(encoding="utf-8")
    assert "открыто критичных и высоких: 1" in sec and "CVE-2024-0001" in sec
    trace = (docs / "traceability.md").read_text(encoding="utf-8")
    assert "Активные сессии" in trace
    assert "docker compose up -d --build" in (docs / "run.md").read_text(encoding="utf-8")
    assert (docs / "img" / "sessions.png").exists()
    limits = (docs / "limitations.md").read_text(encoding="utf-8")
    assert "CVE-2024-0001" in limits


def test_mkdocs_nav_lists_all_pages(artifacts, tmp_path):
    root = _copy(artifacts, tmp_path / "c")
    stage_docs.run(root, root / "09_docs", FakeLLM(FIX, metrics=Metrics()))
    yml = (root / "09_docs" / "mkdocs.yml").read_text(encoding="utf-8")
    for page in stage_docs.REQUIRED_PAGES:
        assert page in yml


def test_adr_and_service_passport_follow_templates(artifacts, tmp_path):
    root = _copy(artifacts, tmp_path / "d")
    stage_docs.run(root, root / "09_docs", FakeLLM(FIX, metrics=Metrics()))
    docs = root / "09_docs" / "docs"
    adr = (docs / "adr" / "ADR-001.md").read_text(encoding="utf-8")
    for h in ("## Контекст и постановка проблемы", "## Статус", "## Альтернативы", "Плюсы:", "Минусы:",
              "## Принятое решение", "## Последствия принятого решения", "## Ответственные"):
        assert h in adr, h
    svc = (docs / "services" / "backend-api.md").read_text(encoding="utf-8")
    for h in ("## Описание", "## Ссылки", "## Требования к ресурсам", "## Отказоустойчивость", "## Авторизация",
              "## Описание окружений", "## Сборка", "## Деплой", "## Конфигурация", "## Переменные окружения для CI/CD",
              "## Мониторинг", "## Логирование", "## Сетевая связность", "## Доступы к сервису"):
        assert h in svc, h
    assert (docs / "api-methods" / "startSession.md").exists()


def test_ui_docs_and_trace_from_participant4(artifacts, tmp_path):
    root = _copy(artifacts, tmp_path / "e")
    ui = root / "03_ux" / "docs"
    ui.mkdir(parents=True)
    (ui / "screens-catalog.md").write_text("# Каталог экранов\n\nТекст.\n", encoding="utf-8")
    (root / "03_ux" / "traceability.json").write_text('{"FR-3": ["home"]}', encoding="utf-8")
    res = stage_docs.run(root, root / "09_docs", FakeLLM(FIX, metrics=Metrics()))
    assert res.passed, [str(e) for e in res.errors]
    docs = root / "09_docs" / "docs"
    assert (docs / "ui" / "screens-catalog.md").exists()
    assert '"Каталог экранов": ui/screens-catalog.md' in (root / "09_docs" / "mkdocs.yml").read_text(encoding="utf-8")
    assert "home" in (docs / "traceability.md").read_text(encoding="utf-8")


def test_backend_owner_overrides_passport_and_adds_adr(artifacts, tmp_path):
    root = _copy(artifacts, tmp_path / "f")
    (root / "08_infra" / "services").mkdir(parents=True)
    (root / "08_infra" / "services" / "backend-api.md").write_text("# Backend API\n\n## Сборка\n\ndotnet build\n",
                                                                     encoding="utf-8")
    (root / "08_infra" / "adr").mkdir()
    (root / "08_infra" / "adr" / "outbox.md").write_text("# Outbox и Kafka\n\nТекст.\n", encoding="utf-8")
    stage_docs.run(root, root / "09_docs", FakeLLM(FIX, metrics=Metrics()))
    docs = root / "09_docs" / "docs"
    assert "dotnet build" in (docs / "services" / "backend-api.md").read_text(encoding="utf-8")
    assert "Outbox и Kafka" in (docs / "adr" / "index.md").read_text(encoding="utf-8")


def test_security_findings_linked_to_requirements(artifacts, tmp_path):
    root = _copy(artifacts, tmp_path / "g")
    req = json.loads((root / "01_requirements" / "requirements.json").read_text(encoding="utf-8"))
    sec_id = req["security"][0]["id"]
    (root / "07_tests").mkdir(exist_ok=True)
    (root / "07_tests" / "security.json").write_text(json.dumps({"findings": [
        {"tool": "gitleaks", "id": "generic-api-key", "title": "Ключ в коде", "severity": "high",
         "status": "fixed", "req": sec_id}]}), encoding="utf-8")
    stage_docs.run(root, root / "09_docs", FakeLLM(FIX, metrics=Metrics()))
    page = (root / "09_docs" / "docs" / "security-report.md").read_text(encoding="utf-8")
    assert "## По требованиям безопасности" in page and f"| {sec_id} |" in page
