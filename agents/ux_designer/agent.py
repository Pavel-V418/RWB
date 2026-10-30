"""Проектирование интерфейса — адаптер к части участника 4 (packages/specforge-web, UX-агент).

01_requirements + 02_system/openapi.yaml → 03_ux/: спеки экранов screen-spec.<web|android>.json,
пользовательские потоки, привязка требований к экранам; для документации — screens.json,
traceability.json и docs/ (руководство и каталог экранов). Платформы — из профиля анализа.
Без ответа модели часть строит экраны эвристиками по OpenAPI (baseline) — шлюз остаётся зелёным.
"""

import time
from pathlib import Path

import specforge_web
from specforge_web.agents.ux_designer import agent as ux

from agents._team.bridge import WebLLM, crashed, team_result, web_diagnostics
from agents._team.web import platforms, ui_docs, write_team_inputs
from specforge.core.agent_api import AgentContext, BaseAgent
from specforge.core.contracts import AgentSpec, ProfileName, StageResult


def _quiet(*_args: object, **_kwargs: object) -> None:
    """Прогресс части — в structlog платформы не нужен: итог в StageResult."""


class UxDesigner(BaseAgent):
    spec = AgentSpec(
        name="ux_designer",
        stage="03_ux",
        kind="structured",
        description="Экраны, состояния и потоки по требованиям и OpenAPI (UI Kit PrimeNG)",
        inputs=[
            "01_requirements/requirements.json",
            "01_requirements/profile.json",
            "02_system/openapi.yaml",
        ],
        outputs=["**/*"],
        profile=ProfileName.CODE,
        depends_on=["system_architect"],
        max_repairs=0,  # исправление экранов по шлюзу UX — внутри части
        token_budget=250_000,
    )
    cache_sources = (Path(specforge_web.__file__).parent,)

    def run(self, ctx: AgentContext) -> StageResult:
        started = time.monotonic()
        chosen = platforms(ctx.input_dir)
        with ctx.llm.track() as meter:
            try:
                result = ux.run(
                    ctx.input_dir, ctx.output_dir, WebLLM(ctx.llm), platforms=chosen, log=_quiet
                )
                screens = write_team_inputs(ctx.output_dir)
                ui_docs(ctx.input_dir, ctx.output_dir)
            except Exception as exc:  # часть не должна ронять конвейер
                return crashed(self, ctx, started, meter.usage, exc)
        return team_result(
            self,
            ctx,
            started=started,
            usage=meter.usage,
            passed=result.passed,
            diagnostics=web_diagnostics(result, "ux"),
            attempts=result.attempts,
            summary=f"платформы {', '.join(chosen)}, экранов {screens}",
        )


AGENT = UxDesigner()
