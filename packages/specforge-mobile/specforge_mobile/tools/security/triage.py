"""
Триаж находок: сначала детерминированные правила, потом LLM пачками.

Вердикты: подтверждено (остаётся open, может блокировать) / ложное (false_positive) /
требует проверки (open, не блокирует). Кэш по id находки: повторный скан не тратит токены.
Секреты в LLM не отправляются: для них нет окна кода, длинные строки маскируются.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .findings import Finding

TEST_PATH = re.compile(r"(^|/)(tests?|fixtures?|__tests__|testdata|src/test|samples?)/|(^|/)test_[^/]+\.py$|"
                       r"Test\.kt$|\.example$|\.sample$|example\.(env|ya?ml|json)$", re.I)
TOKENISH = re.compile(r"[A-Za-z0-9_\-+/=]{16,}")
BATCH = 15


def rule_triage(f: Finding) -> bool:
    """True — вердикт вынесен правилом, LLM не нужна."""
    if f.status != "open":
        return True
    if f.severity == "info":
        f.status, f.verdict_reason = "accepted", "информационное сообщение"
        return True
    if f.tool == "contract":  # провал функционального теста API — факт, LLM не нужна
        f.verdict_reason = "подтверждено: контрактный тест API воспроизвёл нарушение"
        return True
    if f.category == "dependencies":
        f.verdict_reason = f"подтверждено: уязвимая версия зависимости ({f.cve or f.rule})"
        f.fix = f.fix or "обновить пакет до версии с исправлением (см. Fixed Version в отчёте сканера)"
        return True
    if f.category == "secrets" and re.search(r"\.example$|\.sample$|example\.|fixtures?/", f.path, re.I):
        f.status, f.verdict_reason = "false_positive", "пример/фикстура: значения не настоящие"
        return True
    return False


def code_window(root: Path, f: Finding, radius: int = 4) -> str:
    if f.category == "secrets" or not f.line:
        return ""
    p = root / f.path
    if not p.is_file():
        return ""
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    lo, hi = max(1, f.line - radius), min(len(lines), f.line + radius)
    out = "\n".join(f"{'>' if i == f.line else ' '}{i:4d} | {lines[i - 1]}" for i in range(lo, hi + 1))
    return TOKENISH.sub(lambda m: m[0][:4] + "…", out) if f.category in ("secrets", "authn") else out


PROMPT = """Ты — специалист по безопасности приложений. Для каждой находки сканера реши:
- "confirmed": реальная проблема в коде продукта;
- "false_positive": ложное срабатывание (тестовый код, недостижимо, значение не секрет, проверка уже есть);
- "uncertain": без запуска не понять.
Тестовые файлы — обычно false_positive, если только это не настоящий секрет.
Ответ — ТОЛЬКО JSON-массив без markdown:
[{{"id": "...", "verdict": "confirmed|false_positive|uncertain", "reason": "1 фраза по-русски",
  "fix": "как исправить, 1 фраза (для confirmed)"}}]

Находки:
{items}"""


def _parse(text: str) -> list[dict]:
    text = re.sub(r"^```\w*\s*|\s*```$", "", text.strip())
    start, end = text.find("["), text.rfind("]")
    return json.loads(text[start:end + 1]) if start >= 0 < end else []


def llm_triage(findings: list[Finding], root: Path, llm, cache: dict, log=print) -> dict:
    """Возвращает статистику {calls, tokens, cached}."""
    stats = {"calls": 0, "tokens": 0, "cached": 0}
    todo = []
    for f in findings:
        if f.id in cache:
            v = cache[f.id]
            _apply(f, v)
            stats["cached"] += 1
        elif not rule_triage(f):
            todo.append(f)
    if not todo or llm is None:
        for f in todo:
            f.verdict_reason = f.verdict_reason or "требует проверки: LLM-триаж не запускался"
        return stats
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        items = []
        for f in batch:
            item = {"id": f.id, "tool": f.tool, "rule": f.rule, "severity": f.severity, "category": f.category,
                    "location": f.location, "message": f.title[:300]}
            w = code_window(root, f)
            if w:
                item["code"] = w
            items.append(item)
        messages = [{"role": "user", "content": PROMPT.format(items=json.dumps(items, ensure_ascii=False, indent=1))}]
        try:
            reply = llm.chat(messages, max_tokens=4000)
            stats["calls"] += 1
            stats["tokens"] += getattr(reply, "tokens", 0)
            verdicts = {v.get("id"): v for v in _parse(getattr(reply, "text", reply))}
        except Exception as e:  # LLM недоступна — находки остаются открытыми, контур не падает
            log(f"  LLM-триаж: ошибка ({e}); пачка остаётся «требует проверки»")
            for f in batch:  # в кэш не пишем: при следующем запуске LLM разберёт их заново
                _apply(f, {"verdict": "uncertain", "reason": "LLM недоступна"})
            continue
        for f in batch:
            v = verdicts.get(f.id)
            if v:
                cache[f.id] = v
            _apply(f, v or {"verdict": "uncertain", "reason": "LLM не дала вердикт"})
    return stats


def _apply(f: Finding, v: dict) -> None:
    verdict, reason = v.get("verdict", "uncertain"), v.get("reason", "")
    if verdict == "confirmed":
        f.verdict_reason, f.fix = f"подтверждено: {reason}", v.get("fix", "") or f.fix
    elif verdict == "false_positive":
        f.status, f.verdict_reason = "false_positive", f"ложное: {reason}"
    else:
        f.verdict_reason = f"требует проверки: {reason}"
