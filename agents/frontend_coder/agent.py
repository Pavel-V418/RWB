"""Веб-админка — адаптер к части участника 4 (packages/specforge-web, генератор + frontend-агент).

03_ux/screen-spec.web.json + 02_system/openapi.yaml → 05_web/admin/: Angular 20.1.6 + PrimeNG
(клиент API, страницы, маршруты, unit- и e2e-тесты, Dockerfile на nginx). Модель пишет только
нестандартные блоки (слоты), быстрые шлюзы части (kit-lint, шаблоны, типы) — внутри агента.
Сборку и тесты (`npm run gate`: kit-lint + ng build + jest) запускает шлюз платформы в песочнице
specforge/node:22; красный шлюз чинит ремонтник платформы.
"""

import time
from pathlib import Path

import specforge_web
from specforge_web.agents.frontend import agent as fe

from agents._team.bridge import WebLLM, crashed, team_result, web_diagnostics
from specforge.core.agent_api import AgentContext, BaseAgent
from specforge.core.contracts import AgentSpec, ProfileName, StageResult

GATE = "cd admin && npm install --no-audit --no-fund && npm run gate"


def _quiet(*_args: object, **_kwargs: object) -> None:
    """Прогресс части — в structlog платформы не нужен: итог в StageResult."""


class FrontendCoder(BaseAgent):
    spec = AgentSpec(
        name="frontend_coder",
        stage="05_web",
        kind="tool",
        description="Административный web-интерфейс на Angular по спеке экранов",
        inputs=["03_ux/screen-spec.web.json", "03_ux/mock-events.json", "02_system/openapi.yaml"],
        outputs=["**/*"],
        tools=["read_file", "str_replace", "list_dir", "grep", "run"],  # для ремонтника
        profile=ProfileName.CODE,
        sandbox_image="specforge/node:22",
        gate_commands=[GATE],
        gate_parsers={GATE: "tsc"},
        protected_globs=["admin/src/app/core/**", "admin/package.json"],
        depends_on=["ux_designer"],
        max_repairs=2,
        token_budget=250_000,
    )
    cache_sources = (Path(specforge_web.__file__).parent,)

    def run(self, ctx: AgentContext) -> StageResult:
        started = time.monotonic()
        with ctx.llm.track() as meter:
            try:
                result = fe.run(
                    ctx.input_dir, ctx.output_dir, WebLLM(ctx.llm), build=False, log=_quiet
                )
            except Exception as exc:  # часть не должна ронять конвейер
                return crashed(self, ctx, started, meter.usage, exc)
        staged = team_result(
            self,
            ctx,
            started=started,
            usage=meter.usage,
            passed=result.passed,
            diagnostics=web_diagnostics(result, "frontend"),
            attempts=result.attempts,
        )
        # сборку проверит шлюз платформы (gate_commands): результат части — без собственного gate
        return staged.model_copy(update={"gate": None})


AGENT = FrontendCoder()
