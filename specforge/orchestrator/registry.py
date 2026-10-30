"""Реестр агентов: каждый подкаталог agents/<name>/ с agent.py и переменной AGENT."""

import importlib
from pathlib import Path

from specforge.core.agent_api import Agent

AGENTS_PACKAGE = "agents"


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
