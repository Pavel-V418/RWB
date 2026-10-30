"""`specforge doctor` — готов ли стенд к прогону.

- инфраструктура: vLLM (модель, tool calling, json_schema, переключение thinking), Postgres,
  Langfuse, GitLab, Docker; не настроенное через env (Langfuse, GitLab) — SKIP, а не FAIL;
- агенты реестра: загрузка и spec, prompt_dir с промптами, sandbox_image есть и запускается,
  программы из gate_commands есть в образе (`command -v`) или на хосте (без образа);
- граф: проверка реестра (циклы, depends_on, outputs) и недостижимые этапы для профилей
  «всё включено», «без mobile», «без geo»: этап включён своим enabled_if, но отрезан
  выключенной зависимостью.

Итог — таблица OK / FAIL / SKIP с подсказкой, как починить; код выхода 1, если есть FAIL.
"""

import os
import re
import shlex
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

import structlog
import typer
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from specforge.core.agent_api import Agent
from specforge.core.contracts import AgentSpec, CaseProfile
from specforge.llm import ChatMessage, LLMClient, LLMError, ToolSpec
from specforge.orchestrator.cache import agent_dir
from specforge.orchestrator.registry import (
    PROFILE_STAGE,
    Registry,
    RegistryError,
    available_agents,
    load_agent,
)
from specforge.rag.embeddings import Embedder, describe, embedder_from_env

log = structlog.get_logger(__name__)

Status = Literal["OK", "FAIL", "SKIP"]
MAX_TOKENS = 1024
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SHELL_BUILTINS = frozenset({"cd", "export", "set", "test", "[", "true", "false", "echo", "exit"})


@dataclass
class Check:
    group: str
    name: str
    status: Status
    detail: str = ""
    hint: str = ""


@dataclass
class DoctorDeps:
    """Внешние зависимости проверок — подменяются в тестах."""

    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    llm_factory: Callable[[], LLMClient] = LLMClient.from_env
    list_models: Callable[[LLMClient], list[str]] | None = None
    registry_factory: Callable[[], Registry] | None = None
    docker_factory: Callable[[], Any] | None = None
    pg_connect: Callable[[str], Any] | None = None
    langfuse_factory: Callable[[Mapping[str, str]], Any] | None = None
    gitlab_factory: Callable[[str, str], Any] | None = None
    which: Callable[[str], str | None] = shutil.which
    embedder_factory: Callable[[], Embedder | None] = embedder_from_env


def run_doctor(deps: DoctorDeps | None = None) -> list[Check]:
    deps = deps or DoctorDeps()
    checks: list[Check] = []
    checks += check_llm(deps)
    checks.append(check_embeddings(deps))
    checks.append(check_postgres(deps))
    checks.append(check_langfuse(deps))
    checks.append(check_gitlab(deps))
    docker_client, docker_check = check_docker(deps)
    checks.append(docker_check)
    agents, registry, load_checks = load_agents(deps)
    checks += load_checks
    for agent in agents:
        checks += check_agent(agent, docker_client, deps)
    if registry is not None:
        checks += check_graph(registry)
    return checks


# --- инфраструктура -----------------------------------------------------------------------------


class _Answer(BaseModel):
    result: int


ADD = ToolSpec(
    name="add",
    description="Сложить два целых числа.",
    parameters={
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    },
)


def _user(text: str) -> list[ChatMessage]:
    return [ChatMessage(role="user", content=text)]


def check_llm(deps: DoctorDeps) -> list[Check]:
    hint_env = "проверьте LLM_BASE_URL / LLM_MODEL / LLM_API_KEY в .env и что vLLM запущен"
    try:
        llm = deps.llm_factory()
    except LLMError as exc:
        return [Check("vLLM", "конфигурация", "FAIL", str(exc)[:200], hint_env)]
    base = llm.config.base_url
    checks: list[Check] = []
    try:
        models = (deps.list_models or _list_models)(llm)
    except Exception as exc:
        return [Check("vLLM", "доступность", "FAIL", f"{base}: {exc}"[:200], hint_env)]
    if llm.config.model in models:
        checks.append(Check("vLLM", "модель", "OK", f"{llm.config.model} на {base}"))
    else:
        checks.append(
            Check(
                "vLLM",
                "модель",
                "FAIL",
                f"{llm.config.model!r} нет; есть: {', '.join(models) or '—'}",
                "LLM_MODEL должен совпадать с --served-model-name у vLLM",
            )
        )
        return checks

    def probe(name: str, hint: str, fn: Callable[[], tuple[bool, str]]) -> None:
        try:
            ok, detail = fn()
        except LLMError as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        checks.append(Check("vLLM", name, "OK" if ok else "FAIL", detail[:200], "" if ok else hint))

    def tool_calling() -> tuple[bool, str]:
        r = llm.chat(_user("Сколько будет 17 + 25? Вызови add."), [ADD], max_tokens=MAX_TOKENS)
        call = r.tool_calls[0] if r.tool_calls else None
        ok = call is not None and call.name == "add"
        return ok, f"вызов {call.name}({call.arguments})" if call else f"текст: {r.content[:60]!r}"

    def json_schema() -> tuple[bool, str]:
        value = llm.structured(_user("Вычисли 6 * 7."), _Answer, max_tokens=MAX_TOKENS)
        return value.result == 42, f"result={value.result}"

    def thinking_toggle() -> tuple[bool, str]:
        on = llm.chat(
            _user("Сколько будет 12 * 13?"), thinking=True, temperature=0.6, max_tokens=MAX_TOKENS
        )
        off = llm.chat(_user("Сколько будет 12 * 13?"), thinking=False, max_tokens=MAX_TOKENS)
        has_on, has_off = bool((on.reasoning or "").strip()), bool((off.reasoning or "").strip())
        return has_on and not has_off, f"reasoning: on={has_on}, off={has_off}"

    probe(
        "tool calling",
        "vLLM: --enable-auto-tool-choice и TOOL_CALL_PARSER из model card",
        tool_calling,
    )
    probe(
        "json_schema", "LLM_STRUCTURED_MODE=prompt, если эндпоинт без response_format", json_schema
    )
    probe(
        "thinking on/off",
        "REASONING_PARSER у vLLM и LLM_VLLM_EXTRAS=true (chat_template_kwargs.enable_thinking)",
        thinking_toggle,
    )
    return checks


def _list_models(llm: LLMClient) -> list[str]:
    return [m.id for m in llm._client().models.list()]


def check_embeddings(deps: DoctorDeps) -> Check:
    try:
        embedder = deps.embedder_factory()
    except Exception as exc:
        return Check("Эмбеддинги", "режим", "FAIL", str(exc)[:200], "проверьте EMBED_* в .env")
    mode = describe(embedder)
    if embedder is None:
        return Check(
            "Эмбеддинги",
            "режим",
            "SKIP",
            f"{mode}: поиск по документации только FTS5",
            "infra/fetch_docs.sh скачает веса локальной модели, или задайте EMBED_BASE_URL",
        )
    try:
        dim = int(embedder.embed_query("проверка").shape[-1])
    except Exception as exc:
        return Check(
            "Эмбеддинги",
            "режим",
            "FAIL",
            f"{mode}: {exc}"[:200],
            "проверьте EMBED_BASE_URL / EMBED_MODEL / EMBED_API_KEY",
        )
    return Check("Эмбеддинги", "режим", "OK", f"{mode}, размерность {dim}")


def check_postgres(deps: DoctorDeps) -> Check:
    dsn = deps.env.get("SPECFORGE_PG_DSN", "")
    if not dsn:
        return Check(
            "Postgres",
            "подключение",
            "SKIP",
            "SPECFORGE_PG_DSN не задан: чекпойнты в памяти, события в SQLite",
            "для resume между процессами задайте SPECFORGE_PG_DSN",
        )
    try:
        connect = deps.pg_connect or _pg_connect
        with connect(dsn) as conn:
            version = conn.execute("SHOW server_version").fetchone()[0]
            extensions = {r[0] for r in conn.execute("SELECT extname FROM pg_extension")}
    except Exception as exc:
        return Check(
            "Postgres",
            "подключение",
            "FAIL",
            str(exc)[:200],
            "docker compose -f infra/docker-compose.platform.yml up -d postgres",
        )
    missing = {"pg_trgm", "vector"} - extensions
    if missing:
        return Check(
            "Postgres",
            "подключение",
            "FAIL",
            f"PostgreSQL {version}, нет расширений: {', '.join(sorted(missing))}",
            "выполните infra/postgres/init.sql (CREATE EXTENSION ...)",
        )
    return Check("Postgres", "подключение", "OK", f"PostgreSQL {version}, pg_trgm и vector есть")


def _pg_connect(dsn: str) -> Any:
    import psycopg  # noqa: PLC0415

    return psycopg.connect(dsn, connect_timeout=5)


def check_langfuse(deps: DoctorDeps) -> Check:
    if not (deps.env.get("LANGFUSE_PUBLIC_KEY") and deps.env.get("LANGFUSE_SECRET_KEY")):
        return Check("Langfuse", "авторизация", "SKIP", "LANGFUSE_* не заданы — трассировки нет")
    try:
        client = (deps.langfuse_factory or _langfuse)(deps.env)
        ok = bool(client.auth_check())
    except Exception as exc:
        ok, error = False, str(exc)[:200]
    else:
        error = "ключи не приняты"
    host = deps.env.get("LANGFUSE_HOST", "")
    if ok:
        return Check("Langfuse", "авторизация", "OK", host)
    return Check(
        "Langfuse",
        "авторизация",
        "FAIL",
        f"{host}: {error}",
        "проверьте LANGFUSE_HOST и ключи проекта (Settings → API keys)",
    )


def _langfuse(env: Mapping[str, str]) -> Any:
    from langfuse import Langfuse  # noqa: PLC0415

    return Langfuse(
        public_key=env["LANGFUSE_PUBLIC_KEY"],
        secret_key=env["LANGFUSE_SECRET_KEY"],
        host=env.get("LANGFUSE_HOST") or None,
    )


def check_gitlab(deps: DoctorDeps) -> Check:
    url, token = deps.env.get("GITLAB_URL", ""), deps.env.get("GITLAB_TOKEN", "")
    if not url or not token:
        return Check(
            "GitLab", "доступ", "SKIP", "GITLAB_URL / GITLAB_TOKEN не заданы — без публикации"
        )
    project = deps.env.get("GITLAB_PROJECT", "")
    try:
        gl = (deps.gitlab_factory or _gitlab)(url, token)
        gl.auth()
        if project:
            gl.projects.get(project)
    except Exception as exc:
        return Check(
            "GitLab",
            "доступ",
            "FAIL",
            f"{url}: {exc}"[:200],
            "токен с правами api; GITLAB_PROJECT — id или group/project",
        )
    target = project or "новый проект на каждый прогон"
    return Check("GitLab", "доступ", "OK", f"{url}, {target}")


def _gitlab(url: str, token: str) -> Any:
    import gitlab  # noqa: PLC0415

    return gitlab.Gitlab(url, private_token=token, timeout=10)


def check_docker(deps: DoctorDeps) -> tuple[Any | None, Check]:
    try:
        client = (deps.docker_factory or _docker)()
        client.ping()
        version = client.version().get("Version", "?")
    except Exception as exc:
        return None, Check(
            "Docker",
            "демон",
            "FAIL",
            str(exc)[:200],
            "запустите Docker: без него агенты с sandbox_image не работают "
            "(SPECFORGE_SANDBOX=local — только для разработки)",
        )
    return client, Check("Docker", "демон", "OK", f"Docker {version}")


def _docker() -> Any:
    import docker  # noqa: PLC0415

    return docker.from_env(timeout=10)


# --- агенты -------------------------------------------------------------------------------------


def load_agents(deps: DoctorDeps) -> tuple[list[Agent], Registry | None, list[Check]]:
    if deps.registry_factory is not None:
        try:
            registry = deps.registry_factory()
        except RegistryError as exc:
            return [], None, [_registry_fail(exc)]
        return list(registry.agents.values()), registry, [_registry_ok(registry)]
    agents: list[Agent] = []
    checks: list[Check] = []
    for name in available_agents():
        if name.startswith("_"):
            continue
        try:
            agents.append(load_agent(name))
        except Exception as exc:  # чужой агент не должен ронять doctor
            checks.append(
                Check(
                    f"агент {name}",
                    "spec",
                    "FAIL",
                    f"{type(exc).__name__}: {exc}"[:200],
                    f"python -c 'import agents.{name}.agent'; поля AgentSpec — "
                    "docs/AGENT_SDK.md §4",
                )
            )
    try:
        registry = Registry(agents)
    except RegistryError as exc:
        return agents, None, [*checks, _registry_fail(exc)]
    return agents, registry, [*checks, _registry_ok(registry)]


def _registry_ok(registry: Registry) -> Check:
    if not registry.agents:
        return Check(
            "граф", "реестр", "FAIL", "агентов нет", "добавьте agents/<name>/agent.py с AGENT"
        )
    return Check(
        "граф", "реестр", "OK", f"агентов {len(registry.agents)}, шагов {len(registry.steps)}"
    )


def _registry_fail(exc: RegistryError) -> Check:
    return Check(
        "граф",
        "реестр",
        "FAIL",
        str(exc).replace("\n", " ")[:300],
        "исправьте depends_on / outputs / parallel_group; specforge list-agents покажет план",
    )


def check_agent(agent: Agent, docker_client: Any | None, deps: DoctorDeps) -> list[Check]:
    spec = agent.spec
    group = f"агент {spec.name}"
    checks = [Check(group, "spec", "OK", f"{spec.kind}, этап {spec.stage}")]
    prompts = agent_dir(agent) / spec.prompt_dir
    required = ["system.md", "task.md"] if spec.kind == "tool" else ["system.md"]
    missing = [f for f in required if not (prompts / f).is_file()]
    if spec.llm_free:
        checks.append(Check(group, "prompt_dir", "SKIP", "llm_free — промпты не нужны"))
    elif not prompts.is_dir() or missing:
        checks.append(
            Check(
                group,
                "prompt_dir",
                "FAIL",
                f"{prompts}: нет {', '.join(missing) if prompts.is_dir() else 'каталога'}",
                f"создайте {spec.prompt_dir}/{' и '.join(required)} в каталоге агента",
            )
        )
    else:
        checks.append(Check(group, "prompt_dir", "OK", str(Path(spec.prompt_dir))))
    checks += _sandbox_checks(spec, group, docker_client, deps)
    return checks


def gate_programs(commands: Sequence[str]) -> list[str]:
    """Программы, которые вызывают команды шлюза (первое слово каждой части `&&`, `;`, `|`)."""
    programs: list[str] = []
    for command in commands:
        for part in _split_shell(command):
            try:
                words = shlex.split(part)
            except ValueError:
                words = part.split()
            while words and ENV_ASSIGNMENT.match(words[0]):  # VAR=value cmd
                words = words[1:]
            if words and words[0] not in SHELL_BUILTINS and words[0] not in programs:
                programs.append(words[0])
    return programs


def _split_shell(command: str) -> list[str]:
    for sep in ("&&", "||", ";", "|"):
        command = command.replace(sep, "\n")
    return [p.strip() for p in command.splitlines() if p.strip()]


def _sandbox_checks(
    spec: AgentSpec, group: str, docker_client: Any | None, deps: DoctorDeps
) -> list[Check]:
    programs = gate_programs(spec.gate_commands)
    if spec.sandbox_image is None:
        missing = [p for p in programs if not p.startswith((".", "/")) and deps.which(p) is None]
        if not programs:
            return []
        if missing:
            return [
                Check(
                    group,
                    "gate_commands",
                    "FAIL",
                    f"на хосте нет: {', '.join(missing)}",
                    "установите программы или задайте sandbox_image с ними",
                )
            ]
        return [Check(group, "gate_commands", "OK", f"на хосте: {', '.join(programs)}")]
    if docker_client is None:
        return [Check(group, "sandbox_image", "SKIP", spec.sandbox_image, "нужен Docker")]
    return _image_checks(spec.sandbox_image, programs, group, docker_client)


def _image_checks(image: str, programs: list[str], group: str, docker_client: Any) -> list[Check]:
    try:
        docker_client.images.get(image)
    except Exception as exc:
        return [
            Check(
                group,
                "sandbox_image",
                "FAIL",
                f"{image}: {exc}"[:200],
                f"docker pull {image} или соберите образ из infra/images/",
            )
        ]
    names = [p for p in programs if not p.startswith((".", "/"))]
    script = "echo ready; for p in " + " ".join(shlex.quote(p) for p in names) + "; do "
    script += 'command -v "$p" >/dev/null 2>&1 || echo "missing:$p"; done'
    try:
        raw = docker_client.containers.run(
            image, ["sh", "-c", script], remove=True, stdout=True, stderr=True
        )
    except Exception as exc:
        return [
            Check(
                group,
                "sandbox_image",
                "FAIL",
                f"{image} не запускается: {exc}"[:200],
                "в образе нужен /bin/sh; проверьте ENTRYPOINT",
            )
        ]
    output = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    checks = [Check(group, "sandbox_image", "OK", f"{image} запускается")]
    missing = [line.split(":", 1)[1] for line in output.splitlines() if line.startswith("missing:")]
    if missing:
        checks.append(
            Check(
                group,
                "gate_commands",
                "FAIL",
                f"в {image} нет: {', '.join(missing)}",
                "добавьте программы в образ или поправьте gate_commands",
            )
        )
    elif names:
        checks.append(Check(group, "gate_commands", "OK", f"в образе: {', '.join(names)}"))
    return checks


# --- граф ---------------------------------------------------------------------------------------


def profiles_for(registry: Registry) -> dict[str, CaseProfile]:
    base = {f: True for f in CaseProfile.model_fields if f != "extra"}
    extra = {
        s.enabled_if: True
        for s in registry.specs.values()
        if s.enabled_if and s.enabled_if not in base
    }
    everything = CaseProfile(**base, extra=extra)
    return {
        "всё включено": everything,
        "без mobile": everything.model_copy(update={"has_mobile": False}),
        "без geo": everything.model_copy(update={"has_geo": False}),
    }


def unreachable(registry: Registry, profile: CaseProfile) -> dict[str, str]:
    """Этапы, включённые своим enabled_if, но отрезанные выключенной зависимостью."""
    specs = registry.specs
    off: dict[str, str] = {}
    for step in registry.steps:
        for name in step:
            spec = specs[name]
            if spec.enabled_if and not profile.is_enabled(spec.enabled_if):
                off[name] = f"{spec.enabled_if}=false"
                continue
            blocked = [d for d in spec.depends_on if d in off]
            if blocked:
                off[name] = f"зависит от выключенного {', '.join(blocked)}"
    return {n: why for n, why in off.items() if why.startswith("зависит")}


def check_graph(registry: Registry) -> list[Check]:
    checks = []
    known = set(CaseProfile.model_fields) - {"extra"}
    custom = sorted(
        {
            s.enabled_if
            for s in registry.specs.values()
            if s.enabled_if and s.enabled_if not in known
        }
    )
    if custom:
        checks.append(
            Check(
                "граф",
                "enabled_if",
                "OK",
                f"флаги из CaseProfile.extra: {', '.join(custom)} — по умолчанию false",
                "",
            )
        )
    for label, profile in profiles_for(registry).items():
        cut = unreachable(registry, profile)
        if cut:
            checks.append(
                Check(
                    "граф",
                    label,
                    "FAIL",
                    "; ".join(f"{n}: {why}" for n, why in cut.items()),
                    "дайте этапу тот же enabled_if, что у зависимости, или уберите зависимость",
                )
            )
        else:
            active = sum(
                1
                for n, s in registry.specs.items()
                if n != _profile_name(registry)
                and not (s.enabled_if and not profile.is_enabled(s.enabled_if))
            )
            checks.append(Check("граф", label, "OK", f"этапов к запуску: {active}"))
    return checks


def _profile_name(registry: Registry) -> str:
    agent = registry.profile_agent
    return agent.spec.name if agent else PROFILE_STAGE


# --- вывод и CLI ------------------------------------------------------------------------------

STYLE = {"OK": "[green]OK[/]", "FAIL": "[bold red]FAIL[/]", "SKIP": "[dim]SKIP[/]"}


def render(checks: Sequence[Check]) -> Table:
    table = Table(title="specforge doctor", show_lines=False)
    for column in ("Компонент", "Проверка", "Итог", "Детали", "Как починить"):
        table.add_column(column, overflow="fold")
    for c in checks:
        table.add_row(c.group, c.name, STYLE[c.status], c.detail, c.hint)
    return table


def register_cli(app: typer.Typer, deps_factory: Callable[[], DoctorDeps] = DoctorDeps) -> None:
    @app.command("doctor")
    def doctor_command(
        width: Annotated[int | None, typer.Option(help="Ширина таблицы.")] = None,
    ) -> None:
        """Проверить стенд: vLLM, Postgres, Langfuse, GitLab, Docker, агенты и граф."""
        from dotenv import find_dotenv, load_dotenv  # noqa: PLC0415

        load_dotenv(find_dotenv(usecwd=True))
        checks = run_doctor(deps_factory())
        console = Console(width=width)
        console.print(render(checks))
        failed = sum(c.status == "FAIL" for c in checks)
        console.print(
            f"FAIL: {failed}, OK: {sum(c.status == 'OK' for c in checks)}, "
            f"SKIP: {sum(c.status == 'SKIP' for c in checks)}"
        )
        raise typer.Exit(1 if failed else 0)
