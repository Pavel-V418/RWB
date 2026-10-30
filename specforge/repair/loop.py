"""Цикл ремонта этапа по диагностикам шлюза — общий для всех агентов.

До spec.max_repairs итераций:
  git-снимок workspace → узкий контекст + известные решения из fix memory → agent_loop
  (профиль REPAIR; read_file, str_replace, grep и run только с командами шлюза) →
  проверка правок → повторный шлюз.
Итерация откатывается к снимку, если ошибок стало больше, изменён защищённый файл или в правках
появилось глушение ошибок / отключение тестов. Зелёный шлюз — успех, решение пишется в fix
memory. Ремонты исчерпаны (или модель сообщила об ошибке в контракте) — статус degraded, откат
к последнему зелёному снимку, если он был, и событие escalation.
"""

import dataclasses
import re
import time
from pathlib import Path
from string import Template

import structlog
from pydantic import JsonValue

from specforge.core.agent_api import AgentContext, agent_loop
from specforge.core.contracts import AgentSpec, GateResult, StageResult, StageStatus, Usage
from specforge.core.events import Event, EventType
from specforge.gates.runner import GateRunner
from specforge.llm import ProfileName
from specforge.repair.context import build_repair_context
from specforge.repair.fix_memory import (
    FixMemory,
    KnownFix,
    fix_memory_from_env,
    lookup_many,
    signature_of,
)
from specforge.repair.snapshots import STATE_DIR, SnapshotError, Snapshotter
from specforge.tools.base import glob_match

log = structlog.get_logger(__name__)

PROMPTS = Path(__file__).parent / "prompts"
REPAIR_TOOLS = ("read_file", "str_replace", "grep", "run")
REPAIR_MAX_STEPS = 20
CONTRACT_MARKER = "КОНТРАКТ:"
MAX_RECORDED_SIGNATURES = 5

# Правки, которые глушат ошибки или выключают тесты вместо исправления (по добавленным строкам).
FORBIDDEN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), reason)
    for pattern, reason in (
        (r"#\s*pragma\s+warning\s+disable", "#pragma warning disable"),
        (r"@Suppress(Warnings)?\b", "@Suppress"),
        (r"eslint-disable", "eslint-disable"),
        (r"@ts-(ignore|nocheck|expect-error)", "@ts-ignore"),
        (r"\bas\s+any\b|:\s*any\b(?!\w)", "тип any"),
        (r"#\s*type:\s*ignore|#\s*noqa", "# type: ignore / noqa"),
        (r"\[(Fact|Theory)\s*\(\s*Skip\s*=|\[Ignore\]|@Disabled\b|@Ignore\b", "отключение теста"),
        (r"\b(xit|xdescribe|xtest)\s*\(|\b(it|test|describe)\.skip\s*\(", "отключение теста"),
        (r"pytest\.(mark\.)?skip", "отключение теста"),
        (r"catch\s*(\([^)]*\))?\s*\{\s*\}|except(\s+\w+)?\s*:\s*pass\b", "пустой catch"),
        (r"catch\s*\(\s*(Exception|Throwable|e\s*:\s*(Exception|Throwable))\b", "catch-all"),
    )
)


def repair_loop(
    spec: AgentSpec,
    ctx: AgentContext,
    gate_result: GateResult,
    *,
    memory: FixMemory | None = None,
    runner: GateRunner | None = None,
    snapshots: Snapshotter | None = None,
) -> StageResult:
    if ctx.sandbox is None:
        raise ValueError("repair_loop: у этапа нет песочницы")
    return _Repair(spec, ctx, gate_result, memory, runner, snapshots).run()


class _Repair:
    def __init__(
        self,
        spec: AgentSpec,
        ctx: AgentContext,
        gate: GateResult,
        memory: FixMemory | None,
        runner: GateRunner | None,
        snapshots: Snapshotter | None,
    ) -> None:
        assert ctx.sandbox is not None
        self.spec = spec
        self.ctx = ctx
        self.sandbox = ctx.sandbox
        self.current = gate
        self.memory = memory or fix_memory_from_env()
        self.runner = runner or GateRunner(logs_dir=ctx.workdir / "logs")
        self.snaps = snapshots or Snapshotter(ctx.sandbox)
        self.protected = [*spec.protected_globs, f"{STATE_DIR}/**"]
        self.repair_ctx = dataclasses.replace(
            ctx,
            llm=ctx.llm.for_repair(),  # thinking агента не наследуется (config/profiles.yaml)
            protected_globs=[*ctx.protected_globs, *self.protected],
            allowed_commands=list(spec.gate_commands),
        )
        self.usage = Usage()
        self.notes: list[str] = []
        self.started = time.monotonic()

    def run(self) -> StageResult:
        if not self.snaps.available:
            log.warning("repair.no_snapshots", agent=self.spec.name)
        system_prompt = (PROMPTS / "system.md").read_text("utf-8")
        reason = f"ремонты исчерпаны ({self.spec.max_repairs})"
        attempts = 0

        for attempt in range(1, self.spec.max_repairs + 1):
            remaining = self.ctx.budget_tokens - self.ctx.llm.usage.total_tokens
            if remaining <= 0:
                reason = "исчерпан бюджет токенов этапа"
                break
            attempts = attempt
            self._emit(EventType.repair_attempt, attempt=attempt, errors=self.current.total_errors)
            snapshot = self._snapshot(f"repair-{attempt}")
            known = lookup_many(self.memory, self.current.diagnostics)
            if known:
                self._emit("fix_memory_hits", attempt=attempt, hits=len(known))

            outcome = agent_loop(
                dataclasses.replace(self.repair_ctx, budget_tokens=remaining),
                system_prompt,
                self._task(attempt, known),
                REPAIR_TOOLS,
                max_steps=min(self.spec.max_steps, REPAIR_MAX_STEPS),
                profile=ProfileName.REPAIR,
            )
            self.usage = self.usage.add(outcome.usage)

            if outcome.stopped_reason == "finish" and outcome.summary.strip().startswith(
                CONTRACT_MARKER
            ):
                self._rollback(snapshot, attempt, "модель сообщила об ошибке в контракте")
                reason = outcome.summary.strip()
                break

            violation = self._violation(snapshot)
            if violation:
                self._rollback(snapshot, attempt, violation)
                continue

            diff = self._diff(snapshot)
            gate = self.runner.run(self.spec, self.sandbox, self.ctx.run_id)
            if gate.passed:
                self._remember(outcome.summary, diff)
                self._emit("repair_succeeded", attempt=attempt)
                return self._result(
                    StageStatus.passed, attempts, gate, f"починено: {outcome.summary}"
                )
            if gate.total_errors > self.current.total_errors:
                self._rollback(
                    snapshot,
                    attempt,
                    f"ошибок стало больше: {self.current.total_errors} → {gate.total_errors}",
                )
                continue
            self.notes.append(
                f"Попытка {attempt}: ошибок {self.current.total_errors} → {gate.total_errors}. "
                f"Итог попытки: {outcome.summary[:300]}"
            )
            self.current = gate

        restored = self._restore_green()
        self._emit(
            "escalation",
            reason=reason[:500],
            errors=self.current.total_errors,
            restored_green=restored,
        )
        summary = f"degraded: {reason}"
        if restored:
            summary += "; workspace откачен к последнему зелёному снимку"
        return self._result(StageStatus.degraded, attempts, self.current, summary)

    # --- шаги ---------------------------------------------------------------------------------

    def _task(self, attempt: int, known: list[KnownFix]) -> str:
        context = build_repair_context(
            self.current.diagnostics,
            self.sandbox.workspace,
            protected_globs=self.spec.protected_globs,
        )
        if self.current.total_errors > len(self.current.diagnostics):
            context.text += f"\n\n(всего ошибок: {self.current.total_errors}, показаны первые)"
        known_text = ""
        if known:
            items = "\n\n".join(
                f"{i}. {fix.fix_summary}\n```diff\n{fix.diff[:1500]}\n```"
                for i, fix in enumerate(known, 1)
            )
            known_text = (
                "\n\n## Известные решения похожих ошибок\n"
                "Применяй, только если подходят к этому коду.\n\n" + items
            )
        notes = (
            "\n\n## Предыдущие попытки\n" + "\n".join(f"- {n}" for n in self.notes)
            if self.notes
            else ""
        )
        template = Template((PROMPTS / "task.md").read_text("utf-8"))
        return template.safe_substitute(
            stage=self.spec.stage,
            agent=self.spec.name,
            total_errors=str(self.current.total_errors),
            attempt=str(attempt),
            max_attempts=str(self.spec.max_repairs),
            gate_commands="\n".join(f"  {c}" for c in self.spec.gate_commands),
            protected="\n".join(f"  {g}" for g in self.spec.protected_globs) or "  (нет)",
            context=context.text or "(диагностик нет — смотри вывод команд шлюза)",
            known_fixes=known_text,
            notes=notes,
        )

    def _violation(self, snapshot: str | None) -> str | None:
        if snapshot is None:
            return None
        try:
            changed = self.snaps.changed_files(snapshot)
            diff = self.snaps.diff(snapshot, max_chars=200_000)
        except SnapshotError as exc:
            log.warning("repair.diff_failed", error=str(exc))
            return None
        touched = [f for f in changed if any(glob_match(f, g) for g in self.protected)]
        if touched:
            return f"изменены защищённые файлы: {', '.join(touched[:5])}"
        for line in diff.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            for pattern, what in FORBIDDEN_PATTERNS:
                if pattern.search(line):
                    return f"запрещённая правка ({what}): {line[1:].strip()[:150]}"
        for block in diff.split("diff --git ")[1:]:
            header = block.splitlines()[0] if block else ""
            if "\ndeleted file mode" in block[:300] and re.search(r"test", header, re.IGNORECASE):
                return f"удалён тестовый файл: {header.split(' b/')[-1]}"
        return None

    def _rollback(self, snapshot: str | None, attempt: int, reason: str) -> None:
        self.notes.append(f"Попытка {attempt} отклонена и откачена: {reason}")
        if snapshot is not None:
            try:
                self.snaps.restore(snapshot)
            except SnapshotError as exc:
                log.error("repair.rollback_failed", error=str(exc))
        self._emit("repair_rolled_back", attempt=attempt, reason=reason[:300])

    def _remember(self, summary: str, diff: str) -> None:
        if not diff.strip():
            return
        signatures = []
        for diagnostic in self.current.diagnostics:
            signature = signature_of(diagnostic)
            if diagnostic.severity == "error" and signature not in signatures:
                signatures.append(signature)
        for signature in signatures[:MAX_RECORDED_SIGNATURES]:
            self.memory.record(signature, summary, diff, agent=self.spec.name)

    def _restore_green(self) -> bool:
        try:
            green = self.snaps.green()
            if green:
                self.snaps.restore(green)
                return True
        except SnapshotError as exc:
            log.error("repair.green_restore_failed", error=str(exc))
        return False

    def _snapshot(self, label: str) -> str | None:
        if not self.snaps.available:
            return None
        try:
            return self.snaps.snapshot(label)
        except SnapshotError as exc:
            log.warning("repair.snapshot_failed", error=str(exc))
            return None

    def _diff(self, snapshot: str | None) -> str:
        if snapshot is None:
            return ""
        try:
            return self.snaps.diff(snapshot)
        except SnapshotError:
            return ""

    def _result(
        self, status: StageStatus, attempts: int, gate: GateResult, summary: str
    ) -> StageResult:
        return StageResult(
            agent=self.spec.name,
            status=status,
            attempts=attempts,
            usage=self.usage,
            duration_s=time.monotonic() - self.started,
            gate=gate,
            summary=summary[:1000],
        )

    def _emit(self, kind: str, **data: JsonValue) -> None:
        self.ctx.emit(
            Event(
                kind=kind,
                run_id=self.ctx.run_id,
                stage=self.spec.stage,
                agent=self.spec.name,
                data=data,
            )
        )
