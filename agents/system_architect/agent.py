"""Системный анализ — адаптер к части участника 2 (packages/specforge-analysis, этап 2).

01_requirements/ → 02_system/: OpenAPI 3.1, AsyncAPI 3.0, DDL (PostgreSQL 17 + PostGIS), доменная
модель, диаграммы, документ системного анализа, трассируемость. Шлюз части проверяет покрытие FR,
ссылки контрактов и применение DDL (SPECFORGE_PG_DSN — в транзакции с ROLLBACK).
"""

import time
from pathlib import Path

import specforge_analysis
from specforge_analysis import stage_system

from agents._team.bridge import (
    METRICS_FILE,
    AnalysisLLM,
    analysis_diagnostics,
    crashed,
    team_result,
)
from specforge.core.agent_api import AgentContext, BaseAgent
from specforge.core.contracts import AgentSpec, ProfileName, StageResult

REQUIREMENTS = "01_requirements"


class SystemArchitect(BaseAgent):
    spec = AgentSpec(
        name="system_architect",
        stage="02_system",
        kind="structured",
        description="Системный анализ: OpenAPI, AsyncAPI, DDL, доменная модель, SA-документ",
        inputs=[f"{REQUIREMENTS}/requirements.json"],
        outputs=["**/*"],
        profile=ProfileName.ANALYSIS,
        depends_on=["analyst"],
        max_repairs=0,  # ремонт покрытия FR — внутри части
        token_budget=300_000,
    )
    cache_sources = (Path(specforge_analysis.__file__).parent,)

    def run(self, ctx: AgentContext) -> StageResult:
        started = time.monotonic()
        llm = AnalysisLLM(ctx.llm, metrics_file=ctx.output_dir / METRICS_FILE)
        with ctx.llm.track() as meter:
            try:
                result = stage_system.run(ctx.input_dir / REQUIREMENTS, ctx.output_dir, llm)
            except Exception as exc:  # часть не должна ронять конвейер
                return crashed(self, ctx, started, meter.usage, exc)
        return team_result(
            self,
            ctx,
            started=started,
            usage=meter.usage,
            passed=result.passed,
            diagnostics=analysis_diagnostics(result, "system"),
            attempts=result.attempts,
        )


AGENT = SystemArchitect()
