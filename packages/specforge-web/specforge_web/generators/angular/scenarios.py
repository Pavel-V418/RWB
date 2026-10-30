"""E2E-сценарии пользователя из спеки экранов (ТЗ, этап 3: «проверять фактическое выполнение пользовательского
сценария: действия обращаются к работающему backend, данные сохраняются, ошибки и ограничения доступа
обрабатываются»).

Для каждого кейса генерируются (детерминированно, без LLM):
  • форма создания: заполнение валидными значениями → запрос к API 2xx → уведомление об успехе →
    созданная запись находится в списке по уникальной метке прогона (данные сохранились);
  • форма создания: пустая отправка → ошибки обязательных полей, запрос не уходит;
    неверный формат поля с pattern → понятная ошибка поля;
  • действие на карточке (назначить, отменить, …): запрос уходит в API, сервер не отвечает 5xx,
    пользователь видит обратную связь (успех или понятная ошибка, если действие недопустимо для записи);
  • ограничение доступа: пользователь с ролью без доступа видит «нет доступа».
Запуск против настоящего стенда: BASE_URL + E2E_USERS (см. e2e/support.ts).

Трассируемость: в названия тестов добавляются теги требований (@FR-01), а scenario_trace() отдаёт
FR → [{case, kind}] (kind: main — основной сценарий, alternative — ошибка ввода, access — запрет по роли);
generate.py пишет это в e2e/scenarios.trace.json, tools/ui_docs.py — в матрицу трассируемости.
"""
from __future__ import annotations

import json
import re

from .openapi_ts import TsGen
from .samples import field_plan

MAX_GUARDS = 6


def _path_regex(path: str) -> str:
    return re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path))


def _example(gen: TsGen, body_type: str | None, field: str):
    sch = gen.resolve(gen.schemas.get(body_type or "") or {})
    prop = gen.resolve((sch.get("properties") or {}).get(field) or {})
    ex = prop.get("examples") or ([prop["example"]] if "example" in prop else [])
    return ex[0] if ex else None


def _reqs(screen: dict) -> list[str]:
    """Требования экрана и его слотов, без повторов, в порядке появления."""
    out: list[str] = []
    for r in [*(screen.get("requirements") or []), *(r for sl in screen.get("slots") or [] for r in sl.get("requirements") or [])]:
        if r not in out:
            out.append(r)
    return out


def build(spec: dict, gen: TsGen) -> dict:
    screens = {s["id"]: s for s in spec["screens"]}
    all_roles = sorted({r for s in spec["screens"] if not s.get("public") for r in s["roles"]})
    forms, actions, guards, skipped = [], [], [], []

    for s in spec["screens"]:
        if s.get("public") or s["archetype"] != "form":
            continue
        form = s["form"]
        if ":" in s["route"] or form.get("load"):
            skipped.append(f"{s['id']}: форма с параметрами маршрута — проверяется через карточку")
            continue
        sub = form["submit"]
        op = gen.ops.get(sub["operationId"])
        if not op:
            continue
        fields = [field_plan(f, _example(gen, op.body_type, f["name"])) for f in form["fields"]]
        target = screens.get(sub.get("navigateTo") or "")
        lst = None
        if target and target["archetype"] == "list":
            visible = {c["field"] for c in target["list"]["columns"] if not c.get("hideBelow")}
            check = next((f["name"] for f in fields if f.get("marker") and f["name"] in visible), None)
            if check:
                lst = {"id": target["id"], "route": target["route"], "search": bool(target["list"].get("search")), "field": check}
        pattern_fields = [f["name"] for f in form["fields"] if (f.get("validation") or {}).get("pattern") and f.get("control") in ("text", "textarea")]
        forms.append({"id": s["id"], "route": s["route"], "roles": s["roles"], "method": op.method.upper(), "path": _path_regex(op.path),
                      "success": sub.get("successMessage") or "", "fields": fields, "list": lst, "patternFields": pattern_fields,
                      "requirements": _reqs(s)})

    for s in spec["screens"]:
        if s["archetype"] != "detail":
            continue
        acts = []
        # если хоть одно действие экрана закрепило за собой требования (action.requirements), остальные действия
        # ни к какому FR не привязываем (тест остаётся, но без тега) — иначе «Завершить» попало бы в FR про список;
        # если не закрепило ни одно — у действий требования экрана
        claimed = any(a.get("requirements") for a in s.get("actions", []) or [])
        rest = [] if claimed else _reqs(s)
        for a in s.get("actions", []) or []:
            op = gen.ops.get(a.get("operationId") or "")
            if not op or a.get("scope", "screen") != "screen":
                continue
            acts.append({"id": a["id"], "label": a["label"], "method": op.method.upper(), "path": _path_regex(op.path),
                         "confirm": bool(a.get("confirm")), "roles": a.get("roles") or s["roles"],
                         "requirements": list(a.get("requirements") or rest)})
        if not acts:
            continue
        owner = next((x for x in spec["screens"] if x["archetype"] == "list" and (x.get("list") or {}).get("rowLink") == s["id"]
                      and ":" not in x["route"]), None)
        if not owner:
            skipped.append(f"{s['id']}: нет списка, ведущего в карточку, — действия не проверяются")
            continue
        actions.append({"detail": s["id"], "list": {"id": owner["id"], "route": owner["route"]}, "roles": s["roles"], "actions": acts})

    for s in spec["screens"]:
        if s.get("public") or ":" in s["route"] or s["archetype"] == "form":
            continue
        denied = [r for r in all_roles if r not in s["roles"]]
        if denied and len(guards) < MAX_GUARDS:
            guards.append({"id": s["id"], "route": s["route"], "denied": denied[0], "requirements": _reqs(s)})

    login = gen.ops.get("login")
    return {"api": {"apiBase": "/api/v1", "loginPath": login.path if login else None},
            "forms": forms, "actions": actions, "guards": guards, "skipped": skipped}


# Названия тестов — те же строки, что в TS-шаблоне ниже (без тегов): по ним матрица ссылается на тест.
def _form_cases(f: dict) -> list[tuple[str, str, str | None]]:
    blocked = next((x for x in f["fields"] if x.get("required") and x.get("skip")), None)
    out = [(f"форма {f['id']} › создание: запрос к API, уведомление, запись сохранилась", "main",
            blocked["skip"] if blocked else None)]
    if any(x.get("required") for x in f["fields"]):
        out.append((f"форма {f['id']} › пустая отправка: ошибки обязательных полей, запрос не уходит", "alternative", None))
    out += [(f"форма {f['id']} › неверный формат поля {n}: понятная ошибка", "alternative", None) for n in f["patternFields"]]
    return out


def scenario_trace(cfg: dict) -> dict[str, list[dict]]:
    """FR → [{case, kind, screen[, skip]}] по сгенерированным сценариям (kind: main | alternative | access)."""
    trace: dict[str, list[dict]] = {}

    def add(reqs, case, kind, screen, skip=None):
        for r in reqs or []:
            item = {"case": case, "kind": kind, "screen": screen}
            if skip:
                item["skip"] = skip  # тест есть, но заранее известно, что он будет пропущен
            if item not in trace.setdefault(r, []):
                trace[r].append(item)

    for f in cfg["forms"]:
        for case, kind, skip in _form_cases(f):
            add(f.get("requirements"), case, kind, f["id"], skip)
    for d in cfg["actions"]:
        for a in d["actions"]:
            add(a.get("requirements"), f"{d['detail']}: «{a['label']}» обращается к API и даёт обратную связь", "main", d["detail"])
    for g in cfg["guards"]:
        add(g.get("requirements"), f"{g['id']}: роль {g['denied']} видит «нет доступа»", "access", g["id"])
    return dict(sorted(trace.items()))


def scenarios_spec(spec: dict, gen: TsGen, cfg: dict | None = None) -> str:
    cfg = cfg or build(spec, gen)
    j = lambda x: json.dumps(x, ensure_ascii=False, indent=2)  # noqa: E731
    skipped = "\n".join(f"//   - {x}" for x in cfg["skipped"]) or "//   —"
    return f"""// AUTO-GENERATED из спеки экранов: пользовательские сценарии против работающего backend.
// Запуск: BASE_URL=<стенд> E2E_USERS='{{"<роль>": {{"login": "...", "password": "..."}}}}' npx playwright test e2e/scenarios.spec.ts
// Только сценарии одного требования: npx playwright test e2e/scenarios.spec.ts --grep @FR-01
// Связь «требование → сценарий» для матрицы трассируемости: e2e/scenarios.trace.json
// Не покрыто автоматически (причины):
{skipped}
import {{ expect, test }} from '@playwright/test';

import {{ ApiCfg, FieldPlan, apiPath, fillField, runMarker, signIn }} from './support';

const API: ApiCfg = {j(cfg['api'])};

interface FormCase {{
  id: string; route: string; roles: string[]; method: string; path: string; success: string;
  fields: FieldPlan[]; list: {{ id: string; route: string; search: boolean; field: string }} | null; patternFields: string[];
  requirements: string[];
}}
interface ActionCase {{
  detail: string; list: {{ id: string; route: string }}; roles: string[];
  actions: {{ id: string; label: string; method: string; path: string; confirm: boolean; roles: string[]; requirements: string[] }}[];
}}

const FORMS: FormCase[] = {j(cfg['forms'])};

const ACTIONS: ActionCase[] = {j(cfg['actions'])};

const GUARDS: {{ id: string; route: string; denied: string; requirements: string[] }}[] = {j(cfg['guards'])};

// Теги требований в названии теста: --grep @FR-01 выбирает сценарии одного требования.
const tags = (reqs: string[]): string => reqs.map((r) => ` @${{r}}`).join('');

for (const f of FORMS) {{
  test.describe(`форма ${{f.id}}${{tags(f.requirements)}}`, () => {{
    test('создание: запрос к API, уведомление, запись сохранилась', async ({{ page, request }}) => {{
      const blocked = f.fields.find((x) => x.required && x.skip);
      test.skip(!!blocked, blocked?.skip ?? '');
      const reason = await signIn(page, request, API, f.roles);
      test.skip(!!reason, reason ?? '');
      await page.goto(f.route);
      await expect(page.getByTestId(`${{f.id}}-form`)).toBeVisible();
      const marker = runMarker();
      const typed: Record<string, string | null> = {{}};
      for (const field of f.fields) {{
        if (!field.skip && (field.required || field.marker)) typed[field.name] = await fillField(page, f.id, field, marker);
      }}
      const response = page.waitForResponse((r) => r.request().method() === f.method && apiPath(API, f.path).test(new URL(r.url()).pathname));
      await page.getByTestId(`${{f.id}}-submit`).click();
      const r = await response;
      expect(r.status(), `ответ API: ${{await r.text()}}`).toBeLessThan(300);
      if (f.success) await expect(page.locator('.p-toast-message').filter({{ hasText: f.success }})).toBeVisible();
      await expect(page.getByTestId(`${{f.id}}-form-error`)).toHaveCount(0);
      if (f.list) {{
        const value = typed[f.list.field];
        await page.goto(f.list.route);
        if (f.list.search && value) {{
          const search = page.getByTestId(`${{f.list.id}}-search`);
          await search.fill(value);
          await search.press('Enter');
        }}
        await expect(page.getByTestId(`${{f.list.id}}-table-row`).filter({{ hasText: value ?? '' }}).first(), 'созданная запись не найдена в списке').toBeVisible();
      }}
    }});

    test('пустая отправка: ошибки обязательных полей, запрос не уходит', async ({{ page, request }}) => {{
      test.skip(!f.fields.some((x) => x.required), 'нет обязательных полей');
      const reason = await signIn(page, request, API, f.roles);
      test.skip(!!reason, reason ?? '');
      let sent = false;
      page.on('request', (rq) => {{
        if (rq.method() === f.method && apiPath(API, f.path).test(new URL(rq.url()).pathname)) sent = true;
      }});
      await page.goto(f.route);
      await page.getByTestId(`${{f.id}}-submit`).click();
      await expect(page.locator(`[data-testid^="${{f.id}}-error-"]`).first()).toBeVisible();
      expect(sent, 'форма с ошибками ушла на сервер').toBe(false);
    }});

    for (const name of f.patternFields) {{
      test(`неверный формат поля ${{name}}: понятная ошибка`, async ({{ page, request }}) => {{
        const reason = await signIn(page, request, API, f.roles);
        test.skip(!!reason, reason ?? '');
        await page.goto(f.route);
        await page.getByTestId(`${{f.id}}-field-${{name}}`).fill('!!!');
        await page.getByTestId(`${{f.id}}-submit`).click();
        await expect(page.getByTestId(`${{f.id}}-error-${{name}}`)).toBeVisible();
      }});
    }}
  }});
}}

for (const d of ACTIONS) {{
  for (const a of d.actions) {{
    test(`${{d.detail}}: «${{a.label}}» обращается к API и даёт обратную связь${{tags(a.requirements)}}`, async ({{ page, request }}) => {{
      const reason = await signIn(page, request, API, a.roles);
      test.skip(!!reason, reason ?? '');
      await page.goto(d.list.route);
      const row = page.getByTestId(`${{d.list.id}}-table-row`).first();
      test.skip(!(await row.isVisible({{ timeout: 10_000 }}).catch(() => false)), 'в списке нет записей для проверки действия');
      await row.click();
      await expect(page.getByTestId(d.detail)).toBeVisible();
      const button = page.getByTestId(`${{d.detail}}-action-${{a.id}}`);
      test.skip(!(await button.isVisible().catch(() => false)), 'действие скрыто для этой записи (условие видимости)');
      const response = page.waitForResponse((r) => r.request().method() === a.method && apiPath(API, a.path).test(new URL(r.url()).pathname));
      await button.click();
      if (a.confirm) await page.getByRole('button', {{ name: 'Да' }}).click();
      const r = await response;
      expect(r.status(), `сервер ответил ошибкой: ${{await r.text()}}`).toBeLessThan(500);
      await expect(page.locator('.p-toast-message').first(), 'нет обратной связи пользователю').toBeVisible();
    }});
  }}
}}

for (const g of GUARDS) {{
  test(`${{g.id}}: роль ${{g.denied}} видит «нет доступа»${{tags(g.requirements)}}`, async ({{ page, request }}) => {{
    const reason = await signIn(page, request, API, [g.denied], true);
    test.skip(!!reason, reason ?? '');
    await page.goto(g.route);
    await expect(page.getByTestId(`${{g.id}}-state-forbidden`)).toBeVisible();
  }});
}}
"""
