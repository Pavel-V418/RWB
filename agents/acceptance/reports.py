"""Отчёты о тестировании и о проверке безопасности из вердикта приёмщика — детерминированно,
без LLM: результаты проверок, дефекты и уязвимости с критичностью, итоговый статус."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.acceptance.agent import Verdict

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
MAX_ROWS = 50


def render_test_report(v: "Verdict") -> str:
    lines = [
        "# Отчёт о тестировании",
        "",
        f"**Итоговый статус:** {'пройдено' if v.ready else 'не пройдено'} "
        f"(блокеров: {len(v.blockers)}, регрессий: {len(v.regressions)}).",
        "",
        "## Шлюзы этапов на итоговом репозитории",
        "",
    ]
    if v.gates:
        lines += [
            "| Этап | Статус этапа | Шлюз сейчас | Тестов выполнено | Ошибок | Первая ошибка |",
            "|---|---|---|---:|---:|---|",
        ]
        for g in v.gates:
            now = {True: "зелёный", False: "красный", None: "не проверен"}[g.passed_now]
            if g.regression:
                now = "**регрессия**"
            tests = "—" if g.tests_executed is None else str(g.tests_executed)
            first = _cell(g.first_error or g.infra_error or "")
            lines.append(
                f"| {g.agent} | {g.state_status.value} | {now} | {tests} | {g.total_errors} "
                f"| {first} |"
            )
        executed = [g.tests_executed for g in v.gates if g.tests_executed is not None]
        lines += ["", f"Всего выполнено тестов: {sum(executed) if executed else 'нет отчётов'}."]
    else:
        lines.append("Шлюзы не перепроверялись (нет этапов с командами шлюза).")

    lines += ["", "## Запуск и пользовательский сценарий", "", *_runtime_lines(v)]

    c = v.coverage
    lines += ["", "## Покрытие требований", ""]
    if c.matrix is None:
        lines.append("Матрицы трассируемости нет — покрытие требований неизвестно.")
    else:
        lines.append(f"Матрица `{c.matrix}`: покрыто {c.covered} из {c.total} ({c.share:.0%}).")
        lines += [f"- {req.id}: {', '.join(req.missing)}" for req in c.uncovered[:MAX_ROWS]]

    lines += ["", "## Дефекты", ""]
    defects = [b for b in v.blockers if not _is_security(b)]
    lines += [f"- {_cell(d)}" for d in defects[:MAX_ROWS]] or ["Блокирующих дефектов нет."]
    if v.not_done:
        lines += ["", "## Не сделано", ""]
        lines += [f"- {_cell(n)}" for n in v.not_done[:MAX_ROWS]]
    return "\n".join(lines) + "\n"


def _runtime_lines(v: "Verdict") -> list[str]:
    r = v.runtime
    if r.compose_file is None:
        return ["docker-compose не найден — запуск не проверялся."]
    if not r.checked:
        return [f"Запуск не проверялся: {r.note}"]
    lines = [f"- `docker compose up` ({r.compose_file}): {'ок' if r.up_ok else 'ошибка'}"]
    for s in r.services:
        metrics = "есть" if s.metrics_ok else "нет"
        problems = f" — {'; '.join(s.problems)}" if s.problems else ""
        lines.append(
            f"- {s.service}: /health = {s.health or 'нет ответа'}, /metrics {metrics}{problems}"
        )
    if r.e2e_script is None:
        lines.append("- e2e / демо-сценарий: нет")
    else:
        lines.append(f"- e2e `{r.e2e_script}`: {'пройден' if r.e2e_ok else 'упал'}")
    return lines


def render_security_report(v: "Verdict") -> str:
    sec, secrets, static = v.security, v.secrets, v.static_analysis
    failed = bool(sec.blocking or secrets.findings)
    status = (
        "не пройдено"
        if failed
        else ("не проверено: нет отчёта сканера" if not sec.reports else "пройдено")
    )
    ci_job = (
        "нет"
        if v.ci.file is None
        else ("есть" if not any("безопасност" in p for p in v.ci.problems) else "нет")
    )
    lines = [
        "# Отчёт о проверке безопасности",
        "",
        f"**Итоговый статус:** {status}.",
        "",
        "## Инструменты",
        "",
        f"- Trivy (зависимости, образы, CVE): отчётов {len(sec.reports)}",
        f"- Статический анализ: {', '.join(static.tools) or 'не найден'}"
        + (f"; отчёты: {', '.join(static.reports[:5])}" if static.reports else ""),
        f"- Поиск секретов (приёмщик): проверено файлов {secrets.scanned_files}",
        f"- Job безопасности в GitLab CI: {ci_job}",
        "",
        "## Уязвимости",
        "",
    ]
    if sec.reports:
        lines += ["| Критичность | Количество |", "|---|---:|"]
        lines += [f"| {s} | {sec.counts.get(s, 0)} |" for s in SEVERITIES]
        if sec.blocking:
            lines += ["", "Блокирующие (HIGH и выше):", ""]
            lines += [f"- {b}" for b in sec.blocking[:MAX_ROWS]]
    else:
        lines.append("Отчётов сканера нет — уязвимости не проверены.")
    lines += ["", "## Секреты", ""]
    lines += [f"- **{_cell(f)}**" for f in secrets.findings[:MAX_ROWS]]
    lines += [f"- {_cell(f)} (проверить вручную)" for f in secrets.suspicious[:MAX_ROWS]]
    if not secrets.findings and not secrets.suspicious:
        lines.append("Секретов не найдено.")
    return "\n".join(lines) + "\n"


def _is_security(text: str) -> bool:
    return text.startswith(("уязвимости", "секреты"))


def _cell(text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first.replace("|", "\\|")[:200]
