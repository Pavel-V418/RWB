"""Тесты и безопасность — адаптер к части участника 5 (packages/specforge-mobile, этап 07_tests).

Дерево артефактов → 07_tests/: security-контур (Semgrep, gitleaks, Trivy — те, что есть на
хосте; находки → SEC-требования анализа → триаж правилами и моделью) → security.json,
findings.json, report.md. Если есть Docker и стенд 08_infra — docker compose up, контрактные
тесты из OpenAPI (401/403 по x-roles, чужие id, Idempotency-Key, 429, отсутствие 5xx) и e2e по
сценарию (E2E_SCENARIO), затем стенд гасится. Для CI продукта — ci/*.gitlab-ci.yml,
e2e/route_e2e.py и копия кода инструментов (в опубликованном репозитории reports/tests/, их
подключает .gitlab-ci.yml участника 3). Настройки — packages/specforge-mobile/README.md.
"""

import time
from pathlib import Path

import specforge_mobile
from specforge_mobile import qa

from agents._team.bridge import crashed, team_result
from agents._team.mobile import MobileLLM, mobile_diagnostics, notes_diagnostics
from specforge.core.agent_api import AgentContext, BaseAgent
from specforge.core.contracts import AgentSpec, ProfileName, StageResult

PACKAGE = Path(specforge_mobile.__file__).resolve().parents[1]


def _quiet(*_args: object, **_kwargs: object) -> None:
    """Прогресс части — в structlog платформы не нужен: итог в StageResult."""


class Qa(BaseAgent):
    spec = AgentSpec(
        name="qa",
        stage="07_tests",
        kind="structured",
        description="Тесты и безопасность: контракт API, e2e, сканеры, отчёт по SEC-требованиям",
        inputs=[
            "01_requirements/requirements.json",
            "02_system/openapi.yaml",
            "08_infra/**/*",
        ],
        outputs=["**/*"],
        profile=ProfileName.CODE,
        depends_on=["backend_coder", "devops"],
        max_repairs=0,  # проверки не чинят код: находки — в отчёт и StageResult.gate
        token_budget=60_000,
    )
    cache_sources = (
        PACKAGE / "specforge_mobile" / "qa.py",
        PACKAGE / "specforge_mobile" / "tools",
        PACKAGE / "ci",
    )

    def run(self, ctx: AgentContext) -> StageResult:
        started = time.monotonic()
        llm = MobileLLM(ctx.llm)
        with ctx.llm.track() as meter:
            try:
                result = qa.run(ctx.input_dir, ctx.output_dir, llm, log=_quiet)
            except Exception as exc:  # часть не должна ронять конвейер
                return crashed(self, ctx, started, meter.usage, exc)
        diagnostics = mobile_diagnostics(result.get("errors", []), "qa") + notes_diagnostics(
            result.get("notes", []), "qa"
        )
        return team_result(
            self,
            ctx,
            started=started,
            usage=meter.usage,
            passed=bool(result.get("passed")),
            diagnostics=diagnostics,
            summary=f"проверки: {', '.join(result.get('ran', [])) or 'нет'}",
        )


AGENT = Qa()
