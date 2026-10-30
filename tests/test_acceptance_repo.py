"""Приёмщик без запуска: CI, секреты, готовность к запуску, статический анализ, документы по
шаблонам из требований кейса, отчёты о тестировании и безопасности."""

from pathlib import Path

from agents.acceptance.agent import TEMPLATES, RepoFindings, build_verdict
from agents.acceptance.checks import (
    CoverageCheck,
    GateCheck,
    RuntimeCheck,
    SecurityCheck,
    check_docs,
    load_templates,
)
from agents.acceptance.repo_checks import (
    check_ci,
    check_launch,
    check_secrets,
    check_static_analysis,
)
from agents.acceptance.reports import render_security_report, render_test_report
from specforge.core.contracts import AgentSpec, StageStatus
from specforge.orchestrator.nodes import StageSnapshot

EMPTY = StageSnapshot(results={}, specs={}, order=[])


def write(root: Path, rel: str, text: str = "x") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_gitlab_ci(tmp_path: Path) -> None:
    assert check_ci(tmp_path).file is None
    write(tmp_path, ".gitlab-ci.yml", "build: [unclosed")
    assert check_ci(tmp_path).error.startswith("невалидный YAML")

    write(tmp_path, ".gitlab-ci.yml", "stages: [build]\nbuild:\n  script: [dotnet build]\n")
    weak = check_ci(tmp_path)
    assert weak.valid
    assert weak.jobs == ["build"]
    assert len(weak.problems) == 3  # нет тестов, безопасности, artifacts

    write(
        tmp_path,
        ".gitlab-ci.yml",
        "stages: [build, test]\n"
        ".base: {image: x}\n"
        "unit:\n  stage: test\n  script: [dotnet test]\n"
        "  artifacts: {reports: {junit: [r.xml]}}\n"
        "scan:\n  stage: test\n  script: [trivy fs --format json -o trivy.json .]\n",
    )
    good = check_ci(tmp_path)
    assert good.jobs == ["scan", "unit"]
    assert good.problems == []


def test_secrets(tmp_path: Path) -> None:
    write(tmp_path, "infra/key.pem", "-----BEGIN RSA PRIVATE KEY-----\nabc\n")
    write(tmp_path, "backend/appsettings.json", '{"aws": "AKIAABCDEFGHIJKLMNOP"}')
    write(tmp_path, ".env", "DB_PASSWORD=real")
    write(tmp_path, ".env.example", "DB_PASSWORD=changeme")
    write(tmp_path, "docker-compose.yml", "environment:\n  password: ${DB_PASSWORD}\n")
    write(tmp_path, "backend/Config.cs", 'var password = "Sup3rS3cretValue";')
    write(tmp_path, "backend/tests/AuthTests.cs", 'var password = "Sup3rS3cretValue";')
    write(tmp_path, "web/node_modules/lib/key.pem", "-----BEGIN PRIVATE KEY-----")

    found = check_secrets(tmp_path)
    assert sorted(found.findings) == [
        ".env: файл окружения в репозитории",
        "backend/appsettings.json:1: ключ AWS",
        "infra/key.pem:1: приватный ключ",
    ]
    assert found.suspicious == ["backend/Config.cs:1: password задан литералом"]


def test_launch_readiness_and_static_analysis_in_verdict(tmp_path: Path) -> None:
    compose = write(
        tmp_path,
        "docker-compose.yml",
        "services:\n  db: {image: postgis/postgis:17-3.5}\n"
        "  api: {build: backend, environment: {DB: '${DB_URL}'}}\n",
    )
    launch = check_launch(tmp_path, compose)
    assert (launch.readme, launch.env_example, launch.needs_env, launch.uses_db) == (
        False,
        False,
        True,
        True,
    )
    no_tools = check_static_analysis(tmp_path, [AgentSpec(name="a", stage="a", kind="tool")])
    verdict = build_verdict(
        EMPTY,
        [],
        RuntimeCheck(compose_file="docker-compose.yml"),
        [],
        SecurityCheck(reports=["trivy.json"]),
        CoverageCheck(),
        RepoFindings(ci=check_ci(tmp_path), launch=launch, static_analysis=no_tools),
    )
    assert "нет .gitlab-ci.yml в корне репозитория — GitLab CI не запустится" in verdict.not_done
    assert any("README.md" in n for n in verdict.not_done)
    assert any(".env.example" in n for n in verdict.not_done)
    assert any("миграций" in n for n in verdict.not_done)
    assert any("тестовых данных" in n for n in verdict.not_done)
    assert any(r.startswith("статический анализ не выполнялся") for r in verdict.risks)

    write(tmp_path, "db/migrations/001_init.sql", "create table t (id int);")
    write(tmp_path, "db/seed/demo.sql", "insert into t values (1);")
    ready = check_launch(tmp_path, compose)
    assert ready.migrations == ["db/migrations/001_init.sql"]
    assert ready.seed_data == ["db/seed/demo.sql"]
    lint = AgentSpec(name="w", stage="web", kind="tool", gate_commands=["npx eslint . -f json"])
    assert check_static_analysis(tmp_path, [lint]).tools == ["eslint"]


def test_project_doc_templates_follow_case_requirements(tmp_path: Path) -> None:
    templates = load_templates(TEMPLATES)
    sa = templates["system_analysis"]
    assert len(sa["sections"]) == 14
    write(
        tmp_path,
        "analysis/system-analysis.md",
        "\n".join(f"## {s.split('|')[0]}" for s in sa["sections"]) + "\n- FR-1: вход\n",
    )
    write(tmp_path, "README.md", "# Проект\n## Запуск\n")
    docs = {d.doc_type: d for d in check_docs(tmp_path, templates)}
    assert docs["system_analysis"].share == 1.0
    assert docs["system_analysis"].missing_patterns == []
    assert docs["run_guide"].share < 1

    verdict = build_verdict(
        EMPTY, [], RuntimeCheck(), list(docs.values()), SecurityCheck(), CoverageCheck()
    )
    assert "нет документа «Руководство пользователя» (docs/user*guide*.md, docs/user/**/*.md)" in (
        verdict.not_done
    )
    assert not any("Отчёт о тестировании" in n for n in verdict.not_done)  # required: false
    assert any(r.startswith("«Инструкция по запуску»") for r in verdict.risks)

    write(tmp_path, "analysis/system-analysis.md", "## Назначение\nбез идентификаторов\n")
    sa_only = check_docs(tmp_path, {"system_analysis": sa})[0]
    assert sa_only.missing_patterns == ["идентификаторы функциональных требований"]


def test_test_and_security_reports() -> None:
    gates = [
        GateCheck(
            agent="backend", state_status=StageStatus.passed, passed_now=True, tests_executed=42
        ),
        GateCheck(
            agent="web",
            state_status=StageStatus.passed,
            passed_now=False,
            total_errors=2,
            first_error="TS2307: Cannot find | module",
        ),
    ]
    security = SecurityCheck(
        reports=["trivy.json"], counts={"HIGH": 1, "LOW": 3}, blocking=["CVE-1 (libx, HIGH)"]
    )
    verdict = build_verdict(
        EMPTY, gates, RuntimeCheck(), [], security, CoverageCheck(), RepoFindings()
    )
    tests_md = render_test_report(verdict)
    assert "**Итоговый статус:** не пройдено" in tests_md
    assert "| backend | passed | зелёный | 42 | 0 |  |" in tests_md
    assert "| web | passed | **регрессия** | — | 2 | TS2307: Cannot find \\| module |" in tests_md
    assert "Всего выполнено тестов: 42." in tests_md
    assert "web: шлюз был зелёным" in tests_md  # регрессия — дефект
    assert "уязвимости HIGH" not in tests_md  # уязвимости — в отчёте безопасности

    sec_md = render_security_report(verdict)
    assert "**Итоговый статус:** не пройдено." in sec_md
    assert "| HIGH | 1 |" in sec_md
    assert "- CVE-1 (libx, HIGH)" in sec_md
    clean = build_verdict(EMPTY, [], RuntimeCheck(), [], SecurityCheck(), CoverageCheck())
    assert "не проверено: нет отчёта сканера" in render_security_report(clean)
