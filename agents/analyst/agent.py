"""Анализ требований — адаптер к части участника 2 (packages/specforge-analysis, этап 1).

ТЗ (artifacts/input/requirements.md) → 01_requirements/: requirements.json, profile.json, критерии
приёмки (Gherkin), истории, трассируемость, вопросы. Шлюз — внутренний шлюз части; её вопросы
пользователю → questions.json (протокол уточнений платформы).
"""

import time
from pathlib import Path

import specforge_analysis
from specforge_analysis import stage_requirements

from agents._team.bridge import (
    METRICS_FILE,
    AnalysisLLM,
    analysis_diagnostics,
    crashed,
    team_result,
    write_questions,
)
from specforge.core.agent_api import AgentContext, BaseAgent
from specforge.core.artifacts import INPUT_STAGE
from specforge.core.contracts import AgentSpec, ProfileName, StageResult


class Analyst(BaseAgent):
    spec = AgentSpec(
        name="analyst",
        stage="01_requirements",
        kind="structured",
        description="Анализ требований: FR/NFR, роли, сценарии, критерии приёмки, вопросы",
        inputs=["input/*"],
        outputs=["**/*"],
        profile=ProfileName.ANALYSIS,
        max_repairs=0,  # ремонт критериев приёмки — внутри части
        token_budget=300_000,
    )
    cache_sources = (Path(specforge_analysis.__file__).parent,)

    def run(self, ctx: AgentContext) -> StageResult:
        started = time.monotonic()
        llm = AnalysisLLM(ctx.llm, metrics_file=ctx.output_dir / METRICS_FILE)
        with ctx.llm.track() as meter:
            try:
                result = stage_requirements.run(
                    ctx.input_dir / INPUT_STAGE, ctx.output_dir, llm, mode="auto"
                )
            except Exception as exc:  # часть не должна ронять конвейер
                return crashed(self, ctx, started, meter.usage, exc)
        asked = write_questions(ctx.output_dir / "requirements.json", ctx.output_dir)
        return team_result(
            self,
            ctx,
            started=started,
            usage=meter.usage,
            passed=result.passed,
            diagnostics=analysis_diagnostics(result, "requirements"),
            attempts=result.attempts,
            summary=f"вопросов {asked}",
        )


AGENT = Analyst()
