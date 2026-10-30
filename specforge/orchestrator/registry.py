"""Реестр агентов: каждый подкаталог agents/<name>/ с agent.py и переменной AGENT.

`Registry` — проверенный набор агентов конвейера и план его шагов:
- `depends_on` ссылаются на существующих агентов, циклов нет;
- `outputs` разных агентов не пересекаются (шаблоны сравниваются в пространстве artifacts/:
  `<stage>/<pattern>`);
- агентов со `stage="profile"` не больше одного, он идёт первым и ни от кого не зависит;
- агенты одной `parallel_group` не зависят друг от друга и пишут в разные каталоги этапов.

Каталоги с именем на `_` (образцы, `_example`) в конвейер не входят, но запускаются через
`specforge agent run`.
"""

import fnmatch
import importlib
import re
from collections.abc import Iterable
from functools import cached_property
from pathlib import Path

from specforge.core.agent_api import Agent
from specforge.core.artifacts import INPUT_STAGE
from specforge.core.contracts import AgentSpec

AGENTS_PACKAGE = "agents"
PROFILE_STAGE = "profile"
"""Этап агента, который строит CaseProfile; его узел графа — `profile`."""
RESERVED_NODES = frozenset({PROFILE_STAGE, "finalize"})
STAGE_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
"""Имя этапа — это каталог artifacts/<stage>/."""

Step = tuple[str, ...]
"""Шаг конвейера: один агент или параллельная группа."""


class RegistryError(Exception):
    pass


def agents_root() -> Path:
    package = importlib.import_module(AGENTS_PACKAGE)
    return Path(next(iter(package.__path__)))


def available_agents() -> list[str]:
    return sorted(
        d.name
        for d in agents_root().iterdir()
        if d.is_dir() and not d.name.startswith(("__", ".")) and (d / "agent.py").is_file()
    )


def load_agent(name: str) -> Agent:
    names = available_agents()
    if name not in names:
        raise RegistryError(f"агент {name!r} не найден; доступны: {', '.join(names) or '—'}")
    module = importlib.import_module(f"{AGENTS_PACKAGE}.{name}.agent")
    agent = getattr(module, "AGENT", None)
    if agent is None:
        raise RegistryError(f"в {AGENTS_PACKAGE}/{name}/agent.py нет переменной AGENT")
    if not isinstance(agent, Agent):
        raise RegistryError(f"{AGENTS_PACKAGE}.{name}.agent.AGENT не реализует протокол Agent")
    if agent.spec.name != name:
        raise RegistryError(
            f"AGENT.spec.name={agent.spec.name!r} не совпадает с каталогом agents/{name}/"
        )
    return agent


def load_all() -> dict[str, Agent]:
    return {name: load_agent(name) for name in available_agents()}


class Registry:
    """Проверенный набор агентов конвейера. Ошибки проверки — RegistryError со всеми проблемами."""

    def __init__(self, agents: Iterable[Agent]) -> None:
        self.agents: dict[str, Agent] = {}
        duplicates = []
        for agent in agents:
            if agent.spec.name in self.agents:
                duplicates.append(agent.spec.name)
            self.agents[agent.spec.name] = agent
        self.agents = dict(sorted(self.agents.items()))
        problems = [f"агент {name!r} зарегистрирован дважды" for name in duplicates]
        problems += validate(self.specs)
        if problems:
            raise RegistryError("реестр агентов неверен:\n" + "\n".join(f"- {p}" for p in problems))

    @classmethod
    def discover(cls) -> "Registry":
        """Все агенты из agents/*/agent.py, кроме каталогов на `_`."""
        return cls(load_agent(name) for name in available_agents() if not name.startswith("_"))

    @property
    def specs(self) -> dict[str, AgentSpec]:
        return {name: agent.spec for name, agent in self.agents.items()}

    @property
    def profile_agent(self) -> Agent | None:
        return next((a for a in self.agents.values() if a.spec.stage == PROFILE_STAGE), None)

    @cached_property
    def steps(self) -> list[Step]:
        """Шаги после узла profile, в порядке выполнения."""
        profile = self.profile_agent
        done = {profile.spec.name} if profile else set()
        return plan_steps(self.specs, done)

    def node_of(self, target: str) -> str:
        """Узел графа по имени агента или этапа (у этапа из нескольких агентов — первый)."""
        profile = self.profile_agent
        if target == PROFILE_STAGE or (profile is not None and target == profile.spec.name):
            return PROFILE_STAGE
        if target in self.agents:
            return target
        for step in self.steps:
            for name in step:
                if self.agents[name].spec.stage == target:
                    return name
        raise RegistryError(f"нет агента или этапа {target!r}")


def validate(specs: dict[str, AgentSpec]) -> list[str]:
    problems: list[str] = []
    for name, spec in specs.items():
        if name in RESERVED_NODES and spec.stage != PROFILE_STAGE:
            problems.append(f"{name}: имя зарезервировано за узлом графа")
        if not STAGE_PATTERN.match(spec.stage) or spec.stage == INPUT_STAGE:
            problems.append(
                f"{name}: недопустимое имя этапа {spec.stage!r} (это каталог artifacts/)"
            )
        for dep in spec.depends_on:
            if dep == name:
                problems.append(f"{name}: зависит сам от себя")
            elif dep not in specs:
                problems.append(f"{name}: depends_on ссылается на неизвестного агента {dep!r}")
        if spec.enabled_if == "extra":
            problems.append(f"{name}: enabled_if='extra' — не флаг CaseProfile")

    profiles = [n for n, s in specs.items() if s.stage == PROFILE_STAGE]
    if len(profiles) > 1:
        problems.append(f"несколько агентов этапа profile: {', '.join(profiles)}")
    for name in profiles:
        if specs[name].depends_on:
            problems.append(f"{name}: агент этапа profile идёт первым и не может иметь depends_on")

    cycle = find_cycle(specs)
    if cycle:
        problems.append("цикл зависимостей: " + " → ".join(cycle))
    problems += _output_conflicts(specs)
    if not cycle:
        problems += _group_conflicts(specs)
    return problems


def find_cycle(specs: dict[str, AgentSpec]) -> list[str] | None:
    """Первый найденный цикл по depends_on (замкнутый путь) или None."""
    state: dict[str, int] = {}  # 1 — в обходе, 2 — пройден
    path: list[str] = []

    def visit(name: str) -> list[str] | None:
        state[name] = 1
        path.append(name)
        for dep in specs[name].depends_on:
            if dep not in specs:
                continue
            if state.get(dep) == 1:
                return [*path[path.index(dep) :], dep]
            if dep not in state and (found := visit(dep)):
                return found
        path.pop()
        state[name] = 2
        return None

    for name in specs:
        if name not in state and (found := visit(name)):
            return found
    return None


def ancestors(specs: dict[str, AgentSpec], name: str) -> set[str]:
    """Все агенты, от которых `name` зависит прямо или транзитивно."""
    seen: set[str] = set()
    stack = list(specs[name].depends_on)
    while stack:
        dep = stack.pop()
        if dep in seen or dep not in specs:
            continue
        seen.add(dep)
        stack.extend(specs[dep].depends_on)
    return seen


def globs_overlap(a: str, b: str) -> bool:
    """Приближённая проверка пересечения glob-шаблонов: совпадение, вложенность одного в
    другой (`web/**` и `web/src/*.ts`) или общий литеральный префикс у шаблонов с `**`."""
    if a == b or fnmatch.fnmatchcase(a, b) or fnmatch.fnmatchcase(b, a):
        return True
    prefix_a, prefix_b = _literal_prefix(a), _literal_prefix(b)
    if "**" in a and prefix_b.startswith(prefix_a):
        return True
    return "**" in b and prefix_a.startswith(prefix_b)


def _literal_prefix(pattern: str) -> str:
    match = re.search(r"[*?\[]", pattern)
    return pattern if match is None else pattern[: match.start()]


def _output_conflicts(specs: dict[str, AgentSpec]) -> list[str]:
    problems = []
    names = list(specs)
    for i, first in enumerate(names):
        for second in names[i + 1 :]:
            for a in specs[first].outputs:
                for b in specs[second].outputs:
                    qa, qb = f"{specs[first].stage}/{a}", f"{specs[second].stage}/{b}"
                    if globs_overlap(qa, qb):
                        problems.append(f"outputs пересекаются: {first} {qa!r} и {second} {qb!r}")
    return problems


def _group_conflicts(specs: dict[str, AgentSpec]) -> list[str]:
    problems = []
    groups: dict[str, list[str]] = {}
    for name, spec in specs.items():
        if spec.parallel_group is not None:
            groups.setdefault(spec.parallel_group, []).append(name)
    for group, members in groups.items():
        for name in members:
            linked = ancestors(specs, name) & set(members)
            if linked:
                problems.append(
                    f"группа {group!r}: {name} зависит от {', '.join(sorted(linked))} "
                    "той же группы — параллельно их не запустить"
                )
        stages: dict[str, str] = {}
        for name in members:
            other = stages.setdefault(specs[name].stage, name)
            if other != name:
                problems.append(
                    f"группа {group!r}: {other} и {name} в одном этапе {specs[name].stage!r} — "
                    "параллельные агенты не могут делить каталог этапа"
                )
    return problems


def plan_steps(specs: dict[str, AgentSpec], done: set[str]) -> list[Step]:
    """Детерминированный топологический план: на каждом шаге — первый (по имени) готовый агент
    без группы или вся группа, все члены которой готовы. Если готовы лишь часть членов группы
    и больше нечего запускать — запускается готовая часть."""
    finished = set(done)
    remaining = [n for n in sorted(specs) if n not in finished]
    steps: list[Step] = []
    while remaining:
        ready = [n for n in remaining if set(specs[n].depends_on) <= finished]
        if not ready:
            raise RegistryError(f"зависимости не разрешаются: {', '.join(remaining)}")
        step: Step | None = None
        for name in ready:
            group = specs[name].parallel_group
            if group is None:
                step = (name,)
                break
            members = [n for n in remaining if specs[n].parallel_group == group]
            if all(m in ready for m in members):
                step = tuple(members)
                break
        if step is None:
            group = specs[ready[0]].parallel_group
            step = tuple(n for n in ready if specs[n].parallel_group == group)
        steps.append(step)
        finished.update(step)
        remaining = [n for n in remaining if n not in finished]
    return steps
