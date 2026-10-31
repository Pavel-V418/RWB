"""Инфраструктура — адаптер к DevOps-агенту участника 3 (packages/specforge-backend).

04_backend/ → 08_infra/: docker-compose стенда (PostGIS, Kafka, backend, web по профилю), db-init из
схемы, deploy.sh, .gitlab-ci.yml продукта (build, test, package, security, deploy, e2e), паспорт
сервиса и ADR по шаблонам организаторов. Шлюз части: docker compose up --wait + /health/ready,
/swagger, /metrics; при провале модель разбирает логи: правка compose/Dockerfile (infra) или
текст обратной связи для backend (app, 08_infra/devops-feedback.txt). Нужен Docker на хосте.
"""

import time
from pathlib import Path

import specforge_backend
from specforge_backend.devops import agent as devops

from agents._team.backend import BackendLLM, gate_diagnostics, platform_env
from agents._team.bridge import crashed, team_result
from specforge.core.agent_api import AgentContext, BaseAgent
from specforge.core.contracts import AgentSpec, ProfileName, StageResult

PACKAGE = Path(specforge_backend.__file__).resolve().parents[1]


def _quiet(*_args: object, **_kwargs: object) -> None:
    """Прогресс части — в structlog платформы не нужен: итог в StageResult."""


class DevOps(BaseAgent):
    spec = AgentSpec(
        name="devops",
        stage="08_infra",
        kind="structured",
        description="Стенд docker-compose, CI продукта, паспорт сервиса и ADR",
        inputs=["04_backend/**/*", "02_system/openapi.yaml"],
        outputs=["**/*"],
        profile=ProfileName.CODE,
        depends_on=["backend_coder"],
        max_repairs=0,  # ремонт стенда по логам — внутри части
        token_budget=60_000,
    )
    cache_sources = (
        PACKAGE / "specforge_backend" / "devops",
        PACKAGE / "generators",
        PACKAGE / "templates",
    )

    def run(self, ctx: AgentContext) -> StageResult:
        started = time.monotonic()
        platform_env()
        llm = BackendLLM(ctx.llm)
        with ctx.llm.track() as meter:
            try:
                # стенд гасится после шлюза: хост конвейера — не место для работающего продукта
                result = devops.run(ctx.input_dir, ctx.output_dir, llm, down=True, log=_quiet)
            except Exception as exc:  # часть не должна ронять конвейер
                return crashed(self, ctx, started, meter.usage, exc)
        return team_result(
            self,
            ctx,
            started=started,
            usage=meter.usage,
            passed=result.passed,
            diagnostics=gate_diagnostics(result.errors, "stand"),
            attempts=result.attempts,
        )


AGENT = DevOps()
