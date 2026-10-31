"""Android — адаптер к части участника 5 (packages/specforge-mobile).

03_ux/screen-spec.android.json + 02_system/openapi.yaml + требования → 06_android/: каркас
Kotlin 2.2 / Compose / Material 3 с замороженным ядром (геолокация, трекинг, уведомления,
таймер), Kotlin-клиент API (генератор участника 3, без LLM), экраны по UX-спеке (план → слой
данных → экраны → навигация), скриншот-тесты из спеки (без LLM) и шлюз Gradle (сборка,
unit-тесты, снимки Roborazzi) с ремонтом внутри части. Результат: 06_android/project
(Android-проект), 06_android/screenshots/*.png, gate_report.json.

Мобильное приложение строится, только если 01_requirements/profile.json → needs_mobile_app
(проверка при запуске, а не enabled_if: выключенный этап не должен блокировать документацию).
Сборка — в образе specforge/android:jdk21-api36, локальным Gradle или никак (см.
specforge_mobile.android_agent.toolchain).
"""

import json
import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import specforge_mobile
from specforge_mobile.android_agent import agent as android
from specforge_mobile.android_agent import toolchain

from agents._team.backend import platform_env
from agents._team.bridge import crashed, diagnostic, team_result
from agents._team.mobile import MobileLLM, mobile_diagnostics, notes_diagnostics
from specforge.core.agent_api import AgentContext, BaseAgent
from specforge.core.contracts import (
    AgentSpec,
    Diagnostic,
    ProfileName,
    StageResult,
    StageStatus,
    Usage,
)

PACKAGE = Path(specforge_mobile.__file__).resolve().parents[1]
CLIENT_GENERATOR = PACKAGE.parent / "specforge-backend" / "generators" / "gen-clients.sh"
SPEC = "03_ux/screen-spec.android.json"
PROFILE = "01_requirements/profile.json"
MAX_REPAIRS = 3


def _quiet(*_args: object, **_kwargs: object) -> None:
    """Прогресс части — в structlog платформы не нужен: итог в StageResult."""


def needs_mobile(input_dir: Path) -> bool:
    """needs_mobile_app из профиля анализа; профиля нет — строим (решает наличие UX-спеки)."""
    path = input_dir / PROFILE
    if not path.is_file():
        return True
    try:
        return bool(json.loads(path.read_text(encoding="utf-8")).get("needs_mobile_app", False))
    except (ValueError, AttributeError):
        return True


def kotlin_client(input_dir: Path, workdir: Path) -> tuple[Path | None, str | None]:
    """Kotlin-клиент из OpenAPI генератором участника 3; (папка generated, предупреждение)."""
    openapi = input_dir / "02_system" / "openapi.yaml"
    if not openapi.is_file() or not CLIENT_GENERATOR.is_file():
        return (
            None,
            "нет openapi.yaml или генератора клиентов участника 3 — используется клиент каркаса",
        )
    env = dict(os.environ, CLIENTS="kotlin")
    env.setdefault("ANDROID_APP_ID", "com.specforge.case1")
    try:
        proc = subprocess.run(
            [str(CLIENT_GENERATOR), str(openapi), str(workdir)],
            capture_output=True,
            text=True,
            env=env,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"генератор Kotlin-клиента не запустился ({exc}) — используется клиент каркаса"
    found = sorted((workdir / "06_android").glob("**/core/network/generated"))
    if proc.returncode != 0 or not found:
        tail = (proc.stdout + proc.stderr).strip()[-400:]
        return None, f"Kotlin-клиент не сгенерирован ({tail}) — используется клиент каркаса"
    return found[0], None


class AndroidCoder(BaseAgent):
    spec = AgentSpec(
        name="android_coder",
        stage="06_android",
        kind="structured",
        description="Android-приложение (Kotlin, Compose): экраны по UX-спеке, тесты, скриншоты",
        inputs=[
            SPEC,
            "02_system/openapi.yaml",
            PROFILE,
            "01_requirements/requirements.json",
        ],
        outputs=["**/*"],
        profile=ProfileName.CODE,
        depends_on=["system_architect", "ux_designer"],
        max_repairs=0,  # ремонт по ошибкам kotlinc / JUnit / снимков — внутри части
        token_budget=300_000,
    )
    cache_sources = (
        PACKAGE / "specforge_mobile" / "android_agent",
        PACKAGE / "specforge_mobile" / "tools" / "android_gate",
        PACKAGE / "android",
    )

    def run(self, ctx: AgentContext) -> StageResult:
        started = time.monotonic()
        if not needs_mobile(ctx.input_dir):
            (ctx.output_dir / "README.md").write_text(
                "Мобильное приложение кейсом не требуется (needs_mobile_app = false).\n",
                encoding="utf-8",
            )
            return self.result(
                StageStatus.passed,
                started=started,
                artifacts=self.collect_artifacts(ctx),
                summary="мобильное приложение не требуется",
            )
        if not (ctx.input_dir / SPEC).is_file():
            errors = [diagnostic("android", f"нет {SPEC}: экраны строить не по чему")]
            return team_result(
                self, ctx, started=started, usage=Usage(), passed=False, diagnostics=errors
            )

        platform_env()
        mode = toolchain.choose()
        gate: Callable[[Path], dict[str, Any]] | None
        if mode == "docker":
            gate = toolchain.docker_gate
        elif mode == "local":
            gate = None  # android.default_gate: локальный Gradle
        else:
            gate = toolchain.no_toolchain_gate
        warnings: list[Diagnostic] = []
        llm = MobileLLM(ctx.llm)
        with (
            tempfile.TemporaryDirectory(prefix="specforge-kotlin-") as tmp,
            ctx.llm.track() as meter,
        ):
            client, warning = kotlin_client(ctx.input_dir, Path(tmp))
            if warning:
                warnings.append(diagnostic("android", warning, severity="warning"))
            try:
                result = android.run(
                    ctx.input_dir,
                    ctx.output_dir,
                    llm,
                    api_client=client,
                    gate=gate,
                    max_repairs=MAX_REPAIRS if mode else 0,  # собрать нечем — не чиним вслепую
                    offline=mode == "local" and os.environ.get("SPECFORGE_ANDROID_OFFLINE") == "1",
                    log=_quiet,
                )
            except Exception as exc:  # часть не должна ронять конвейер
                return crashed(self, ctx, started, meter.usage, exc)
        diagnostics = (
            mobile_diagnostics(result.get("errors", []), "android")
            + notes_diagnostics(result.get("notes", []), "android")
            + warnings
        )
        shots = len(list((ctx.output_dir / "screenshots").glob("*.png")))
        return team_result(
            self,
            ctx,
            started=started,
            usage=meter.usage,
            passed=bool(result.get("passed")),
            diagnostics=diagnostics,
            attempts=int(result.get("attempts", 1)),
            summary=f"сборка: {mode or 'нечем'}; снимков {shots}",
        )


AGENT = AndroidCoder()
