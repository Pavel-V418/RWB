"""Мост между частями команды (packages/specforge-analysis, packages/specforge-web) и платформой.

Части написаны под контракт `run(input_dir, output_dir, llm) -> GateResult` со своими клиентами
LLM. Здесь:
- AnalysisLLM, WebLLM — их протоколы LLM поверх LLMClient платформы: все вызовы модели идут через
  LLMClient (учёт токенов, профили, ретраи, ожидание недоступной модели, телеметрия);
- team_result — их GateResult → StageResult платформы (шлюз — их, внутренний: диагностики
  попадают в StageResult.gate);
- write_questions — их вопросы аналитика → questions.json (протокол уточнений платформы).
"""

import json
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from specforge_analysis.llm import CallRecord, Metrics
from specforge_analysis.llm import LLMError as AnalysisLLMError
from specforge_web.agents.common.llm import guided_schema
from specforge_web.contracts.agent.agent_contract import LLMResponse

from specforge.core.agent_api import AgentContext, BaseAgent
from specforge.core.contracts import (
    QUESTIONS_FILE,
    ClarificationQuestion,
    Diagnostic,
    GateResult,
    ProfileName,
    StageResult,
    StageStatus,
    Usage,
)
from specforge.llm import ChatMessage, LLMClient, StructuredOutputError

METRICS_FILE = "metrics.jsonl"
MAX_DIAGNOSTICS = 20


def _messages(raw: Sequence[dict[str, Any]]) -> list[ChatMessage]:
    return [ChatMessage(role=m["role"], content=str(m.get("content") or "")) for m in raw]


class AnalysisLLM:
    """Протокол specforge_analysis.llm.LLM: structured(name, messages, schema, thinking,
    max_tokens) и metrics (строки metrics.jsonl — для страницы эффективности в документации)."""

    def __init__(
        self,
        client: LLMClient,
        *,
        metrics_file: Path | None = None,
        profile: ProfileName = ProfileName.ANALYSIS,
    ) -> None:
        self.client = client
        self.profile = profile
        self.metrics = Metrics(sink=metrics_file)

    def structured[T: BaseModel](
        self,
        name: str,
        messages: list[dict[str, Any]],
        schema: type[T],
        *,
        thinking: bool = False,
        max_tokens: int = 8192,
    ) -> T:
        started = time.monotonic()
        with self.client.track() as meter:
            try:
                result = self.client.structured(
                    _messages(messages),
                    schema,
                    self.profile,
                    thinking=thinking,
                    max_tokens=max_tokens,
                )
            except StructuredOutputError as exc:
                self._record(name, meter.usage, started, thinking, ok=False, reason=str(exc))
                raise AnalysisLLMError(str(exc)) from exc
        self._record(name, meter.usage, started, thinking)
        return result

    def _record(
        self,
        name: str,
        usage: Usage,
        started: float,
        thinking: bool,
        *,
        ok: bool = True,
        reason: str | None = None,
    ) -> None:
        self.metrics.add(
            CallRecord(
                name=name,
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                duration_s=round(time.monotonic() - started, 3),
                attempt=max(usage.calls, 1),
                cached=usage.dedup_hits > 0,
                ok=ok,
                retry_reason=reason[:300] if reason else None,
                thinking=thinking,
                model=self.client.config.model,
            )
        )


class WebLLM:
    """Протокол specforge_web.contracts.agent.agent_contract.LLM: complete(messages, json_schema,
    thinking, max_tokens, temperature, tag) -> LLMResponse."""

    def __init__(self, client: LLMClient, profile: ProfileName = ProfileName.CODE) -> None:
        self.client = client
        self.profile = profile

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        json_schema: dict[str, Any] | None = None,
        thinking: bool = False,
        max_tokens: int = 8192,
        temperature: float = 0.2,
        tag: str = "",
    ) -> LLMResponse:
        response = self.client.chat(
            _messages(messages),
            profile=self.profile,
            temperature=temperature,
            thinking=thinking,
            max_tokens=max_tokens,
            response_schema=guided_schema(json_schema) if json_schema is not None else None,
            schema_name=(tag or "output").replace(".", "_"),
        )
        usage = response.usage
        return LLMResponse(
            text=response.content,
            tokens_in=usage.prompt_tokens,
            tokens_out=usage.completion_tokens,
            tokens_cached=usage.cached_tokens,
        )


def diagnostic(
    tool: str,
    message: str,
    *,
    code: str | None = None,
    severity: str = "error",
    file: str | None = None,
    line: int | None = None,
) -> Diagnostic:
    return Diagnostic(
        tool=tool,
        code=code,
        message=message,
        severity="warning" if severity == "warning" else "error",
        file=file,
        line=line,
        category="generic",
    )


def team_result(
    agent: BaseAgent,
    ctx: AgentContext,
    *,
    started: float,
    usage: Usage,
    passed: bool,
    diagnostics: Iterable[Diagnostic],
    attempts: int = 1,
    summary: str = "",
) -> StageResult:
    """Шлюз части пройден → passed; не пройден, но артефакты есть (внутренние ремонты части
    исчерпаны) → degraded, конвейер идёт дальше; артефактов нет → failed."""
    found = list(diagnostics)
    errors = [d for d in found if d.severity == "error"]
    ordered = errors + [d for d in found if d.severity != "error"]
    gate = GateResult(
        passed=passed,
        diagnostics=ordered[:MAX_DIAGNOSTICS],
        total_errors=len(errors),
        duration_s=round(time.monotonic() - started, 3),
    )
    artifacts = agent.collect_artifacts(ctx)
    if passed:
        status = StageStatus.passed
    elif artifacts:
        status = StageStatus.degraded
    else:
        status = StageStatus.failed
    head = "шлюз пройден" if passed else f"шлюз не пройден: ошибок {len(errors)}"
    warnings = len(found) - len(errors)
    text = f"{head}, предупреждений {warnings}" + (f"; {summary}" if summary else "")
    if not passed and errors:
        text += f"; первая: {errors[0].message[:200]}"
    result = agent.result(
        status, started=started, usage=usage, artifacts=artifacts, summary=text[:500]
    )
    return result.model_copy(update={"attempts": max(attempts, 1), "gate": gate})


def crashed(
    agent: BaseAgent, ctx: AgentContext, started: float, usage: Usage, exc: Exception
) -> StageResult:
    """Исключение внутри части — failed этапа с причиной, конвейер продолжается."""
    return agent.result(
        StageStatus.failed,
        started=started,
        usage=usage,
        artifacts=agent.collect_artifacts(ctx),
        summary=f"{type(exc).__name__}: {exc}"[:500],
    )


def analysis_diagnostics(result: Any, tool: str) -> list[Diagnostic]:
    """Issue(code, severity, message, target) части анализа → Diagnostic."""
    return [
        diagnostic(
            tool,
            f"[{issue.target}] {issue.message}" if issue.target else issue.message,
            code=issue.code,
            severity=issue.severity,
        )
        for issue in [*result.errors, *result.warnings]
    ]


def web_diagnostics(result: Any, tool: str) -> list[Diagnostic]:
    """GateError(message, file, line, severity, code) веб-части → Diagnostic."""
    return [
        diagnostic(tool, e.message, code=e.code, severity=e.severity, file=e.file, line=e.line)
        for e in result.errors
    ]


def write_questions(requirements: Path, output_dir: Path) -> int:
    """Вопросы аналитика (requirements.json → questions[]) → questions.json платформы: ответ
    пользователя или допущение (default_option) попадут в input/clarifications.*."""
    if not requirements.is_file():
        return 0
    data = json.loads(requirements.read_text(encoding="utf-8"))
    questions = []
    for q in data.get("questions") or []:
        if not isinstance(q, dict) or not q.get("question") or not q.get("id"):
            continue
        options = ", ".join(str(o) for o in q.get("options") or [])
        why = str(q.get("impact") or "") + (f" Варианты: {options}." if options else "")
        questions.append(
            ClarificationQuestion(
                id=str(q["id"])[:40],
                question=str(q["question"]),
                why=why.strip(),
                impact=str(q.get("topic") or "other"),
                assumption=str(q.get("default_option") or ""),
            ).model_dump()
        )
    if questions:
        (output_dir / QUESTIONS_FILE).write_text(
            json.dumps({"questions": questions}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return len(questions)
