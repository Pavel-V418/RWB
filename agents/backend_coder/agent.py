"""Backend — адаптер к части участника 3 (packages/specforge-backend).

02_system/openapi.yaml + schema.sql → 04_backend/: золотой каркас ASP.NET 10 (Clean Architecture,
EF Core + NetTopologySuite, Kafka через outbox, SignalR, health, /metrics), детерминированные
генераторы (DTO и контроллеры из OpenAPI, EF-модели из DDL), затем backend-агент реализует
I<Tag>Api по тегам и пишет интеграционные тесты по критериям приёмки. Шлюзы части внутренние:
dotnet build -warnaserror и dotnet test (Testcontainers), свои ремонты; тег, не собравшийся за
попытки, — заглушка 501. Нужны dotnet SDK 10 и Docker на хосте конвейера.
"""

import subprocess
import time
from pathlib import Path

import specforge_backend
from specforge_backend.backend import agent as backend

from agents._team.backend import BackendLLM, gate_diagnostics, platform_env
from agents._team.bridge import crashed, diagnostic, team_result
from specforge.core.agent_api import AgentContext, BaseAgent
from specforge.core.contracts import AgentSpec, ProfileName, StageResult, Usage

PACKAGE = Path(specforge_backend.__file__).resolve().parents[1]
SYSTEM = "02_system"


def _quiet(*_args: object, **_kwargs: object) -> None:
    """Прогресс части — в structlog платформы не нужен: итог в StageResult."""


class BackendCoder(BaseAgent):
    spec = AgentSpec(
        name="backend_coder",
        stage="04_backend",
        kind="structured",
        description="Backend на ASP.NET 10: каркас, код из контрактов, доменная логика и тесты",
        inputs=[
            f"{SYSTEM}/openapi.yaml",
            f"{SYSTEM}/schema.sql",
            "01_requirements/requirements.json",
        ],
        outputs=["**/*"],
        profile=ProfileName.CODE,
        depends_on=["system_architect"],
        max_repairs=0,  # ремонт по ошибкам компилятора и тестов — внутри части
        token_budget=400_000,
    )
    cache_sources = (PACKAGE / "specforge_backend", PACKAGE / "generators", PACKAGE / "templates")

    def run(self, ctx: AgentContext) -> StageResult:
        started = time.monotonic()
        platform_env()
        system = ctx.input_dir / SYSTEM
        # 1. Без LLM: каркас + контракт ASP.NET + EF-модели (output_dir очищен оркестратором)
        for script, source in (("gen-server.sh", "openapi.yaml"), ("gen-ef.sh", "schema.sql")):
            proc = subprocess.run(
                [str(PACKAGE / "generators" / script), str(system / source), str(ctx.output_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                errors = [diagnostic(script, (proc.stdout + proc.stderr).strip()[-1500:])]
                return team_result(
                    self, ctx, started=started, usage=Usage(), passed=False, diagnostics=errors
                )
        # 2. Backend-агент: реализация тегов + тесты (модель — через LLMClient платформы)
        llm = BackendLLM(ctx.llm)
        with ctx.llm.track() as meter:
            try:
                result = backend.run(ctx.input_dir, ctx.output_dir, llm, max_attempts=4, log=_quiet)
            except Exception as exc:  # часть не должна ронять конвейер
                return crashed(self, ctx, started, meter.usage, exc)
        stubs = [tag for tag, status in result.tags.items() if status == "stub"]
        return team_result(
            self,
            ctx,
            started=started,
            usage=meter.usage,
            passed=result.passed,
            diagnostics=gate_diagnostics(result.errors, "backend"),
            attempts=result.attempts,
            summary=f"заглушки 501: {', '.join(stubs)}" if stubs else "",
        )


AGENT = BackendCoder()
