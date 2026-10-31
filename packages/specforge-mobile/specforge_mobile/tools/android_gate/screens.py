#!/usr/bin/env python3
"""
Скриншот-тесты из UX-спеки — детерминированно, без LLM.

Соглашение с агентом: для каждого экрана в основном коде feature/ есть
    @Composable fun <Pascal>ScreenSample(state: String)
которая рисует экран в состоянии state ("ready", "loading", ...) на примерных данных.
Генератор пишет app/src/test/kotlin/com/specforge/app/generated/ScreenshotTest.kt — по тесту
на каждую пару «экран × состояние» из спеки → screenshots/<screenId>--<state>--mobile.png.

Тесты агент не пишет и не чинит: если снимка нет, виноват экран или его Sample, и ошибка
указывает на Kotlin-файл экрана (ремонт чинит основной код).

    python screens.py <android_project> --spec <screen-spec.json> generate   # записать тест
    python screens.py <android_project> --spec <screen-spec.json> check      # до сборки: Sample и состояния
    python screens.py <android_project> --spec <screen-spec.json> verify     # после сборки: все PNG на месте

Ошибки печатаются как `file:line:column: error: сообщение`, код выхода 1.
Только стандартная библиотека Python 3.10+.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_STATES = ["ready", "loading", "empty", "error", "forbidden"]
FEATURE_MAIN = "app/src/main/kotlin/com/specforge/app/feature"
GENERATED_TEST = "app/src/test/kotlin/com/specforge/app/generated/ScreenshotTest.kt"
FEATURE_PKG = "com.specforge.app.feature"


# ---------------------------------------------------------------- спека
def load_spec(path: Path) -> tuple[dict | list, list[dict]]:
    """(сырой JSON, [{screenId, states, title, requirements}]) — терпимо к вариантам формата."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("screens", raw) if isinstance(raw, dict) else raw
    if isinstance(items, dict):  # {"login": {...}}
        items = [{"screenId": k, **v} for k, v in items.items()]
    screens = []
    for s in items:
        sid = s.get("screenId") or s.get("id") or s.get("name")
        if not sid:
            continue
        states = s.get("states") or DEFAULT_STATES
        states = [st if isinstance(st, str) else (st.get("state") or st.get("name") or st.get("id")) for st in states]
        reqs = s.get("requirements") or s.get("reqs") or []
        screens.append({"screenId": sid, "states": [st for st in states if st],
                        "title": s.get("title") or s.get("name") or sid,
                        "requirements": [reqs] if isinstance(reqs, str) else list(reqs)})
    if not screens:
        raise ValueError(f"В {path} нет экранов (ожидаются поля screenId и states)")
    return raw, screens


def pascal(screen_id: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[^A-Za-z0-9]+", screen_id) if p)


def sample_name(screen_id: str) -> str:
    return f"{pascal(screen_id)}ScreenSample"


def guess_package(screen_id: str) -> str:
    return re.sub(r"[^a-z0-9]", "", screen_id.lower()) or "screen"


# ---------------------------------------------------------------- поиск в коде
def _kotlin_files(project: Path) -> list[Path]:
    base = project / FEATURE_MAIN
    return sorted(base.rglob("*.kt")) if base.is_dir() else []


def find_function(project: Path, name: str) -> dict | None:
    """{file, line, package, body} первой top-level функции name в feature/."""
    decl = re.compile(rf"^(?:@\w+(?:\([^)]*\))?\s+)*(?:internal\s+|public\s+)?fun\s+{re.escape(name)}\s*\(", re.M)
    for f in _kotlin_files(project):
        text = f.read_text(encoding="utf-8", errors="replace")
        m = decl.search(text)
        if not m:
            continue
        pkg = re.search(r"^package\s+([\w.]+)", text, re.M)
        start = text.rfind("\n", 0, m.start()) + 1
        nxt = re.compile(r"^(?:@Composable\s*\n)?(?:private\s+|internal\s+)?(?:fun|class|object|val|data)\b", re.M)
        n = nxt.search(text, m.end())
        fun_pos = m.start() + m.group(0).rfind("fun")   # строка самой fun, а не аннотации над ней
        return {"file": f.relative_to(project).as_posix(), "line": text.count("\n", 0, fun_pos) + 1,
                "package": pkg[1] if pkg else "", "body": text[start:n.start() if n else len(text)]}
    return None


def screen_location(project: Path, sid: str) -> tuple[str, int]:
    """Где чинить экран: файл с <Pascal>Screen или ожидаемый путь в feature/<пакет>/."""
    for name in (sample_name(sid), f"{pascal(sid)}Screen", f"{pascal(sid)}Route"):
        hit = find_function(project, name)
        if hit:
            return hit["file"], hit["line"]
    return f"{FEATURE_MAIN}/{guess_package(sid)}/{pascal(sid)}Screen.kt", 1


# ---------------------------------------------------------------- генерация
def generate(project: Path, screens: list[dict]) -> Path:
    imports, tests, has_req = set(), [], False
    for s in screens:
        sid, name = s["screenId"], sample_name(s["screenId"])
        hit = find_function(project, name)
        pkg = hit["package"] if hit else f"{FEATURE_PKG}.{guess_package(sid)}"
        imports.add(f"import {pkg}.{name}")
        reqs = s.get("requirements") or []
        for st in s["states"]:
            fn = re.sub(r"\W", "_", f"{sid}_{st}")
            if reqs:
                has_req = True
                ann = "@Req(" + ", ".join(json.dumps(r) for r in reqs) + ") "
                fn = f"`{' '.join(reqs)} {sid} {st}`"
            else:
                ann = ""
            tests.append(f'    {ann}@Test fun {fn}() = shot("{sid}", "{st}") {{ {name}("{st}") }}')
    if has_req:
        imports.add("import com.specforge.app.Req")
    body = f"""// СГЕНЕРИРОВАНО из UX-спеки (tools/android_gate/screens.py) — не править, перезаписывается.
package com.specforge.app.generated

import androidx.compose.runtime.Composable
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onRoot
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.github.takahirom.roborazzi.captureRoboImage
import com.specforge.app.core.ui.theme.AppTheme
{chr(10).join(sorted(imports))}
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File

/** Снимки для VLM-ревьюера: screenshots/<screenId>--<state>--mobile.png (задача recordRoborazziDebug). */
@RunWith(AndroidJUnit4::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], qualifiers = "w411dp-h891dp-xxhdpi")
class ScreenshotTest {{
    @get:Rule val compose = createComposeRule()

    private val dir = File(System.getProperty("specforge.screenshotDir") ?: "build/screenshots")

    private fun shot(screenId: String, state: String, content: @Composable () -> Unit) {{
        compose.setContent {{ AppTheme(dark = false) {{ content() }} }}
        compose.onRoot().captureRoboImage(File(dir, "$screenId--$state--mobile.png").path)
    }}

{chr(10).join(tests)}
}}
"""
    out = project / GENERATED_TEST
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    return out


# ---------------------------------------------------------------- проверки
def _diag(file: str, line: int, message: str) -> dict:
    return {"tool": "screens", "file": file, "line": line, "column": 1, "severity": "error",
            "category": "generic", "message": message}


def check(project: Path, screens: list[dict]) -> list[dict]:
    """До сборки: у каждого экрана есть Sample и он обрабатывает все состояния из спеки."""
    errors = []
    for s in screens:
        sid, name = s["screenId"], sample_name(s["screenId"])
        hit = find_function(project, name)
        if not hit:
            file, line = screen_location(project, sid)
            errors.append(_diag(file, line,
                                f"Нет @Composable fun {name}(state: String) для экрана «{sid}» из UX-спеки. "
                                f"Добавь её рядом с экраном: when(state) {{ {', '.join(repr(st) for st in s['states'])} }} "
                                f"→ {pascal(sid)}Screen(...) с примерными данными."))
            continue
        if not re.search(rf"fun\s+{name}\s*\(\s*state\s*:\s*String\s*\)", hit["body"]):
            errors.append(_diag(hit["file"], hit["line"], f"Сигнатура должна быть fun {name}(state: String)."))
        missing = [st for st in s["states"] if f'"{st}"' not in hit["body"]]
        if missing:
            errors.append(_diag(hit["file"], hit["line"],
                                f"{name} не обрабатывает состояния из UX-спеки: {', '.join(missing)}. "
                                f"Добавь ветки when для них."))
    return errors


def verify(project: Path, screens: list[dict]) -> list[dict]:
    """После сборки: есть PNG на каждую пару экран × состояние."""
    shots = project / "screenshots"
    have = {p.name for p in shots.glob("*.png")} if shots.is_dir() else set()
    errors = []
    for s in screens:
        missing = [st for st in s["states"] if f"{s['screenId']}--{st}--mobile.png" not in have]
        if missing:
            file, line = screen_location(project, s["screenId"])
            errors.append(_diag(file, line, f"Нет снимков экрана «{s['screenId']}» для состояний: "
                                            f"{', '.join(missing)} — экран или {sample_name(s['screenId'])} "
                                            f"падает при отрисовке (см. упавший тест ScreenshotTest)."))
    return errors


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", type=Path)
    ap.add_argument("--spec", type=Path, required=True)
    ap.add_argument("command", choices=["generate", "check", "verify"])
    args = ap.parse_args()
    _, screens = load_spec(args.spec)
    if args.command == "generate":
        out = generate(args.project, screens)
        n = sum(len(s["states"]) for s in screens)
        print(f"screens: {out.relative_to(args.project).as_posix()} — тестов {n}")
        errors = []
    else:
        errors = check(args.project, screens) if args.command == "check" else verify(args.project, screens)
    for e in errors:
        print(f"{e['file']}:{e['line']}:{e['column']}: error: {e['message']}")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
