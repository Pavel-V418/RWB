"""Документация — адаптер к части участника 2 (packages/specforge-analysis, этап docs).

artifacts/* → 09_docs/: сайт MkDocs (mkdocs.yml + docs/) — продуктовое описание, архитектура, API,
модель данных, SA-документ, инструкция по запуску, руководства пользователя и разработчика, ADR,
паспорта сервисов, отчёты о тестировании и безопасности, ограничения, трассируемость.
Результаты других этапов читаются по договорённым путям (03_ux, 05_web, 07_tests, 08_infra);
чего нет — раздел помечается «ожидается».
"""

import time
from pathlib import Path

import specforge_analysis
from specforge_analysis import stage_docs

from agents._team.bridge import (
    METRICS_FILE,
    AnalysisLLM,
    analysis_diagnostics,
    crashed,
    team_result,
)
from specforge.core.agent_api import AgentContext, BaseAgent
from specforge.core.contracts import AgentSpec, ProfileName, StageResult


class TechWriter(BaseAgent):
    spec = AgentSpec(
        name="tech_writer",
        stage="09_docs",
        kind="structured",
        description="Документация проекта (MkDocs) по шаблонам организаторов",
        inputs=[
            "01_requirements/**/*",
            "02_system/**/*",
            "03_ux/**/*",
            "05_web/screenshots/*",
            "06_android/screenshots/*",
            "04_backend/traceability.md",
            "07_tests/**/*",
            "08_infra/**/*",
        ],
        outputs=["**/*"],
        profile=ProfileName.ANALYSIS,
        depends_on=[
            "system_architect",
            "ux_designer",
            "frontend_coder",
            "backend_coder",
            "devops",
            "android_coder",
            "qa",
        ],
        max_repairs=0,
        token_budget=150_000,
    )
    cache_sources = (Path(specforge_analysis.__file__).parent,)

    def run(self, ctx: AgentContext) -> StageResult:
        started = time.monotonic()
        llm = AnalysisLLM(ctx.llm, metrics_file=ctx.output_dir / METRICS_FILE)
        with ctx.llm.track() as meter:
            try:
                result = stage_docs.run(ctx.input_dir, ctx.output_dir, llm)
            except Exception as exc:  # часть не должна ронять конвейер
                return crashed(self, ctx, started, meter.usage, exc)
        return team_result(
            self,
            ctx,
            started=started,
            usage=meter.usage,
            passed=result.passed,
            diagnostics=analysis_diagnostics(result, "docs"),
            attempts=result.attempts,
        )


AGENT = TechWriter()
