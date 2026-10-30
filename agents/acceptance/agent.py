"""Приёмщик — последний этап перед finalize. Проверяет итоговый репозиторий, а не доверяет state:
поздние этапы (ремонт, DevOps, исправления безопасности) могли сломать ранние.

1. Собирает итоговый репозиторий из artifacts/ по config/repo_layout.yaml (как publisher).
2. Заново выполняет шлюзы passed/degraded этапов на нём — параллельно, в своих песочницах;
   был passed, стал красный — «регрессия».
3. docker compose up + /health (Healthy / Degraded / Unhealthy) и /metrics (Prometheus) у backend-
   сервисов, e2e/демо-сценарий, если он есть.
4. Документы по config/doc_templates.yaml (перечень и разделы — из требований кейса): есть ли
   обязательные документы, доля разделов, идентификаторы ФТ.
5. Отчёты Trivy: уязвимости HIGH и выше — «не готово».
6. Покрытие требований по матрице трассируемости (FR → код → тест).
7. Без запуска (repo_checks): .gitlab-ci.yml в корне (job'ы тестов и безопасности, artifacts),
   секреты в файлах и закоммиченные .env, README / .env.example / миграции / тестовые данные,
   статический анализ в шлюзах и CI.

Результат — verdict.json, summary.md (резюме через LLM, профиль REVIEW), test-report.md и
security-report.md (детерминированные отчёты о тестировании и безопасности). Этап passed, если
продукт готов, иначе degraded: вердикт есть, но готовности нет.
"""

import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from agents.acceptance.checks import (
    ComposeRunner,
    CoverageCheck,
    DockerCompose,
    DocTypeCheck,
    GateCheck,
    HttpGet,
    RuntimeCheck,
    SecurityCheck,
    assemble_repo,
    check_coverage,
    check_docs,
    check_runtime,
    check_trivy,
    find_compose,
    http_get,
    load_templates,
    rerun_gates,
)
from agents.acceptance.repo_checks import (
    CiCheck,
    LaunchCheck,
    SecretsCheck,
    StaticAnalysisCheck,
    check_ci,
    check_launch,
    check_secrets,
    check_static_analysis,
)
from agents.acceptance.reports import render_security_report, render_test_report
from specforge.core.agent_api import AgentContext, BaseAgent
from specforge.core.contracts import AgentSpec, ProfileName, StageResult, StageStatus
from specforge.llm import ChatMessage, LLMError
from specforge.orchestrator.nodes import STAGE_SNAPSHOT_FILE, StageSnapshot
from specforge.orchestrator.publisher import RepoLayout
from specforge.orchestrator.registry import ACCEPTANCE_STAGE
from specforge.paths import PROJECT_ROOT

TEMPLATES = PROJECT_ROOT / "config" / "doc_templates.yaml"
REPO_DIR = "acceptance-repo"
SUMMARY_CHARS = 2500
MAX_LISTED = 20
TEST_REPORT = "test-report.md"
SECURITY_REPORT = "security-report.md"
OUTPUTS = ("verdict.json", "summary.md", TEST_REPORT, SECURITY_REPORT)


class Verdict(BaseModel):
    ready: bool
    blockers: list[str] = Field(default_factory=list)
    """Почему не готово: регрессии, красные шлюзы, запуск, уязвимости HIGH+, e2e."""
    regressions: list[str] = Field(default_factory=list)
    not_done: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    gates: list[GateCheck] = Field(default_factory=list)
    runtime: RuntimeCheck = Field(default_factory=RuntimeCheck)
    docs: list[DocTypeCheck] = Field(default_factory=list)
    security: SecurityCheck = Field(default_factory=SecurityCheck)
    coverage: CoverageCheck = Field(default_factory=CoverageCheck)
    ci: CiCheck = Field(default_factory=CiCheck)
    secrets: SecretsCheck = Field(default_factory=SecretsCheck)
    launch: LaunchCheck = Field(default_factory=LaunchCheck)
    static_analysis: StaticAnalysisCheck = Field(default_factory=StaticAnalysisCheck)
    summary: str = ""


class RepoFindings(BaseModel):
    """Проверки репозитория без запуска (repo_checks)."""

    ci: CiCheck = Field(default_factory=CiCheck)
    secrets: SecretsCheck = Field(default_factory=SecretsCheck)
    launch: LaunchCheck = Field(default_factory=LaunchCheck)
    static_analysis: StaticAnalysisCheck = Field(default_factory=StaticAnalysisCheck)


def build_verdict(
    snapshot: StageSnapshot,
    gates: list[GateCheck],
    runtime: RuntimeCheck,
    docs: list[DocTypeCheck],
    security: SecurityCheck,
    coverage: CoverageCheck,
    repo: RepoFindings | None = None,
) -> Verdict:
    """repo=None — проверки репозитория не выполнялись (их пункты в вердикт не попадают)."""
    v = Verdict(
        ready=False, gates=gates, runtime=runtime, docs=docs, security=security, coverage=coverage
    )
    _gate_items(v, gates)
    _stage_items(v, snapshot)
    _runtime_items(v, runtime)
    _doc_items(v, docs)
    _security_items(v, security)
    _coverage_items(v, coverage)
    if repo is not None:
        v.ci, v.secrets, v.launch = repo.ci, repo.secrets, repo.launch
        v.static_analysis = repo.static_analysis
        _ci_items(v, repo.ci)
        _secret_items(v, repo.secrets)
        _launch_items(v, repo.launch, runtime)
        _static_items(v, repo.static_analysis)
    v.ready = not v.blockers
    return v


def _ci_items(v: Verdict, ci: CiCheck) -> None:
    if ci.file is None:
        v.not_done.append("нет .gitlab-ci.yml в корне репозитория — GitLab CI не запустится")
    elif ci.error:
        v.blockers.append(f".gitlab-ci.yml: {ci.error}")
    else:
        v.risks += [f".gitlab-ci.yml: {p}" for p in ci.problems]


def _secret_items(v: Verdict, secrets: SecretsCheck) -> None:
    if secrets.findings:
        listed = "; ".join(secrets.findings[:5])
        v.blockers.append(f"секреты в репозитории: {len(secrets.findings)} ({listed})")
    if secrets.suspicious:
        listed = "; ".join(secrets.suspicious[:5])
        v.risks.append(f"возможные секреты: {len(secrets.suspicious)} ({listed})")


def _launch_items(v: Verdict, launch: LaunchCheck, runtime: RuntimeCheck) -> None:
    if not launch.readme:
        v.not_done.append("нет README.md в корне — инструкции по запуску для эксперта")
    if launch.needs_env and not launch.env_example:
        v.not_done.append("compose использует переменные окружения, а .env.example нет")
    if launch.uses_db and not launch.migrations:
        v.not_done.append("в compose есть PostgreSQL, а миграций БД не найдено")
    if runtime.compose_file is not None and not launch.seed_data:
        v.not_done.append("нет тестовых данных (seed / test-data / fixtures)")


def _static_items(v: Verdict, static: StaticAnalysisCheck) -> None:
    if not static.tools and not static.reports:
        v.risks.append(
            "статический анализ не выполнялся: нет анализаторов в шлюзах и CI, нет отчётов"
        )


def _gate_items(v: Verdict, gates: list[GateCheck]) -> None:
    red: list[str] = []
    for g in gates:
        detail = f"{g.total_errors} ошибок" + (f": {g.first_error}" if g.first_error else "")
        if g.regression:
            v.regressions.append(
                f"{g.agent}: шлюз был зелёным, на итоговом репозитории — красный ({detail})"
            )
        elif g.passed_now is False:
            red.append(f"{g.agent}: шлюз красный ({g.state_status.value}; {detail})")
        elif g.passed_now is None:
            v.risks.append(f"{g.agent}: шлюз не перепроверен — {g.infra_error}")
    v.blockers += v.regressions + red


def _stage_items(v: Verdict, snapshot: StageSnapshot) -> None:
    for name, result in snapshot.results.items():
        if result.status in (StageStatus.failed, StageStatus.skipped):
            v.not_done.append(f"этап {name}: {result.status.value} — {result.summary[:150]}")
        elif result.status == StageStatus.degraded:
            v.risks.append(f"этап {name} завершён degraded: {result.summary[:150]}")


def _runtime_items(v: Verdict, runtime: RuntimeCheck) -> None:
    if runtime.compose_file is None:
        v.not_done.append(runtime.note or "нет docker-compose")
    elif not runtime.checked:
        v.risks.append(runtime.note)
    elif not runtime.up_ok:
        v.blockers.append(f"docker compose up не поднял сервисы: {runtime.up_log[-300:]}")
    else:
        v.blockers += [f"{s.service}: {p}" for s in runtime.services for p in s.problems]
        if runtime.e2e_script is None:
            v.risks.append("нет e2e/демо-сценария (e2e/run.sh)")
        elif not runtime.e2e_ok:
            v.blockers.append(f"e2e {runtime.e2e_script} упал: {runtime.e2e_log[-300:]}")


def _doc_items(v: Verdict, docs: list[DocTypeCheck]) -> None:
    for d in docs:
        title = d.title or d.doc_type
        if not d.files:
            if d.required:
                v.not_done.append(f"нет документа «{title}» ({', '.join(d.paths[:2])})")
            continue
        if d.share < 1:
            missing = sorted({m.split("|")[0] for f in d.files for m in f.missing})
            v.risks.append(
                f"«{title}»: {d.share:.0%} разделов по шаблону, нет: {', '.join(missing)}"
            )
        if d.missing_patterns:
            v.risks.append(f"«{title}»: не найдено — {', '.join(d.missing_patterns)}")


def _security_items(v: Verdict, security: SecurityCheck) -> None:
    if security.blocking:
        v.blockers.append(
            f"уязвимости HIGH и выше: {len(security.blocking)} ({', '.join(security.blocking[:5])})"
        )
    if not security.reports:
        v.risks.append("нет отчёта Trivy (trivy*.json) — уязвимости не проверены")


def _coverage_items(v: Verdict, coverage: CoverageCheck) -> None:
    if coverage.matrix is None:
        v.risks.append(
            "нет матрицы трассируемости (traceability.*) — покрытие требований неизвестно"
        )
    else:
        v.not_done += [f"{r.id}: {', '.join(r.missing)}" for r in coverage.uncovered[:MAX_LISTED]]


def fallback_summary(v: Verdict) -> str:
    head = "Готово к сдаче." if v.ready else f"Не готово: {len(v.blockers)} блокирующих проблем."
    parts = [head]
    if v.regressions:
        parts.append("Регрессии: " + "; ".join(v.regressions[:3]))
    if v.coverage.matrix:
        parts.append(
            f"Покрытие требований {v.coverage.share:.0%} ({v.coverage.covered}/{v.coverage.total})."
        )
    if v.not_done:
        parts.append(f"Не сделано: {len(v.not_done)} пунктов.")
    if v.risks:
        parts.append(f"Риски: {len(v.risks)}.")
    return " ".join(parts)


class AcceptanceAgent(BaseAgent):
    spec = AgentSpec(
        name="acceptance",
        stage=ACCEPTANCE_STAGE,
        kind="structured",
        description="Приёмка итогового репозитория: регрессии, запуск, docs, Trivy, покрытие",
        inputs=["**/*"],  # кэш этапа — по всем артефактам
        outputs=list(OUTPUTS),
        profile=ProfileName.REVIEW,
        max_repairs=0,
        timeout_s=3600,
    )

    def __init__(
        self,
        *,
        layout: RepoLayout | None = None,
        templates: Path = TEMPLATES,
        compose: Callable[[str], ComposeRunner] = DockerCompose,
        http: HttpGet = http_get,
        max_workers: int = 4,
    ) -> None:
        self.layout = layout
        self.templates = templates
        self.compose = compose
        self.http = http
        self.max_workers = max_workers

    def run(self, ctx: AgentContext) -> StageResult:
        started = time.monotonic()
        snapshot_path = ctx.workdir / STAGE_SNAPSHOT_FILE
        if snapshot_path.is_file():
            snapshot = StageSnapshot.model_validate_json(snapshot_path.read_text(encoding="utf-8"))
        else:
            snapshot = StageSnapshot(results={}, specs={}, order=[])
        snapshot.results.pop(self.spec.name, None)

        repo = ctx.workdir / REPO_DIR
        shutil.rmtree(repo, ignore_errors=True)
        layout = self.layout or RepoLayout.load()
        placed = assemble_repo(ctx.input_dir, repo, layout, snapshot.order, snapshot.specs)
        gates = rerun_gates(
            repo,
            placed,
            snapshot.results,
            snapshot.specs,
            ctx.workdir / "logs",
            ctx.run_id,
            self.max_workers,
            layout=layout,
        )
        verdict = build_verdict(
            snapshot,
            gates,
            check_runtime(repo, self.compose(f"specforge-{ctx.run_id}".lower()), self.http),
            check_docs(repo, load_templates(self.templates)),
            check_trivy(repo),
            check_coverage(repo),
            RepoFindings(
                ci=check_ci(repo),
                secrets=check_secrets(repo),
                launch=check_launch(repo, find_compose(repo)),
                static_analysis=check_static_analysis(repo, snapshot.specs.values()),
            ),
        )
        if not snapshot.results:
            verdict.risks.append(f"нет {STAGE_SNAPSHOT_FILE} — состояние этапов не сверено")

        with ctx.llm.track() as tracker:
            verdict.summary = self._summary(ctx, verdict)
        (ctx.output_dir / "verdict.json").write_text(
            verdict.model_dump_json(indent=2), encoding="utf-8"
        )
        (ctx.output_dir / "summary.md").write_text(verdict.summary + "\n", encoding="utf-8")
        (ctx.output_dir / TEST_REPORT).write_text(render_test_report(verdict), encoding="utf-8")
        (ctx.output_dir / SECURITY_REPORT).write_text(
            render_security_report(verdict), encoding="utf-8"
        )
        return self.result(
            StageStatus.passed if verdict.ready else StageStatus.degraded,
            started=started,
            usage=tracker.usage,
            artifacts=list(OUTPUTS),
            summary=(
                "готово" if verdict.ready else "не готово: " + "; ".join(verdict.blockers[:3])
            )[:500],
        )

    def _summary(self, ctx: AgentContext, verdict: Verdict) -> str:
        brief = verdict.model_dump(mode="json", exclude={"summary"})
        brief["runtime"].pop("up_log", None)
        brief["runtime"].pop("e2e_log", None)
        messages = [
            ChatMessage(role="system", content=self.load_prompt("system.md")),
            ChatMessage(role="user", content=json.dumps(brief, ensure_ascii=False)[:30_000]),
        ]
        try:
            text = ctx.llm.chat(messages, profile=self.spec.profile).content.strip()
        except LLMError as exc:
            verdict.risks.append(f"резюме LLM не получено: {str(exc)[:150]}")
            return fallback_summary(verdict)
        return (text or fallback_summary(verdict))[:SUMMARY_CHARS]


AGENT = AcceptanceAgent()
