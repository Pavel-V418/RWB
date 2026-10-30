"""VLM-ревьюер (шаг 8): снимки экранов → дефекты интерфейса.

  run(input_dir, output_dir, llm, base_url=...) -> GateResult
  python -m agents.vlm_reviewer.review --in artifacts --out artifacts/07_tests/ui --base-url http://localhost:4200
  python -m agents.vlm_reviewer.review --in artifacts --out ... --screenshots DIR --platform android   # готовые снимки (Android)

Уровень 1 (без LLM, на всех кадрах): нет экрана/состояния, горизонтальная прокрутка, элементы за краем,
  кнопки без подписи, ошибки консоли.
Уровень 2 (VLM, только ключевые кадры — экономия токенов): кадр + выдержка из спеки экрана → JSON-дефекты.
Дефекты: {screen, state, viewport, severity: critical|major|minor, element, issue, fix, source: dom|vlm}.
Шлюз пройден, если нет critical и major. defects.json идёт в frontend-агент / генератор (autofix).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

from specforge_web.agents.common.llm import TrackedLLM, Usage, llm_from_env  # noqa: E402
from specforge_web.agents.vlm_reviewer.capture import Shot, capture, find_axe  # noqa: E402
from specforge_web.contracts.agent.agent_contract import GateError, GateResult  # noqa: E402

VLM_STATES = {("ready", "desktop"), ("ready", "mobile"), ("empty", "desktop"), ("submit-error", "desktop")}
A11Y_FIX = {
    "color-contrast": "контраст текста — цвета только из токенов темы (--sf-text, --p-text-muted-color), без своих оттенков",
    "button-name": "у кнопки-иконки задать ariaLabel",
    "label": "у поля формы — label for/inputId или ariaLabel",
    "image-alt": "добавить alt изображению",
    "link-name": "у ссылки — текст или aria-label",
    "aria-required-children": "проверить роли вложенных элементов компонента",
    "aria-valid-attr-value": "проверить значения aria-* атрибутов",
    "landmark-one-main": "основное содержимое — внутри <main>",
}
STATE_DEFECTS = {
    "invalid": ("пустая отправка формы не показывает ошибки обязательных полей",
                "обязательным полям — required в спеке; ошибки выводятся под полем после отправки"),
    "submit-error": ("при ошибке сервера форма не сообщает пользователю, что сохранить не удалось",
                     "задать states.error.title формы; сообщение формы показывается при ответе 4xx/5xx"),
}
MAX_IMAGE_WIDTH = 1280
PARALLEL = 3

DEFECT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["defects"],
    "properties": {
        "defects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["severity", "element", "issue", "fix"],
                "properties": {
                    "severity": {"enum": ["critical", "major", "minor"]},
                    "element": {"type": "string"},
                    "issue": {"type": "string"},
                    "fix": {"type": "string"},
                },
            },
        },
        "summary": {"type": "string"},
    },
}

SYSTEM = """Ты — ревьюер интерфейсов. Тебе показывают скриншот экрана веб-админки (UI Kit PrimeNG) или Android-приложения
(Material 3) и выдержку из его спецификации. Найди реальные дефекты, видимые на скриншоте:
- critical: экран пустой/белый, сообщение об ошибке вместо данных в состоянии «данные», ключевое действие недоступно;
- major: отсутствует элемент из спецификации (колонка, кнопка, блок), съехавшая или перекрывающаяся вёрстка, обрезанный текст,
  нечитаемый контраст, непонятное состояние (пусто/ошибка без пояснения), смешение языков, «сырые» коды вместо подписей;
- minor: неровные отступы, неудачное выравнивание, слишком длинные подписи, мелкие стилистические несоответствия.
Не придумывай дефекты: если экран в порядке — верни пустой список. Не считай дефектом карту без подложки (нейтральный фон,
пометка «Подложка карты недоступна») — на стенде может не быть интернета; дефект — только если не видно самих объектов.
Одинаковые значения в строках — свойство тестовых данных, не дефект интерфейса.
Поле fix — конкретное действие на уровне спецификации экрана или стиля (что изменить), на русском."""


def spec_excerpt(screen: dict) -> str:
    parts = [f"Экран «{screen['title']}» ({screen['archetype']}), маршрут {screen['route']}"]
    if screen.get("list"):
        parts.append("Колонки: " + ", ".join(c["label"] or c["field"] for c in screen["list"]["columns"]))
        if screen["list"].get("filters"):
            parts.append("Фильтры: " + ", ".join(f["label"] for f in screen["list"]["filters"]))
    if screen.get("detail"):
        parts.append("Секции: " + ", ".join(s["title"] for s in screen["detail"]["sections"]))
    if screen.get("form"):
        parts.append("Поля формы: " + ", ".join(f["label"] for f in screen["form"]["fields"]))
    if screen.get("dashboard"):
        parts.append("Виджеты: " + ", ".join(w["title"] for w in screen["dashboard"]["widgets"]))
    if screen.get("map"):
        parts.append("Слои карты: " + ", ".join(l.get("title") or l["id"] for l in screen["map"]["layers"]))
    if screen.get("actions"):
        parts.append("Действия: " + ", ".join(a["label"] for a in screen["actions"]))
    st = screen.get("states", {})
    parts.append(f"Тексты состояний: пусто «{st.get('empty', {}).get('title', '')}», ошибка «{st.get('error', {}).get('title', '')}», нет доступа «{st.get('forbidden', {}).get('title', '')}»")
    return "\n".join(parts)


def image_data_url(path: str) -> str:
    raw = Path(path).read_bytes()
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        if im.width > MAX_IMAGE_WIDTH:
            im = im.resize((MAX_IMAGE_WIDTH, int(im.height * MAX_IMAGE_WIDTH / im.width)))
        if im.height > 2400:  # длинные страницы — верх, где основное содержимое
            im = im.crop((0, 0, im.width, 2400))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=80)
        raw, mime = buf.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def dom_defects(shot: Shot) -> list[dict]:
    d, out = shot.dom, []
    base = {"screen": shot.screen, "state": shot.state, "viewport": shot.viewport, "source": "dom", "image": shot.path}
    if d.get("error"):
        out.append(base | {"severity": "critical", "element": "страница", "issue": f"экран не открылся: {d['error']}", "fix": "проверить маршрут и сборку"})
        return out
    if not d.get("screenFound") and shot.state == "ready":
        out.append(base | {"severity": "critical", "element": f'[data-testid="{shot.screen}"]', "issue": "корневой элемент экрана не найден", "fix": "проверить маршрут и генерацию страницы"})
    if not d.get("stateFound"):
        issue, fix = STATE_DEFECTS.get(shot.state, (f"экран не показывает состояние «{shot.state}»",
                                                     "обернуть данные в <app-state> и связать со статусом источника"))
        out.append(base | {"severity": "major", "element": f"состояние {shot.state}", "issue": issue, "fix": fix})
    if d.get("overflowX"):
        out.append(base | {"severity": "major" if shot.viewport == "mobile" else "minor", "element": ", ".join(d.get("offscreen", [])[:3]) or "страница",
                           "issue": "горизонтальная прокрутка страницы", "fix": "скрыть второстепенные колонки на узких экранах (hideBelow: md)"})
    if d.get("unlabeled"):
        out.append(base | {"severity": "minor", "element": ", ".join(d["unlabeled"][:3]), "issue": "кнопка без доступного имени", "fix": "добавить ariaLabel"})
    if d.get("noAlt"):
        out.append(base | {"severity": "minor", "element": "img", "issue": f"{d['noAlt']} изображений без alt", "fix": "добавить alt"})
    for c in shot.console[:3]:
        out.append(base | {"severity": "major", "element": "консоль", "issue": f"ошибка JS: {c[:200]}", "fix": "исправить ошибку в коде экрана/слота"})
    for v in d.get("a11y", []) or []:
        # critical — элемент недоступен (кнопка без имени, поле без подписи): блокирует шлюз; serious и ниже — замечание
        sev = "major" if v.get("impact") == "critical" else "minor"
        out.append(base | {"severity": sev, "element": v.get("target") or v["id"], "source": "axe",
                           "issue": f"доступность ({v['id']}, {v.get('impact')}): {v.get('help', '')} — элементов: {v.get('nodes', 1)}",
                           "fix": A11Y_FIX.get(v["id"], "исправить по правилу axe-core (WCAG 2 AA)")})
    return out


class VlmReviewer:
    def __init__(self, llm, spec: dict, log=print):
        self.llm = TrackedLLM(llm, Usage()) if llm is not None else None
        self.spec, self.log = spec, log
        self.by_id = {s["id"]: s for s in spec["screens"]}

    def review_image(self, shot: Shot) -> list[dict]:
        screen = self.by_id.get(shot.screen)
        if not screen or not Path(shot.path).exists():
            return []
        content = [
            {"type": "text", "text": f"Платформа: {self.spec['app']['platform']}, вьюпорт: {shot.viewport}, состояние: {shot.state}.\n\n{spec_excerpt(screen)}\n\nВерни дефекты JSON."},
            {"type": "image_url", "image_url": {"url": image_data_url(shot.path)}},
        ]
        try:
            out = self.llm.json([{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}], DEFECT_SCHEMA,
                                tag="vlm.review", thinking=False, max_tokens=1500)
        except Exception as e:  # noqa: BLE001
            self.log(f"[vlm] {shot.screen}/{shot.state}/{shot.viewport}: VLM недоступна ({e})")
            return []
        return [d | {"screen": shot.screen, "state": shot.state, "viewport": shot.viewport, "source": "vlm", "image": shot.path}
                for d in out.get("defects", []) if isinstance(d, dict) and d.get("severity") in ("critical", "major", "minor")]

    def review(self, shots: list[Shot]) -> list[dict]:
        defects = [d for s in shots for d in dom_defects(s)]
        if self.llm:
            key_shots = [s for s in shots if (s.state, s.viewport) in VLM_STATES and not s.dom.get("error")]
            with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
                for ds in pool.map(self.review_image, key_shots):
                    defects += ds
        return defects


def report_md(defects: list[dict], shots: list[Shot]) -> str:
    sev = {"critical": 0, "major": 0, "minor": 0}
    for d in defects:
        sev[d["severity"]] += 1
    lines = ["# Отчёт UI-ревью", "", f"Кадров: {len(shots)} · critical: {sev['critical']} · major: {sev['major']} · minor: {sev['minor']}", "",
             "| Экран | Состояние | Вьюпорт | Важность | Элемент | Проблема | Как исправить | Источник |", "|---|---|---|---|---|---|---|---|"]
    order = {"critical": 0, "major": 1, "minor": 2}
    for d in sorted(defects, key=lambda x: (order[x["severity"]], x["screen"])):
        cell = lambda v: str(v).replace("|", "\\|").replace("\n", " ")[:160]  # noqa: E731
        lines.append(f"| {d['screen']} | {d['state']} | {d['viewport']} | {d['severity']} | {cell(d['element'])} | {cell(d['issue'])} | {cell(d['fix'])} | {d['source']} |")
    return "\n".join(lines) + "\n"


def run(input_dir: Path, output_dir: Path, llm, base_url: str | None = None, screenshots: Path | None = None,
        platform: str = "web", log=print) -> GateResult:
    t0 = time.time()
    spec = json.loads((input_dir / "03_ux" / f"screen-spec.{platform}.json").read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    axe_js = None
    if screenshots:
        # готовые кадры (например, с Android-эмулятора): имя файла <screen>--<state>--<viewport>.png или <screen>.png
        shots = []
        for p in sorted(screenshots.glob("*.png")):
            parts = p.stem.split("--")
            shots.append(Shot(parts[0], parts[1] if len(parts) > 1 else "ready", parts[2] if len(parts) > 2 else "mobile", str(p), "", {"screenFound": True, "stateFound": True}))
    else:
        api_path = input_dir / "02_system" / "openapi.yaml"
        openapi = yaml.safe_load(api_path.read_text(encoding="utf-8")) if api_path.exists() else None
        axe_js = find_axe(input_dir / "05_web", ROOT)
        shots = capture(base_url or "http://localhost:4200", spec, output_dir / "screenshots", log=log, openapi=openapi, axe_js=axe_js)
    reviewer = VlmReviewer(llm, spec, log)
    defects = reviewer.review(shots)
    (output_dir / "defects.json").write_text(json.dumps(defects, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "ui-review.md").write_text(report_md(defects, shots), encoding="utf-8")
    errors = [GateError(message=f"{d['screen']}/{d['state']}/{d['viewport']}: {d['issue']} → {d['fix']}", file=d["screen"],
                        severity="error" if d["severity"] in ("critical", "major") else "warning", code=f"UI_{d['severity'].upper()}_{d['source'].upper()}")
              for d in defects]
    u = reviewer.llm.usage if reviewer.llm else Usage()
    metrics = {"shots": len(shots), "defects": {s: sum(1 for d in defects if d["severity"] == s) for s in ("critical", "major", "minor")},
               "a11y": ("axe-core" if axe_js else "не выполнялась (нет axe-core)") if not screenshots else "—",
               "vlmShots": sum(1 for s in shots if (s.state, s.viewport) in VLM_STATES) if reviewer.llm else 0, "usage": u.__dict__}
    res = GateResult(passed=not any(e.severity == "error" for e in errors), errors=errors, tokens_in=u.tokens_in, tokens_out=u.tokens_out,
                     tokens_cached=u.tokens_cached, llm_calls=u.calls, attempts=1, duration_sec=round(time.time() - t0, 2),
                     artifacts=[str(output_dir / "defects.json"), str(output_dir / "ui-review.md")], metrics=metrics)
    res.write(output_dir)
    log(f"[vlm] {'PASSED' if res.passed else 'FAILED'} · кадров {len(shots)} · дефекты {metrics['defects']}")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SpecForge VLM UI reviewer")
    ap.add_argument("--in", dest="inp", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--base-url")
    ap.add_argument("--screenshots", type=Path)
    ap.add_argument("--platform", default="web")
    ap.add_argument("--offline", action="store_true", help="только DOM-проверки, без VLM")
    a = ap.parse_args(argv)
    res = run(a.inp, a.out, None if a.offline else llm_from_env(), a.base_url, a.screenshots, a.platform)
    return 0 if res.passed else 1


if __name__ == "__main__":
    sys.exit(main())
