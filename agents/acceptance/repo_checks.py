"""Проверки итогового репозитория без запуска: CI, секреты, готовность к запуску из чистого
окружения, следы статического анализа. Только детерминированный разбор файлов, без сети."""

import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from specforge.core.contracts import AgentSpec

SKIP_DIRS = frozenset(
    {".git", ".specforge", "node_modules", "bin", "obj", "build", ".gradle", "dist", ".venv"}
)
MAX_SCAN_BYTES = 1_000_000

# --- GitLab CI ----------------------------------------------------------------------------------

CI_FILE = ".gitlab-ci.yml"
CI_RESERVED = frozenset(
    {
        "stages",
        "variables",
        "default",
        "include",
        "workflow",
        "image",
        "services",
        "before_script",
        "after_script",
        "cache",
        "pages",
    }
)
_TEST_JOB = re.compile(r"test|e2e|spec", re.IGNORECASE)
_SECURITY_JOB = re.compile(
    r"trivy|sast|security|dependency|audit|scan|semgrep|gitleaks|secret", re.IGNORECASE
)


class CiCheck(BaseModel):
    file: str | None = None
    valid: bool = False
    jobs: list[str] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    """Замечания (риски): нет job'ов тестов / безопасности, результаты не сохраняются."""
    error: str = ""


def check_ci(repo: Path) -> CiCheck:
    path = repo / CI_FILE
    if not path.is_file():
        return CiCheck()
    check = CiCheck(file=CI_FILE)
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except yaml.YAMLError as exc:
        check.error = f"невалидный YAML: {str(exc).splitlines()[0][:200]}"
        return check
    if not isinstance(data, dict):
        check.error = "верхний уровень — не словарь job'ов"
        return check
    jobs = {
        str(name): job
        for name, job in data.items()
        if str(name) not in CI_RESERVED
        and not str(name).startswith(".")
        and isinstance(job, dict)
        and ({"script", "trigger", "extends"} & set(job))
    }
    check.valid = bool(jobs)
    check.jobs = sorted(jobs)
    if not jobs:
        check.error = "нет ни одного job со script"
        return check
    described = {
        name: f"{name} {job.get('stage', '')} {job.get('script', '')}" for name, job in jobs.items()
    }
    if not any(_TEST_JOB.search(d) for d in described.values()):
        check.problems.append("нет job'а тестов")
    if not any(_SECURITY_JOB.search(d) for d in described.values()):
        check.problems.append("нет job'а проверки безопасности (Trivy / SAST / зависимости)")
    if not any("artifacts" in job for job in jobs.values()) and "artifacts" not in (
        data.get("default") or {}
    ):
        check.problems.append("результаты проверок не сохраняются (нет artifacts)")
    return check


# --- секреты ------------------------------------------------------------------------------------

_HARD_SECRETS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("приватный ключ", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("ключ AWS", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("токен GitLab", re.compile(r"\bglpat-[0-9A-Za-z_-]{20,}")),
    ("токен GitHub", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}")),
    ("токен Slack", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}")),
)
_ASSIGNED = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|private[_-]?key)\b"
    r"[\"']?\s*[:=]\s*[\"']?(?P<value>[^\s\"'`,;]{8,})"
)
_PLACEHOLDER = re.compile(
    r"(?i)^(\$\{.*\}|\$\(.*\)|<.*>|\{\{.*\}\}|%.*%|.*(change|example|your|dummy|placeholder|"
    r"xxx|redacted|secret_here|test|sample|local|dev).*)$"
)
_NO_SCAN_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".jar", ".dll")
_EXAMPLE_NAMES = re.compile(r"(?i)\.(example|sample|template|dist)$|example|sample")
_ENV_FILE = re.compile(r"^\.env(\.[\w-]+)?$")


class SecretsCheck(BaseModel):
    findings: list[str] = Field(default_factory=list)
    """Явные секреты (ключи, токены) и закоммиченные .env — блокеры."""
    suspicious: list[str] = Field(default_factory=list)
    """Пароли и ключи, присвоенные литералом, — риск (бывают тестовые)."""
    scanned_files: int = 0


def check_secrets(repo: Path) -> SecretsCheck:
    check = SecretsCheck()
    for path in _files(repo):
        rel = path.relative_to(repo).as_posix()
        if _ENV_FILE.match(path.name) and not _EXAMPLE_NAMES.search(path.name):
            check.findings.append(f"{rel}: файл окружения в репозитории")
            continue
        if path.suffix.lower() in _NO_SCAN_SUFFIXES or path.stat().st_size > MAX_SCAN_BYTES:
            continue
        check.scanned_files += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            for kind, pattern in _HARD_SECRETS:
                if pattern.search(line):
                    check.findings.append(f"{rel}:{number}: {kind}")
            match = _ASSIGNED.search(line)
            if (
                match
                and not _PLACEHOLDER.match(match["value"])
                and not _EXAMPLE_NAMES.search(path.name)
                and "test" not in rel.lower()
            ):
                check.suspicious.append(f"{rel}:{number}: {match.group(1)} задан литералом")
    return check


# --- запуск из чистого окружения ----------------------------------------------------------------

_ENV_REF = re.compile(r"\$\{?[A-Z_][A-Z0-9_]*|env_file")
_DB_IMAGE = re.compile(r"postgres|postgis", re.IGNORECASE)
MIGRATION_GLOBS = (
    "**/migrations/**/*",
    "**/Migrations/*.cs",
    "**/db/*.sql",
    "**/schema*.sql",
    "**/flyway/**/*.sql",
    "**/liquibase/**/*",
)
SEED_GLOBS = (
    "**/*seed*",
    "**/*Seed*",
    "**/test-data/**/*",
    "**/testdata/**/*",
    "**/demo-data/**/*",
    "**/fixtures/**/*",
    "**/*demo*.json",
    "**/*demo*.sql",
)


class LaunchCheck(BaseModel):
    readme: bool = False
    env_example: bool = False
    needs_env: bool = False
    """compose ссылается на переменные окружения / env_file."""
    uses_db: bool = False
    migrations: list[str] = Field(default_factory=list)
    seed_data: list[str] = Field(default_factory=list)


def check_launch(repo: Path, compose_file: Path | None) -> LaunchCheck:
    compose = compose_file.read_text(encoding="utf-8", errors="replace") if compose_file else ""
    return LaunchCheck(
        readme=(repo / "README.md").is_file(),
        env_example=any((repo / n).is_file() for n in (".env.example", ".env.sample")),
        needs_env=bool(_ENV_REF.search(compose)),
        uses_db=bool(_DB_IMAGE.search(compose)),
        migrations=_matches(repo, MIGRATION_GLOBS),
        seed_data=_matches(repo, SEED_GLOBS),
    )


# --- статический анализ -------------------------------------------------------------------------

_ANALYZERS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(rx, re.IGNORECASE))
    for name, rx in (
        ("eslint", r"\beslint\b|\bng\s+lint\b"),
        ("Roslyn (-warnaserror)", r"warnaserror|TreatWarningsAsErrors|dotnet\s+format"),
        ("detekt", r"\bdetekt\b"),
        ("ktlint", r"\bktlint\b"),
        ("Android Lint", r"\blint(Debug|Release)?\b"),
        ("SonarQube", r"\bsonar"),
        ("Semgrep", r"\bsemgrep\b"),
        ("Spectral", r"\bspectral\b"),
        ("hadolint", r"\bhadolint\b"),
        ("Trivy config", r"\btrivy\s+config\b"),
    )
)
REPORT_GLOBS = ("**/*.sarif", "**/semgrep*.json", "**/eslint*.json", "**/detekt*.xml")


class StaticAnalysisCheck(BaseModel):
    tools: list[str] = Field(default_factory=list)
    """Анализаторы в шлюзах этапов и в .gitlab-ci.yml."""
    reports: list[str] = Field(default_factory=list)


def check_static_analysis(repo: Path, specs: Iterable[AgentSpec]) -> StaticAnalysisCheck:
    sources = [c for s in specs for c in s.gate_commands]
    ci = repo / CI_FILE
    if ci.is_file():
        sources.append(ci.read_text(encoding="utf-8", errors="replace"))
    text = "\n".join(sources)
    return StaticAnalysisCheck(
        tools=[name for name, rx in _ANALYZERS if rx.search(text)],
        reports=_matches(repo, REPORT_GLOBS),
    )


# --- общее --------------------------------------------------------------------------------------


def _files(repo: Path) -> Iterator[Path]:
    for path in sorted(repo.rglob("*")):
        rel = path.relative_to(repo)
        if path.is_file() and not (set(rel.parts[:-1]) & SKIP_DIRS) and rel.parts[0] != "_unmapped":
            yield path


def _matches(repo: Path, globs: Iterable[str], limit: int = 20) -> list[str]:
    found: dict[str, None] = {}
    for pattern in globs:
        for path in repo.glob(pattern):
            rel = path.relative_to(repo)
            if path.is_file() and not (set(rel.parts) & SKIP_DIRS):
                found.setdefault(rel.as_posix(), None)
    return sorted(found)[:limit]
