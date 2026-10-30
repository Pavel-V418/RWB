"""Съёмка экранов: каждый экран × состояние × вьюпорт + DOM-проверки (уровень 1, без LLM).

Состояния форсируются перехватом запросов к API — бэкенд не нужен для empty/error/forbidden/loading:
  ready      — запросы идут как есть (mock/реальный backend)
  empty      — GET /api/** → {items: [], total: 0}; объекты → 404
  error      — /api/** → 500 (RFC 7807)
  forbidden  — /api/** → 403
  loading    — ответ не приходит (снимок через 800 мс)
Альтернативные сценарии форм создания (ТЗ: «основные и альтернативные сценарии», «понятная обратная связь»):
  invalid       — пустая отправка: у обязательных полей видны понятные ошибки, запрос не уходит
  submit-error  — поля заполнены валидно, сервер отвечает 500: форма показывает сообщение об ошибке сохранения
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

VIEWPORTS = {"desktop": {"width": 1440, "height": 900}, "mobile": {"width": 390, "height": 844}}
STATES = ("ready", "empty", "error", "forbidden", "loading")
FORM_STATES = ("invalid", "submit-error")
# Доступность (axe-core, WCAG 2 A/AA) — на кадрах с данными и пустых; правила темы и разметки, а не данных
A11Y_STATES = ("ready", "empty", "invalid")
A11Y_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]


def find_axe(*roots: Path) -> Path | None:
    """axe.min.js: SPECFORGE_AXE_JS или node_modules/axe-core сгенерированного проекта (devDependency каркаса)."""
    import os
    env = os.environ.get("SPECFORGE_AXE_JS")
    if env and Path(env).exists():
        return Path(env)
    for r in roots:
        for cand in (r / "node_modules" / "axe-core" / "axe.min.js", r / "admin" / "node_modules" / "axe-core" / "axe.min.js"):
            if cand.exists():
                return cand
    return None


def run_axe(page, axe_src: str) -> list[dict]:
    """axe через протокол браузера (page.evaluate): CSP сайта (script-src 'self') не мешает, как мешал бы <script>."""
    if not page.evaluate("typeof window.axe !== 'undefined'"):
        page.evaluate(axe_src)
    res = page.evaluate("""async (tags) => {
      const r = await axe.run(document, { runOnly: { type: 'tag', values: tags }, resultTypes: ['violations'] });
      return r.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help,
        nodes: v.nodes.length, target: (v.nodes[0] && v.nodes[0].target || []).join(' ') }));
    }""", A11Y_TAGS)
    return res
IGNORED_CONSOLE = re.compile(r"tile|maplibre|favicon|ERR_NAME_NOT_RESOLVED|ERR_INTERNET_DISCONNECTED|net::", re.I)
# Realtime-хаб в ревью не поднят (mock-API/спа-сервер): ошибки подключения SignalR — свойство стенда, не дефект экрана.
# Экран показывает «Нет связи» и работает на REST — это проверяют DOM-проверки.
IGNORED_REALTIME = re.compile(r"negotiat|Failed to start the connection|WebSocket|/hubs/|signalr", re.I)
# Ответ, который ревьюер сам подставил для форсирования состояния, браузер пишет в консоль — это не ошибка экрана.
FORCED_STATUS = {"error": "500", "forbidden": "403", "empty": "404", "submit-error": "500"}


def screen_states(screen: dict, states=STATES) -> tuple[str, ...]:
    """Какие состояния снимаются. Экраны без загрузки данных (вход, «нет доступа») не меняются от ответов API.
    Форма создания не загружает данные, поэтому вместо empty/error/forbidden/loading у неё проверяются
    альтернативные сценарии отправки: пустая отправка и ошибка сервера при сохранении."""
    arch = screen.get("archetype")
    create_form = arch == "form" and not (screen.get("form") or {}).get("load")
    if create_form:
        return ("ready",) + (FORM_STATES if states == STATES else tuple(x for x in states if x in FORM_STATES))
    no_data = arch in ("login", "forbidden") or (arch == "generic" and not screen.get("data"))
    return ("ready",) if no_data else tuple(x for x in states if x not in FORM_STATES)


def console_error_ignored(text: str, url: str, state: str) -> bool:
    if IGNORED_CONSOLE.search(text) or IGNORED_REALTIME.search(text) or IGNORED_REALTIME.search(url or ""):
        return True
    code = FORCED_STATUS.get(state)
    return bool(code and "Failed to load resource" in text and f"status of {code}" in text)

DOM_CHECKS_JS = r"""
(args) => {
  const { screen, state } = args;
  const vw = window.innerWidth;
  const res = { overflowX: document.documentElement.scrollWidth > vw + 1, offscreen: [], unlabeled: [], noAlt: 0,
                screenFound: !!document.querySelector(`[data-testid="${screen}"]`), stateFound: true, rawElements: [] };
  const visible = (el) => !!el && el.getBoundingClientRect().height > 0;
  if (args.stateSelector) res.stateFound = [...document.querySelectorAll(args.stateSelector)].some(visible);
  else if (state !== 'ready') res.stateFound = !!document.querySelector(`[data-testid$="-state-${state}"]`);
  else res.stateFound = !document.querySelector(`[data-testid="${screen}-state-error"]`);
  const sel = (el) => el.getAttribute('data-testid') ? `[data-testid="${el.getAttribute('data-testid')}"]` :
    el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '');
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || cs.position === 'fixed') continue;
    if (r.right > vw + 2 && !el.closest('.p-datatable-table-container, .p-datatable-wrapper, .maplibregl-map, [style*="overflow"]')) {
      if (res.offscreen.length < 5) res.offscreen.push(sel(el));
    }
  }
  for (const b of document.querySelectorAll('button, a[role="button"], [role="button"]')) {
    const name = (b.getAttribute('aria-label') || b.textContent || b.getAttribute('title') || '').trim();
    if (!name && res.unlabeled.length < 5) res.unlabeled.push(sel(b));
  }
  res.noAlt = [...document.querySelectorAll('img')].filter((i) => !i.hasAttribute('alt')).length;
  return res;
}
"""


@dataclass
class Shot:
    screen: str
    state: str
    viewport: str
    path: str
    url: str
    dom: dict = field(default_factory=dict)
    console: list[str] = field(default_factory=list)


def screen_targets(spec: dict) -> list[dict]:
    """Экраны без параметров маршрута + один экран на каждый детальный маршрут не снимаем (нужен id)."""
    return [s for s in spec["screens"] if ":" not in s["route"]]


def _session_script(roles: list[str]) -> str:
    payload = {"token": "vlm-review", "expiresAt": 4102444800000, "user": {"id": "vlm", "name": "Ревьюер UI", "roles": roles}}
    return f"try {{ sessionStorage.setItem('sf.session', {json.dumps(json.dumps(payload, ensure_ascii=False))}); }} catch (e) {{}}"


def capture(base_url: str, spec: dict, out_dir: Path, states=STATES, viewports=("desktop", "mobile"), api_prefix: str = "/api/",
            settle_ms: int = 700, log=print, openapi: dict | None = None, axe_js: Path | None = None) -> list[Shot]:
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    roles = sorted({r for s in spec["screens"] for r in s["roles"]})
    axe_src = axe_js.read_text(encoding="utf-8") if axe_js else None
    if not axe_src:
        log("[vlm] axe-core не найден (npm install в проекте или SPECFORGE_AXE_JS) — проверка доступности пропущена")
    shots: list[Shot] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--use-gl=swiftshader", "--enable-webgl", "--ignore-gpu-blocklist"])
        try:
            for vp in viewports:
                for s in screen_targets(spec):
                    for st in screen_states(s, states):
                        ctx = browser.new_context(viewport=VIEWPORTS[vp], locale="ru-RU", device_scale_factor=1)
                        if not s.get("public"):
                            ctx.add_init_script(_session_script(roles))
                        held = _route_state(ctx, st, api_prefix)
                        page = ctx.new_page()
                        console: list[str] = []
                        page.on("pageerror", lambda e, c=console: c.append(f"pageerror: {e}"))
                        page.on("console", lambda m, c=console, st=st: m.type == "error"
                                and not console_error_ignored(m.text, (m.location or {}).get("url", ""), st) and c.append(m.text[:300]))
                        url = base_url.rstrip("/") + s["route"]
                        try:
                            page.goto(url, wait_until="domcontentloaded", timeout=20000)
                            try:
                                page.wait_for_selector(f'[data-testid="{s["id"]}"], [data-testid^="{s["id"]}-state-"]', timeout=8000)
                            except Exception:  # noqa: BLE001
                                pass
                            if st != "loading":
                                try:
                                    page.wait_for_load_state("networkidle", timeout=5000)
                                except Exception:  # noqa: BLE001
                                    pass
                            page.wait_for_timeout(settle_ms if st != "loading" else 800)
                            selector = _act_form(page, s, st, openapi)
                            dom = page.evaluate(DOM_CHECKS_JS, {"screen": s["id"], "state": st, "stateSelector": selector})
                            if axe_src and st in A11Y_STATES:
                                try:
                                    dom["a11y"] = run_axe(page, axe_src)
                                except Exception as e:  # noqa: BLE001 — сбой проверки не должен ронять ревью
                                    dom["a11yError"] = str(e)[:200]
                            path = out_dir / f"{s['id']}--{st}--{vp}.png"
                            page.screenshot(path=str(path), full_page=vp == "desktop")
                        except Exception as e:  # noqa: BLE001
                            dom, path = {"error": str(e)[:300], "screenFound": False, "stateFound": False}, out_dir / "missing.png"
                        shots.append(Shot(s["id"], st, vp, str(path), url, dom, console))
                        for r in held:  # «загрузка»: отпускаем придержанные запросы до закрытия контекста
                            try:
                                r.abort()
                            except Exception:  # noqa: BLE001
                                pass
                        ctx.close()
                log(f"[vlm] снято {vp}: {len([x for x in shots if x.viewport == vp])} кадров")
        finally:
            browser.close()
    (out_dir / "shots.json").write_text(json.dumps([asdict(x) for x in shots], ensure_ascii=False, indent=2), encoding="utf-8")
    return shots


def _route_state(ctx, state: str, prefix: str) -> list:
    held: list = []
    if state in ("ready", "invalid"):
        return held

    def handler(route):
        req = route.request
        if prefix not in req.url or "/auth/" in req.url:
            return route.continue_()
        if state == "submit-error":  # данные формы грузятся как есть, падает только отправка
            if req.method == "GET":
                return route.continue_()
            return route.fulfill(status=500, content_type="application/problem+json",
                                 body=json.dumps({"title": "Ошибка сервера", "status": 500, "traceId": "vlm-review"}))
        if state == "loading":
            held.append(route)  # не отвечаем — экран остаётся в состоянии загрузки
            return None
        if state == "empty":
            if req.method == "GET" and not re.search(r"/[0-9a-f-]{8,}(/|$|\?)", req.url.split(prefix, 1)[1] or ""):
                return route.fulfill(status=200, content_type="application/json", body='{"items":[],"total":0,"page":1,"size":20}')
            return route.fulfill(status=404, content_type="application/problem+json", body='{"title":"Not found","status":404}')
        code = 500 if state == "error" else 403
        return route.fulfill(status=code, content_type="application/problem+json",
                             body=json.dumps({"title": "Ошибка сервера" if code == 500 else "Forbidden", "status": code, "traceId": "vlm-review"}))

    ctx.route("**/*", handler)
    return held


def _example(openapi: dict | None, screen: dict, field: str):
    """Пример значения поля из схемы тела операции отправки (examples/example)."""
    if not openapi:
        return None
    from specforge_web.generators.angular.openapi_ts import TsGen
    gen = TsGen(openapi)
    op = gen.ops.get(((screen.get("form") or {}).get("submit") or {}).get("operationId", ""))
    sch = gen.resolve(gen.schemas.get(op.body_type or "") or {}) if op else {}
    prop = gen.resolve((sch.get("properties") or {}).get(field) or {})
    ex = prop.get("examples") or ([prop["example"]] if "example" in prop else [])
    return ex[0] if ex else None


def _fill(page, sid: str, plan: dict) -> None:
    el = page.locator(f'[data-testid="{sid}-field-{plan["name"]}"]')
    kind = plan["kind"]
    if kind in ("text", "textarea"):
        value = str(plan.get("value") or "Проверка UI")
        if plan.get("marker"):
            value = (value + "-" * plan.get("minLength", 0))[: max(plan.get("minLength", 0), min(len(value), plan.get("maxLength", 200)))]
        el.fill(value)
    elif kind in ("password", "number"):
        el.locator("input").first.fill(str(plan.get("value", "")))
    elif kind in ("date", "datetime"):
        inp = el.locator("input").first
        inp.fill(str(plan.get("value", "")))
        inp.press("Escape")
    elif kind in ("select", "multiselect"):
        el.click()
        page.get_by_role("option").first.click(timeout=3000)
        if kind == "multiselect":
            page.keyboard.press("Escape")


def _act_form(page, screen: dict, state: str, openapi: dict | None) -> str | None:
    """Сценарий формы для состояний invalid / submit-error. → CSS-селектор того, что должно появиться."""
    if state not in FORM_STATES or screen.get("archetype") != "form":
        return None
    from specforge_web.generators.angular.samples import field_plan
    sid = screen["id"]
    submit = page.locator(f'[data-testid="{sid}-submit"]')
    if state == "invalid":
        submit.click()
        page.wait_for_timeout(300)
        return f'[data-testid^="{sid}-error-"]'
    for f in (screen.get("form") or {}).get("fields", []):
        if not f.get("required"):
            continue
        plan = field_plan(f, _example(openapi, screen, f["name"]))
        if plan.get("skip"):
            continue
        try:
            _fill(page, sid, plan)
        except Exception:  # noqa: BLE001 — не заполнилось: ревьюер покажет, что сообщения нет, это тоже сигнал
            pass
    submit.click()
    try:
        page.wait_for_selector(f'[data-testid="{sid}-form-error"]', timeout=4000)
    except Exception:  # noqa: BLE001
        pass
    return f'[data-testid="{sid}-form-error"]'
