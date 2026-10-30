"""Инструмент run: команда в песочнице.

В контекст модели — усечённый вывод, полный лог — в runs/<id>/logs/.
"""

from specforge.llm import ToolSpec
from specforge.tools.base import Args, Tool, ToolEnv, ToolResult, arg_int, arg_str, truncate_middle

OUTPUT_HEAD_CHARS = 2000
OUTPUT_TAIL_CHARS = 4000
DEFAULT_TIMEOUT_S = 300


def run(env: ToolEnv, args: Args) -> ToolResult:
    command = arg_str(args, "cmd")
    timeout = min(
        arg_int(args, "timeout", DEFAULT_TIMEOUT_S) or DEFAULT_TIMEOUT_S, int(env.max_run_timeout_s)
    )
    result = env.sandbox.exec(command, timeout_s=timeout)
    output = result.output
    log_note = f"полный лог: {result.log_path}" if result.log_path else ""
    status = f"exit {result.exit_code}"
    if result.timed_out:
        status += f" (превышен таймаут {timeout} с)"
    body = truncate_middle(output, OUTPUT_HEAD_CHARS, OUTPUT_TAIL_CHARS, log_note)
    lines = output.count("\n") + (1 if output and not output.endswith("\n") else 0)
    log_part = f", лог: {result.log_path}" if result.log_path else ""
    return ToolResult(
        content=f"$ {command}\n{status}, {result.duration_s:.1f} с\n{body}".rstrip(),
        stub=f"[run: {status}, {lines} строк{log_part}]",
    )


RUN = Tool(
    ToolSpec(
        name="run",
        description=(
            "Выполнить shell-команду в /work (сборка, тесты, линтер). В ответе — код выхода "
            f"и вывод: первые {OUTPUT_HEAD_CHARS} и последние {OUTPUT_TAIL_CHARS} символов."
        ),
        parameters={
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "Команда shell"},
                "timeout": {
                    "type": "integer",
                    "description": f"Таймаут, с (по умолчанию {DEFAULT_TIMEOUT_S})",
                },
            },
            "required": ["cmd"],
        },
    ),
    run,
)
