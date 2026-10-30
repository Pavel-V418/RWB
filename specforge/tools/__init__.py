"""Инструменты агентов. Набор агента задаётся именами в AgentSpec.tools; finish доступен всегда."""

from collections.abc import Sequence

from specforge.llm import ToolSpec
from specforge.tools.base import Tool, ToolEnv, ToolError, ToolResult
from specforge.tools.fs import GREP, LIST_DIR, READ_FILE, STR_REPLACE, WRITE_FILE
from specforge.tools.shell import RUN

FINISH = ToolSpec(
    name="finish",
    description="Завершить работу, когда задача выполнена. summary — что сделано, кратко.",
    parameters={
        "type": "object",
        "properties": {"summary": {"type": "string", "description": "Итог работы"}},
        "required": ["summary"],
    },
)

TOOLS: dict[str, Tool] = {
    t.name: t for t in (READ_FILE, WRITE_FILE, STR_REPLACE, LIST_DIR, GREP, RUN)
}


def get_tools(names: Sequence[str]) -> list[Tool]:
    """Инструменты по именам в заданном порядке (finish сюда не входит — он управляющий)."""
    unknown = [n for n in names if n not in TOOLS and n != FINISH.name]
    if unknown:
        raise ValueError(f"неизвестные инструменты {unknown}; доступны: {sorted(TOOLS)}")
    seen: dict[str, Tool] = {}
    for name in names:
        if name in TOOLS:
            seen.setdefault(name, TOOLS[name])
    return list(seen.values())


__all__ = ["FINISH", "TOOLS", "Tool", "ToolEnv", "ToolError", "ToolResult", "get_tools"]
