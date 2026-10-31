"""
ReplayLLM — «модель», которая отвечает эталонным кодом из каркаса вместо генерации.
Нужна, чтобы проверить механику агента (подготовка проекта, разбор ответов, шлюз, отчёт)
с настоящим Gradle, но без LLM и без токенов:

    python -m specforge_mobile.android_agent.run --in reference/case1 --out build/agent_smoke --replay
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .context import FEATURE_MAIN, FEATURE_TEST
from .llm import LLMReply


class ReplayLLM:
    def __init__(self, template: Path, screens: list[str]):
        self.t, self.screens = template, screens
        main = sorted(p.relative_to(template).as_posix() for p in (template / FEATURE_MAIN).rglob("*.kt"))
        tests = sorted(p.relative_to(template).as_posix() for p in (template / FEATURE_TEST).rglob("*.kt"))
        root_main = lambda r: r.startswith(FEATURE_MAIN) and r.count("/") == FEATURE_MAIN.count("/") + 1
        is_data = lambda r: (root_main(r) and not r.endswith("AppRoot.kt")) or "/wiring/" in r \
            or re.search(r"(Models|Repository)\.kt$", r) or Path(r).name.startswith("Fake")
        self.data = [r for r in main + tests if is_data(r)]
        self.by_screen = {}
        for sid in screens:
            cap = sid[0].upper() + sid[1:]
            self.by_screen[sid] = [r for r in main if f"/feature/{sid}/" in r and r not in self.data] + \
                                  [r for r in tests if Path(r).name.startswith(cap) and r not in self.data]
        used = set(self.data) | {r for v in self.by_screen.values() for r in v}
        strings = sorted(p.relative_to(template).as_posix()
                         for p in (template / "app/src/main/res/values").glob("strings*.xml"))
        self.wiring = [r for r in main + tests if r not in used] + strings

    def _files(self, rels: list[str]) -> str:
        return "\n\n".join(f"<<<FILE {r}>>>\n{(self.t / r).read_text(encoding='utf-8')}\n<<<END>>>" for r in rels)

    def chat(self, messages: list[dict], **_) -> LLMReply:
        user = messages[-1]["content"]
        if user.startswith("Составь план"):
            text = json.dumps({"startScreen": self.screens[0],
                               "screens": [{"screenId": s, "package": s} for s in self.screens]})
        elif user.startswith("Шаг 1"):
            text = self._files(self.data)
        elif m := re.match(r"Шаг 2: экран «(.+?)»", user):
            text = self._files(self.by_screen.get(m[1], []))
        elif user.startswith("Шаг 3"):
            text = self._files(self.wiring)
        else:  # ремонт: эталон уже верный — возвращаем всё заново
            text = self._files(self.data + [r for v in self.by_screen.values() for r in v] + self.wiring)
        return LLMReply(text, len(user) // 4, len(text) // 4)  # оценка токенов для отчёта
