"""Проверки приёмщика над итоговым репозиторием. Внешние вызовы (docker compose, HTTP) — через
протоколы, чтобы проверки тестировались без Docker и сети."""

import concurrent.futures
import fnmatch
import json
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, Field

from specforge.core.artifacts import INPUT_STAGE, MANIFEST_FILE, SERVICE_DIRS
from specforge.core.contracts import AgentSpec, StageResult, StageStatus
from specforge.gates.runner import GateConfigError, GateRunner
from specforge.orchestrator.nodes import create_sandbox
from specforge.orchestrator.publisher import RepoLayout
from specforge.sandbox.base import SandboxError

# --- итоговый репозиторий -----------------------------------------------------------------------


def assemble_repo(
    artifacts: Path,
    target: Path,
    layout: RepoLayout,
    order: Sequence[str],
    specs: dict[str, AgentSpec],
) -> dict[str, str]:
    """Сложить artifacts/<stage>/ в target по раскладке (как publisher): этапы — в порядке плана,
    поздние перекрывают файлы ранних. Возвращает {агент: каталог в репозитории}."""
    target.mkdir(parents=True, exist_ok=True)
    stages = [INPUT_STAGE, *dict.fromkeys(specs[a].stage for a in order if a in specs)]
    placed: dict[str, str] = {}
    for stage in stages:
        source = artifacts / stage
        if not source.is_dir():
            continue
        prefix = layout.dir_of(stage) or f"_unmapped/{stage}/"
        for path in sorted(source.rglob("*")):
            rel = path.relative_to(source)
            if (
                not path.is_file()
                or rel.parts[0] in SERVICE_DIRS
                or rel.as_posix() == MANIFEST_FILE
            ):
                continue
            dest = target / prefix / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
        for agent in order:
            if agent in specs and specs[agent].stage == stage:
                placed[agent] = prefix.rstrip("/")
    return placed


# --- повтор шлюзов ------------------------------------------------------------------------------


class GateCheck(BaseModel):
    agent: str
    state_status: StageStatus
    passed_now: bool | None
    """None — не проверено (инфраструктура / песочница)."""
    total_errors: int = 0
    first_error: str = ""
    infra_error: str | None = None

    @property
    def regression(self) -> bool:
        return self.state_status == StageStatus.passed and self.passed_now is False


def rerun_gates(
    repo: Path,
    placed: dict[str, str],
    results: dict[str, StageResult],
    specs: dict[str, AgentSpec],
    logs_dir: Path,
    run_id: str,
    max_workers: int = 4,
) -> list[GateCheck]:
    """Шлюзы passed/degraded этапов на итоговом репозитории; каждый — в своей копии каталога
    этапа и своей песочнице, независимые этапы — параллельно."""
    todo = [
        name
        for name, result in results.items()
        if result.status in (StageStatus.passed, StageStatus.degraded)
        and name in specs
        and specs[name].gate_commands
        and name in placed
    ]

    def check(name: str) -> GateCheck:
        spec = specs[name].model_copy(update={"name": f"acceptance_{name}"[:60]})
        state = results[name].status
        with tempfile.TemporaryDirectory(prefix=f"acceptance-{name}-") as tmp:
            workspace = Path(tmp) / "work"
            shutil.copytree(repo / placed[name], workspace)
            try:
                sandbox = create_sandbox(spec, workspace, logs_dir)
                try:
                    gate = GateRunner(logs_dir=logs_dir).run(spec, sandbox, run_id)
                finally:
                    sandbox.close()
            except (SandboxError, GateConfigError) as exc:
                return GateCheck(
                    agent=name, state_status=state, passed_now=None, infra_error=str(exc)[:300]
                )
        if gate.infra_error:
            return GateCheck(
                agent=name, state_status=state, passed_now=None, infra_error=gate.infra_error
            )
        first = gate.diagnostics[0].message.splitlines()[0][:200] if gate.diagnostics else ""
        return GateCheck(
            agent=name,
            state_status=state,
            passed_now=gate.passed,
            total_errors=gate.total_errors,
            first_error=first,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(check, todo))


# --- docker compose и healthcheck --------------------------------------------------------------

HEALTH_STATES = ("Healthy", "Degraded", "Unhealthy")
COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
FRONTEND_NAMES = re.compile(r"web|front|ui|nginx|console|admin", re.IGNORECASE)
E2E_SCRIPTS = ("e2e/run.sh", "tests/e2e/run.sh", "scripts/e2e.sh", "demo/run.sh")
_PROM_LINE = re.compile(
    r"^(#\s*(HELP|TYPE)\s+\S+.*|[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}]*\})?\s+"
    r"(-?\d+(\.\d+)?([eE][+-]?\d+)?|NaN|[+-]Inf)(\s+\d+)?)$"
)


class ComposeRunner(Protocol):
    def available(self) -> bool: ...

    def up(self, compose_file: Path) -> tuple[bool, str]: ...

    def ports(self, compose_file: Path) -> dict[str, int]:
        """{сервис, собранный из репозитория (build): опубликованный порт}."""
        ...

    def run_script(self, script: Path, cwd: Path) -> tuple[bool, str]: ...

    def down(self, compose_file: Path) -> None: ...


HttpGet = Callable[[str], tuple[int, str]]


@dataclass
class DockerCompose:
    """Настоящий docker compose через CLI."""

    project: str
    timeout_s: float = 600

    def _run(self, args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=self.timeout_s, check=False
        )

    def available(self) -> bool:
        if shutil.which("docker") is None:
            return False
        return self._run(["docker", "compose", "version"]).returncode == 0

    def up(self, compose_file: Path) -> tuple[bool, str]:
        result = self._run(
            [
                "docker",
                "compose",
                "-p",
                self.project,
                "-f",
                str(compose_file),
                "up",
                "-d",
                "--build",
                "--wait",
                "--wait-timeout",
                "300",
            ],
            cwd=compose_file.parent,
        )
        return result.returncode == 0, (result.stdout + result.stderr)[-3000:]

    def ports(self, compose_file: Path) -> dict[str, int]:
        config = yaml.safe_load(compose_file.read_text(encoding="utf-8")) or {}
        found: dict[str, int] = {}
        for name, service in (config.get("services") or {}).items():
            if not isinstance(service, dict) or "build" not in service:
                continue
            for port in service.get("ports") or []:
                host = str(port.get("published") if isinstance(port, dict) else port).split(":")
                if len(host) >= 2 and host[-2].isdigit():
                    found[name] = int(host[-2])
                    break
        return found

    def run_script(self, script: Path, cwd: Path) -> tuple[bool, str]:
        result = self._run(["bash", str(script)], cwd=cwd)
        return result.returncode == 0, (result.stdout + result.stderr)[-3000:]

    def down(self, compose_file: Path) -> None:
        self._run(["docker", "compose", "-p", self.project, "-f", str(compose_file), "down", "-v"])


def http_get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return int(response.status), response.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(10_000).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(exc)


class ServiceHealth(BaseModel):
    service: str
    port: int
    health: str | None = None
    """Healthy / Degraded / Unhealthy или None — /health не ответил одним из них."""
    metrics_ok: bool = False
    problems: list[str] = Field(default_factory=list)


class RuntimeCheck(BaseModel):
    checked: bool = False
    compose_file: str | None = None
    up_ok: bool | None = None
    up_log: str = ""
    services: list[ServiceHealth] = Field(default_factory=list)
    e2e_script: str | None = None
    e2e_ok: bool | None = None
    e2e_log: str = ""
    note: str = ""


def find_compose(repo: Path) -> Path | None:
    for base in (repo, repo / "infra", repo / "deploy"):
        for name in COMPOSE_NAMES:
            if (base / name).is_file():
                return base / name
    return None


def is_prometheus(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return bool(lines) and all(_PROM_LINE.match(line) for line in lines)


def check_runtime(repo: Path, runner: ComposeRunner, get: HttpGet = http_get) -> RuntimeCheck:
    compose = find_compose(repo)
    if compose is None:
        return RuntimeCheck(note="нет docker-compose в итоговом репозитории")
    check = RuntimeCheck(compose_file=compose.relative_to(repo).as_posix())
    if not runner.available():
        check.note = "Docker недоступен — запуск не проверен"
        return check
    check.checked = True
    try:
        check.up_ok, check.up_log = runner.up(compose)
        if not check.up_ok:
            return check
        for service, port in sorted(runner.ports(compose).items()):
            if FRONTEND_NAMES.search(service):
                continue
            check.services.append(_service_health(service, port, get))
        script = next((repo / s for s in E2E_SCRIPTS if (repo / s).is_file()), None)
        if script is not None:
            check.e2e_script = script.relative_to(repo).as_posix()
            check.e2e_ok, check.e2e_log = runner.run_script(script, repo)
    finally:
        runner.down(compose)
    return check


def _service_health(service: str, port: int, get: HttpGet) -> ServiceHealth:
    item = ServiceHealth(service=service, port=port)
    status, body = get(f"http://localhost:{port}/health")
    state = next((s for s in HEALTH_STATES if re.search(rf"\b{s}\b", body)), None)
    if status == 0:
        item.problems.append(f"/health не отвечает: {body[:120]}")
    elif state is None:
        item.problems.append(f"/health (HTTP {status}) не вернул Healthy / Degraded / Unhealthy")
    else:
        item.health = state
        if state == "Unhealthy":
            item.problems.append("/health: Unhealthy")
    status, body = get(f"http://localhost:{port}/metrics")
    item.metrics_ok = status == 200 and is_prometheus(body)
    if not item.metrics_ok:
        item.problems.append(f"/metrics (HTTP {status}) не в формате Prometheus")
    return item


# --- документация по шаблонам -------------------------------------------------------------------


class DocFile(BaseModel):
    path: str
    share: float
    missing: list[str] = Field(default_factory=list)


class DocTypeCheck(BaseModel):
    doc_type: str
    share: float
    """Средняя доля обязательных разделов по файлам типа; 0 — документов нет."""
    files: list[DocFile] = Field(default_factory=list)


def load_templates(path: Path) -> dict[str, dict[str, list[str]]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): v for k, v in (data.get("templates") or {}).items()}


def check_docs(repo: Path, templates: dict[str, dict[str, list[str]]]) -> list[DocTypeCheck]:
    checks = []
    for doc_type, template in templates.items():
        folder = repo / "docs" / doc_type
        files: list[DocFile] = []
        for path in sorted(folder.rglob("*")) if folder.is_dir() else []:
            if path.suffix.lower() in (".md", ".markdown"):
                files.append(_doc_sections(path, repo, template.get("sections", [])))
            elif path.suffix.lower() in (".yaml", ".yml", ".json") and template.get("keys"):
                files.append(_doc_keys(path, repo, template["keys"]))
        share = sum(f.share for f in files) / len(files) if files else 0.0
        checks.append(DocTypeCheck(doc_type=doc_type, share=round(share, 3), files=files))
    return checks


def _doc_sections(path: Path, repo: Path, sections: list[str]) -> DocFile:
    headings = [
        line.lstrip("#").strip().casefold()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.startswith("#")
    ]
    missing = [
        s
        for s in sections
        if not any(v.strip().casefold() in h for v in s.split("|") for h in headings)
    ]
    share = 1 - len(missing) / len(sections) if sections else 1.0
    return DocFile(path=path.relative_to(repo).as_posix(), share=round(share, 3), missing=missing)


def _doc_keys(path: Path, repo: Path, keys: list[str]) -> DocFile:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (ValueError, yaml.YAMLError):
        data = None
    present = set(data) if isinstance(data, dict) else set()
    missing = [k for k in keys if k not in present]
    return DocFile(
        path=path.relative_to(repo).as_posix(),
        share=round(1 - len(missing) / len(keys), 3),
        missing=missing,
    )


# --- Trivy --------------------------------------------------------------------------------------

BLOCKING_SEVERITIES = ("CRITICAL", "HIGH")


class SecurityCheck(BaseModel):
    reports: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    blocking: list[str] = Field(default_factory=list)
    """HIGH и выше: «ID (пакет, severity)»."""


def check_trivy(*roots: Path) -> SecurityCheck:
    check = SecurityCheck()
    for root in roots:
        for path in sorted(root.rglob("trivy*.json")) if root.is_dir() else []:
            try:
                data: Any = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            check.reports.append(path.as_posix())
            for result in (data.get("Results") or []) if isinstance(data, dict) else []:
                for vuln in result.get("Vulnerabilities") or []:
                    severity = str(vuln.get("Severity", "UNKNOWN")).upper()
                    check.counts[severity] = check.counts.get(severity, 0) + 1
                    if severity in BLOCKING_SEVERITIES:
                        vid, pkg = vuln.get("VulnerabilityID", "?"), vuln.get("PkgName", "?")
                        check.blocking.append(f"{vid} ({pkg}, {severity})")
    return check


# --- трассируемость ------------------------------------------------------------------------------

TRACE_NAMES = ("traceability", "трассируемость")


class Requirement(BaseModel):
    id: str
    code: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class CoverageCheck(BaseModel):
    matrix: str | None = None
    total: int = 0
    covered: int = 0
    share: float = 0.0
    uncovered: list[Requirement] = Field(default_factory=list)


def find_matrix(repo: Path) -> Path | None:
    candidates = [
        p
        for p in sorted(repo.rglob("*"))
        if p.is_file()
        and any(n in p.stem.casefold() for n in TRACE_NAMES)
        and p.suffix.lower() in (".yaml", ".yml", ".json", ".md", ".csv")
    ]
    return candidates[0] if candidates else None


def check_coverage(repo: Path) -> CoverageCheck:
    """FR → код → тест: требование покрыто, если у него есть и код, и тест, и пути есть
    в репозитории (glob-шаблоны допускаются)."""
    matrix = find_matrix(repo)
    if matrix is None:
        return CoverageCheck()
    rows = _read_matrix(matrix)
    check = CoverageCheck(matrix=matrix.relative_to(repo).as_posix(), total=len(rows))
    for row in rows:
        row.missing = [
            f"{kind}: {ref}"
            for kind, refs in (("код", row.code), ("тест", row.tests))
            for ref in refs
            if not _exists(repo, ref)
        ]
        if not row.code:
            row.missing.append("код: не указан")
        if not row.tests:
            row.missing.append("тест: не указан")
        if row.missing:
            check.uncovered.append(row)
        else:
            check.covered += 1
    check.share = round(check.covered / check.total, 3) if check.total else 0.0
    return check


def _exists(repo: Path, ref: str) -> bool:
    ref = ref.strip().split("#", 1)[0].split("::", 1)[0].strip("/ ")
    if not ref:
        return False
    if any(ch in ref for ch in "*?["):
        return any(fnmatch.fnmatch(p.relative_to(repo).as_posix(), ref) for p in repo.rglob("*"))
    return (repo / ref).exists() or any(
        p.as_posix().endswith("/" + ref) for p in repo.rglob(Path(ref).name)
    )


def _refs(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in re.split(r"[,;\n]|<br\s*/?>", str(value)) if v.strip()]


def _read_matrix(path: Path) -> list[Requirement]:
    text = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml", ".json"):
        data: Any = json.loads(text) if suffix == ".json" else yaml.safe_load(text)
        if isinstance(data, dict):
            data = data.get("requirements", [{"id": k, **(v or {})} for k, v in data.items()])
        return [
            Requirement(
                id=str(item.get("id") or item.get("fr") or item.get("requirement")),
                code=_refs(item.get("code")),
                tests=_refs(item.get("tests") or item.get("test")),
            )
            for item in data or []
            if isinstance(item, dict)
        ]
    return _table_rows(text, "," if suffix == ".csv" else "|")


def _table_rows(text: str, sep: str) -> list[Requirement]:
    lines = [line.strip().strip("|") for line in text.splitlines() if sep in line]
    lines = [line for line in lines if not re.fullmatch(r"[\s|:\-]+", line)]
    if not lines:
        return []
    header = [h.strip().casefold() for h in lines[0].split(sep)]

    def column(*names: str) -> int | None:
        return next((i for i, h in enumerate(header) if any(n in h for n in names)), None)

    fr, code, test = column("fr", "id", "требован"), column("код", "code"), column("тест", "test")
    if fr is None:
        return []
    rows = []
    for line in lines[1:]:
        cells = [c.strip() for c in line.split(sep)]
        if len(cells) <= fr or not cells[fr]:
            continue
        rows.append(
            Requirement(
                id=cells[fr],
                code=_refs(cells[code]) if code is not None and code < len(cells) else [],
                tests=_refs(cells[test]) if test is not None and test < len(cells) else [],
            )
        )
    return rows
