"""Приёмщик — последний этап перед finalize. Проверяет итоговый репозиторий, а не доверяет state:
поздние этапы (ремонт, DevOps, исправления безопасности) могли сломать ранние.

1. Собирает итоговый репозиторий из artifacts/ по config/repo_layout.yaml (как publisher).
2. Заново выполняет шлюзы passed/degraded этапов на нём — параллельно, в своих песочницах;
   был passed, стал красный — «регрессия».
3. docker compose up + /health (Healthy / Degraded / Unhealthy) и /metrics (Prometheus) у backend-
   сервисов, e2e/демо-сценарий, если он есть.
4. Документация docs/{tz,adr,api,services} — доля обязательных разделов по шаблонам
   config/doc_templates.yaml.
5. Отчёты Trivy: уязвимости HIGH и выше — «не готово».
6. Покрытие требований по матрице трассируемости (FR → код → тест).

Результат — verdict.json и summary.md (резюме через LLM, профиль REVIEW). Этап passed, если
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
    http_get,
    load_templates,
    rerun_gates,
)
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
    summary: str = ""


def build_verdict(
    snapshot: StageSnapshot,
    gates: list[GateCheck],
    runtime: RuntimeCheck,
    docs: list[DocTypeCheck],
    security: SecurityCheck,
    coverage: CoverageCheck,
) -> Verdict:
    v = Verdict(
        ready=False, gates=gates, runtime=runtime, docs=docs, security=security, coverage=coverage
    )
    _gate_items(v, gates)
    _stage_items(v, snapshot)
    _runtime_items(v, runtime)
    _doc_items(v, docs)
    _security_items(v, security)
    _coverage_items(v, coverage)
    v.ready = not v.blockers
    return v


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
        if not d.files:
            v.not_done.append(f"документация docs/{d.doc_type}: нет документов")
        elif d.share < 1:
            missing = sorted({m for f in d.files for m in f.missing})
            v.risks.append(
                f"docs/{d.doc_type}: {d.share:.0%} разделов по шаблону, нет: {', '.join(missing)}"
            )


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
        outputs=["verdict.json", "summary.md"],
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
        )
        if not snapshot.results:
            verdict.risks.append(f"нет {STAGE_SNAPSHOT_FILE} — состояние этапов не сверено")

        with ctx.llm.track() as tracker:
            verdict.summary = self._summary(ctx, verdict)
        (ctx.output_dir / "verdict.json").write_text(
            verdict.model_dump_json(indent=2), encoding="utf-8"
        )
        (ctx.output_dir / "summary.md").write_text(verdict.summary + "\n", encoding="utf-8")
        return self.result(
            StageStatus.passed if verdict.ready else StageStatus.degraded,
            started=started,
            usage=tracker.usage,
            artifacts=["verdict.json", "summary.md"],
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
