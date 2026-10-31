"""
Android-агент.

    run(input_dir, output_dir, llm) -> GateResult{passed, errors, tokens, attempts, ...}

Шаги: план экранов (JSON) → слой данных → экраны (по одному вызову) → навигация и скриншот-тест →
шлюз (сборка, тесты, скриншоты) → проверка по UX-спеке → ремонт (до max_repairs раз).
Результат: <output_dir>/project (Android-проект), gate_report.json, agent_log.json, screenshots/.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Callable

from specforge_mobile.tools.android_gate import screens as shots

from . import context as ctx
from . import prompts
from . import workspace as ws

REPO = Path(__file__).resolve().parents[2]  # корень пакета packages/specforge-mobile
DEFAULT_TEMPLATE = REPO / "android"
GATE_SCRIPT = Path(__file__).resolve().parents[1] / "tools/android_gate/android_gate.py"
MAX_REPAIR_ERRORS = 15

# Образцы из каркаса (эталон кейса №1). Если файла нет — шаг идёт без образца.
REF_DATA = ["feature/session/SessionModels.kt", "feature/session/SessionSamples.kt",
            "feature/session/ApiSessionRepository.kt",
            "feature/wiring/GeneratedApi.kt", "feature/FeatureModule.kt", "test:FakeSessionRepository.kt"]
REF_SCREEN = ["feature/session/SessionViewModel.kt", "feature/session/SessionScreen.kt",
              "feature/session/SessionSamples.kt", "test:SessionViewModelTest.kt"]
REF_WIRING = ["feature/AppRoot.kt"]


# ---------------------------------------------------------------- LLM
class Usage:
    def __init__(self, raw_dir: Path | None = None):
        self.tokens, self.calls, self.steps = 0, 0, []
        self.raw_dir = raw_dir  # сырые ответы LLM — для разбора неудачных попыток

    def save_raw(self, step: str, text: str):
        if self.raw_dir:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            (self.raw_dir / f"{self.calls:02d}_{step.replace(':', '_')}.txt").write_text(text, encoding="utf-8")

    def add(self, step: str, tokens: int, sec: float, note: str = ""):
        self.tokens += tokens
        self.calls += 1
        self.steps.append({"step": step, "tokens": tokens, "sec": round(sec, 1), "note": note})


def _ask(llm, system: str, user: str, usage: Usage, step: str) -> str:
    """Адаптер к llm: объект с .chat(messages) (LLMReply | dict | str) или просто функция."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    t0 = time.monotonic()
    reply = llm.chat(messages) if hasattr(llm, "chat") else llm(messages)
    if isinstance(reply, str):
        text, tokens = reply, 0
    elif isinstance(reply, dict):
        text = reply.get("text", "")
        u = reply.get("usage") or {}
        tokens = u.get("total_tokens") or (u.get("prompt_tokens", 0) + u.get("completion_tokens", 0))
    else:
        text, tokens = reply.text, getattr(reply, "tokens", 0)
    usage.add(step, tokens, time.monotonic() - t0)
    usage.save_raw(step, text)
    return text


def extract_json(text: str) -> dict:
    text = re.sub(r"^```\w*\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("в ответе нет JSON")
    return json.loads(text[start:end + 1])


# ---------------------------------------------------------------- окружение
def ensure_toolchain(project: Path) -> None:
    """JDK 17+ для Gradle и путь к Android SDK, если их не задали явно (удобно на ноутбуке)."""
    if not os.environ.get("JAVA_HOME") and not shutil.which("java"):
        for base, pattern in ((Path("C:/Program Files/Eclipse Adoptium"), "jdk-21*"),
                              (Path("C:/Program Files/Eclipse Adoptium"), "jdk-17*"),
                              (Path("/usr/lib/jvm"), "java-21*"), (Path("/usr/lib/jvm"), "java-17*")):
            hits = sorted(base.glob(pattern)) if base.is_dir() else []
            if hits:
                os.environ["JAVA_HOME"] = str(hits[-1])
                break
    lp = project / "local.properties"
    if not lp.exists() and not (os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")):
        sdk = Path(os.environ.get("LOCALAPPDATA", "")) / "Android/Sdk"
        if sdk.is_dir():
            lp.write_text("sdk.dir=" + str(sdk).replace("\\", "\\\\").replace(":", "\\:") + "\n", encoding="utf-8")


def _load_gate():
    spec = importlib.util.spec_from_file_location("android_gate", GATE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def default_gate(project: Path, offline: bool = False, timeout: int = 1200) -> dict:
    gate = _load_gate()
    code, log = gate.run_gradle(project, offline, timeout)
    (project / "build").mkdir(exist_ok=True)
    (project / "build/gate.log").write_text(log, encoding="utf-8")
    return gate.evaluate(project, code, log)


# ---------------------------------------------------------------- проверки
def related_files(project: Path, errors: list[dict], skip: set[str], limit: int = 3) -> list[str]:
    """Файлы feature/, где объявлены идентификаторы из сообщений об ошибках ('X') — их отдаём целиком."""
    names = {n for e in errors for n in re.findall(r"'(\w+)'", str(e.get("message", "")))}
    if not names:
        return []
    decl = re.compile(rf"\b(?:class|interface|object|fun|val|var)\s+(?:{'|'.join(map(re.escape, names))})\b")
    found = []
    for base in (ctx.FEATURE_MAIN, ctx.FEATURE_TEST):
        for f in sorted((project / base).rglob("*.kt")):
            rel = f.relative_to(project).as_posix()
            if rel not in skip and decl.search(f.read_text(encoding="utf-8")):
                found.append(rel)
    return found[:limit]


def _signature(errors: list[dict]) -> set[tuple]:
    return {(e.get("file"), e.get("message")) for e in errors}


def _reference(template: Path, items: list[str]) -> str:
    rels = []
    for it in items:
        if it.startswith("test:"):
            hits = sorted((template / ctx.FEATURE_TEST).rglob(it[5:]))
            rels += [h.relative_to(template).as_posix() for h in hits[:1]]
        else:
            rels.append(f"{ctx.FEATURE_MAIN}/{it[len('feature/'):]}")
    return ctx.read_files(template, rels) or "(образца нет)"


# ---------------------------------------------------------------- агент
def run(input_dir, output_dir, llm, *, template: Path | None = None, spec: Path | None = None,
        api_client: Path | None = None, max_repairs: int = 3, offline: bool = False,
        gate: Callable[[Path], dict] | None = None, log: Callable[[str], None] = print,
        resume: bool = False) -> dict:
    """resume=True: проект уже сгенерирован в <output_dir>/project — только шлюз и ремонт (экономия токенов)."""
    t_start = time.monotonic()
    input_dir, output_dir = Path(input_dir).resolve(), Path(output_dir).resolve()
    template = Path(template or DEFAULT_TEMPLATE).resolve()
    project = output_dir / "project"
    output_dir.mkdir(parents=True, exist_ok=True)
    usage, notes = Usage(output_dir / "llm"), []
    gate = gate or (lambda p: default_gate(p, offline))

    # --- входы
    spec_raw, screens = ctx.load_screen_spec(input_dir, spec)
    requirements = ctx.requirements_digest(input_dir)
    resumed = resume and (project / ctx.FEATURE_MAIN / "AppRoot.kt").is_file()
    if not resumed:
        ws.prepare(template, project, api_client)
    ensure_toolchain(project)
    api = ctx.api_digest(project)
    contract = (template / "AGENT_CONTRACT.md").read_text(encoding="utf-8")
    system = prompts.system_prompt(contract, ctx.core_digest(project), ctx.libraries(project))
    log(f"экранов в спеке: {len(screens)} ({', '.join(s['screenId'] for s in screens)})")

    def apply(text: str, step: str) -> list[str]:
        existing = {p.relative_to(project).as_posix() for p in (project / "app/src").rglob("*.kt")}
        files, warnings = ws.parse_files(text, existing)
        written, rejected = ws.write_files(project, files)
        if rejected:
            notes.append(f"{step}: отклонены пути вне контракта: {rejected}")
        notes.extend(f"{step}: {w}" for w in warnings)
        log(f"  {step}: записано файлов {len(written)}" + (f", отклонено {len(rejected)}" if rejected else ""))
        return written

    if not resumed:
        # --- 1. план
        plan = None
        msg = prompts.plan_prompt(spec_raw, screens, requirements, api)
        for _ in range(2):
            text = _ask(llm, system, msg, usage, "plan")
            try:
                plan = extract_json(text)
                break
            except (ValueError, json.JSONDecodeError) as e:
                msg += f"\n\nПрошлый ответ не разобрался как JSON ({e}). Верни только JSON."
        if plan is None:
            raise RuntimeError("LLM не вернула план в JSON")
        planned = {s.get("screenId") for s in plan.setdefault("screens", [])}
        for s in screens:  # спека главнее плана: недостающие экраны и состояния добавляем сами
            if s["screenId"] not in planned:
                plan["screens"].append({"screenId": s["screenId"], "package": re.sub(r"\W", "", s["screenId"]).lower(),
                                        "states": s["states"]})
                notes.append(f"plan: экран {s['screenId']} добавлен из спеки")
        by_id = {s["screenId"]: s for s in screens}
        for p in plan["screens"]:
            if p.get("screenId") in by_id:
                p["states"] = by_id[p["screenId"]]["states"]
        (output_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"  план: экранов {len(plan['screens'])}, репозиториев {len(plan.get('repositories', []))}")

        # --- 2. слой данных, экраны, сборка
        apply(_ask(llm, system, prompts.data_prompt(plan, api, _reference(template, REF_DATA)), usage, "data"), "data")
        ref_screen = _reference(template, REF_SCREEN)
        for p in plan["screens"]:
            if p.get("screenId") not in by_id:
                continue  # лишние экраны плана не генерируем — их нет в спеке
            text = _ask(llm, system, prompts.screen_prompt(p, plan, ctx.feature_outline(project), ref_screen,
                                                           requirements), usage, f"screen:{p['screenId']}")
            apply(text, f"screen:{p['screenId']}")
        apply(_ask(llm, system, prompts.wiring_prompt(plan, screens, ctx.feature_outline(project),
                                                      _reference(template, REF_WIRING)), usage, "wiring"), "wiring")

    # --- 3. шлюз и ремонт
    attempts, result, seen = 0, {}, set()
    while True:
        attempts += 1
        restored = ws.restore_frozen(template, project)
        if restored:
            notes.append(f"попытка {attempts}: откатаны замороженные файлы {restored}")
        ws.clean_outputs(project)
        shots.generate(project, screens)          # скриншот-тесты из спеки — без LLM
        pre = shots.check(project, screens)       # нет Sample/состояния — Gradle не тратим
        if pre:
            log(f"проверка по UX-спеке, попытка {attempts}: ошибок {len(pre)} (сборка не запускалась)")
            result = {"passed": False, "errors": pre}
        else:
            log(f"шлюз, попытка {attempts}…")
            result = gate(project)
        errors = list(result.get("errors", []))
        if result.get("passed"):  # сборка и тесты зелёные — все снимки из спеки на месте?
            errors += shots.verify(project, screens)
        result["errors"] = errors
        result["passed"] = bool(result.get("passed")) and not errors
        log(f"  {'ПРОЙДЕН' if result['passed'] else f'ошибок: {len(errors)}'}")
        if result["passed"] or attempts > max_repairs:
            break
        fix_files = sorted({e["file"] for e in errors if e.get("file") and ws.is_allowed(e["file"])})
        fix_files += related_files(project, errors, set(fix_files))
        repeated = _signature(errors) & seen
        seen |= _signature(errors)
        others = ctx.feature_outline(project)
        prompt = prompts.repair_prompt(errors[:MAX_REPAIR_ERRORS], ctx.read_files(project, fix_files)
                                       or "(ошибки без файла — смотри сообщения)", others, attempts,
                                       fix_files[0] if fix_files else "")
        if repeated:
            prompt += ("\n\nВНИМАНИЕ: эти ошибки уже были, прошлое исправление не помогло — измени подход "
                       "(например, не ссылайся на недоступный/приватный код, передай значение через конструктор):\n"
                       + "\n".join(f"- {f}: {m}" for f, m in sorted(repeated, key=str)))
        text = _ask(llm, system, prompt, usage, f"repair:{attempts}")
        apply(text, f"repair:{attempts}")

    # --- 4. результат
    shots_out = output_dir / "screenshots"
    shutil.rmtree(shots_out, ignore_errors=True)
    if (project / "screenshots").is_dir():
        shutil.copytree(project / "screenshots", shots_out)
    result.update({"tokens": usage.tokens, "attempts": attempts, "llmCalls": usage.calls,
                   "durationSec": round(time.monotonic() - t_start, 1), "notes": notes,
                   "project": project.relative_to(output_dir).as_posix()})
    (output_dir / "gate_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "agent_log.json").write_text(json.dumps({"steps": usage.steps, "notes": notes},
                                                          ensure_ascii=False, indent=2), encoding="utf-8")
    return result
