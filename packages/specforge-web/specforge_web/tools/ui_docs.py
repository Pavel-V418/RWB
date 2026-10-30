#!/usr/bin/env python3
"""Документация интерфейса из screen-spec (шаг 12): одинаково работает на любом кейсе.

  python tools/ui_docs.py --in artifacts --out artifacts/09_docs/ui [--project artifacts/05_web/admin] [--shots artifacts/07_tests/ui/screenshots]

Выход:
  user-guide.<platform>.md  руководство пользователя по ролям и экранам (со скриншотами из UI-ревью, если есть)
  screens.md                каталог экранов + схема навигации (Mermaid)
  traceability.json / .md   FR → экраны → автотесты (unit — состояния, smoke — экран открывается,
                            scenario — пользовательский сценарий из e2e/scenarios.trace.json) — для общей матрицы
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

ARCH = {"list": "список", "detail": "карточка", "form": "форма", "map": "карта", "timeline": "лента событий", "dashboard": "сводка",
        "login": "вход", "forbidden": "нет доступа", "generic": "особый экран"}
FMT = {"datetime": "дата и время", "date": "дата", "time": "время", "duration": "длительность", "relative-time": "сколько времени прошло",
       "currency": "сумма", "number": "число", "status": "статус", "boolean": "да/нет", "link": "ссылка", "coordinates": "координаты"}


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def rel(path: str, base: Path) -> str:
    try:
        return os.path.relpath(path, base).replace("\\", "/")
    except ValueError:
        return path


def screen_section(s: dict, spec: dict, frs: dict, shots: dict, out: Path) -> list[str]:
    by_id = {x["id"]: x for x in spec["screens"]}
    L = [f"### {s['title']}", "", f"*{ARCH.get(s['archetype'], s['archetype'])} · адрес `{s['route']}` · роли: {', '.join(s['roles'])}*", ""]
    if s.get("description"):
        L += [s["description"], ""]
    reqs = [f"{r} — {frs[r]}" for r in s.get("requirements", []) if r in frs]
    if reqs:
        L += ["Закрывает требования: " + "; ".join(reqs) + ".", ""]
    img = shots.get((s["id"], "ready", "desktop"))
    if img:
        L += [f"![{s['title']}]({rel(img, out)})", ""]
    if s.get("list"):
        lst = s["list"]
        L.append("**Что на экране.** Таблица: " + ", ".join(f"{c['label'] or c['field']}" + (f" ({FMT[c['format']]})" if c.get("format") in FMT and c.get("format") != "link" else "") for c in lst["columns"]) + ".")
        if lst.get("tabs"):
            L.append("Вкладки: " + ", ".join(t["label"] for t in lst["tabs"]) + ".")
        extra = []
        if lst.get("search"):
            extra.append(f"поиск ({lst['search'].get('placeholder', 'по тексту')})")
        extra += [f"фильтр «{f['label']}»" for f in lst.get("filters", [])]
        if extra:
            L.append("Найти нужное: " + ", ".join(extra) + ". Кнопка «Сбросить» очищает фильтры.")
        if lst.get("rowLink"):
            L.append(f"Щелчок по строке открывает экран «{by_id[lst['rowLink']]['title']}».")
        L.append("")
    if s.get("detail"):
        L.append("**Что на экране.** " + "; ".join(
            f"{sec['title']}" + (f": {', '.join(f['label'] for f in sec.get('fields', []))}" if sec.get("fields") else "") for sec in s["detail"]["sections"]) + ".")
        L.append("")
    if s.get("form"):
        L.append("**Как заполнить.** Поля, отмеченные *, обязательны.")
        for f in s["form"]["fields"]:
            hint = f.get("hint") or f.get("validation", {}).get("patternMessage") or ""
            L.append(f"- **{f['label']}**{' *' if f.get('required') else ''}{' — ' + hint if hint else ''}")
        L.append(f"\nНажмите «{s['form']['submit']['label']}». После сохранения появится сообщение «{s['form']['submit']['successMessage']}». "
                 "Ошибки показываются под полями; при сбое сети форму можно отправить повторно — дубль не создастся.")
        L.append("")
    if s.get("map"):
        L.append("**Карта.** Слои: " + ", ".join(l.get("title") or l["id"] for l in s["map"]["layers"]) + ". Щелчок по объекту показывает подробности.")
        legend = [f"{v['label']}" for l in s["map"]["layers"] for v in (l.get("statusMap") or {}).values()]
        if legend:
            L.append("Цвета: " + ", ".join(legend) + ".")
        if s["map"].get("sidePanel"):
            L.append(f"Справа — «{s['map']['sidePanel']['title']}».")
        if s["map"].get("playback"):
            L.append("Кнопка ▶ проигрывает маршрут, ползунок — перемотка.")
        L.append("")
    if s.get("timeline"):
        L.append("**Лента.** Новые записи появляются сверху" + (" автоматически" if s.get("realtime") else "") + ".")
        if s["timeline"].get("filters"):
            L.append("Фильтры: " + ", ".join(f["label"] for f in s["timeline"]["filters"]) + ".")
        L.append("")
    if s.get("dashboard"):
        L.append("**Показатели.** " + ", ".join(w["title"] for w in s["dashboard"]["widgets"]) + ".")
        L.append("")
    acts = s.get("actions", [])
    if acts:
        L.append("**Действия.**")
        for a in acts:
            who = f" (доступно: {', '.join(a['roles'])})" if a.get("roles") else ""
            conf = " Потребуется подтверждение." if a.get("confirm") else ""
            res = f" Результат: «{a['successMessage']}»." if a.get("successMessage") else ""
            L.append(f"- «{a['label']}»{who}.{conf}{res}")
        L.append("")
    if s.get("notifications"):
        L.append("**Уведомления.** " + "; ".join(f"«{re.sub(r'\{[^}]+\}', '…', n['text'])}»" for n in s["notifications"]) + ".")
        L.append("")
    st = s.get("states", {})
    if s["archetype"] not in ("login", "forbidden") and st:
        L.append("**Если что-то не так.** "
                 f"Нет данных — «{st.get('empty', {}).get('title', '')}». "
                 f"Ошибка загрузки — «{st.get('error', {}).get('title', '')}»" + (", кнопка «Повторить»" if st.get("error", {}).get("retry", True) else "") + ". "
                 f"Нет прав — «{st.get('forbidden', {}).get('title', '')}».")
        L.append("")
    return L


def user_guide(spec: dict, req: dict, shots: dict, out: Path) -> str:
    frs = {f["id"]: f["title"] for f in req.get("functional", [])} if req else {}
    roles = {r["id"]: r["title"] for r in spec["roles"]}
    by_id = {s["id"]: s for s in spec["screens"]}
    L = [f"# Руководство пользователя — {spec['app']['title']} ({'веб' if spec['app']['platform'] == 'web' else 'Android'})", "",
         "> Документ сгенерирован из спецификации экранов. Скриншоты — из автоматического UI-ревью.", "", "## Начало работы", ""]
    if spec["app"].get("auth", {}).get("required", True) and spec["app"].get("auth", {}).get("loginScreen"):
        L += ["Откройте приложение и войдите под своей учётной записью. Если раздел недоступен вашей роли, вы увидите сообщение «нет доступа» с пояснением.", ""]
    L += ["Главное меню:", ""] + [f"- **{n['label']}** — {by_id[n['screen']]['title']}" for n in spec["navigation"]] + [""]
    L += ["## Роли", ""]
    for rid, title in roles.items():
        allowed = [s["title"] for s in spec["screens"] if rid in s["roles"] and s["archetype"] not in ("login", "forbidden")]
        L.append(f"- **{title}** — {', '.join(allowed) or '—'}")
    L += ["", "## Экраны", ""]
    order = [n["screen"] for n in spec["navigation"]] + [s["id"] for s in spec["screens"] if s["id"] not in {n["screen"] for n in spec["navigation"]}]
    for sid in order:
        s = by_id[sid]
        if s["archetype"] == "forbidden":
            continue
        L += screen_section(s, spec, frs, shots, out)
    return "\n".join(L).rstrip() + "\n"


def screens_md(spec: dict) -> str:
    L = [f"# Экраны — {spec['app']['title']} ({spec['app']['platform']})", "", "| Экран | Тип | Адрес | Роли | Требования |", "|---|---|---|---|---|"]
    for s in spec["screens"]:
        L.append(f"| {s['title']} (`{s['id']}`) | {ARCH.get(s['archetype'], s['archetype'])} | `{s['route']}` | {', '.join(s['roles'])} | {', '.join(s.get('requirements', [])) or '—'} |")
    L += ["", "## Навигация", "", "```mermaid", "flowchart LR"]
    for s in spec["screens"]:
        L.append(f"  {s['id'].replace('-', '_')}[\"{s['title']}\"]")
    edges = set()

    def walk(src, node):
        if isinstance(node, dict):
            for k in ("linkTo", "rowLink", "navigateTo"):
                if isinstance(node.get(k), str):
                    edges.add((src, node[k]))
            for v in node.values():
                walk(src, v)
        elif isinstance(node, list):
            for v in node:
                walk(src, v)
    for s in spec["screens"]:
        walk(s["id"], {k: v for k, v in s.items() if k != "states"})
    for a, b in sorted(edges):
        if a != b:
            L.append(f"  {a.replace('-', '_')} --> {b.replace('-', '_')}")
    L.append("```")
    return "\n".join(L) + "\n"


SCENARIO_KIND = {"main": "основной", "alternative": "альтернативный", "access": "доступ"}


def traceability(specs: list[dict], req: dict, project: Path | None, traces: dict | None = None,
                 scenarios: dict | None = None) -> tuple[dict, str]:
    """FR → экраны → автотесты.

    Виды тестов: unit — тесты состояний страницы (jest); smoke — экран открывается без ошибок консоли
    (e2e/screens.spec.ts); scenario — пользовательский сценарий против API (e2e/scenarios.spec.ts):
    создание и сохранение данных, ошибки ввода, действия, запрет по роли.
    scenarios — содержимое e2e/scenarios.trace.json; если None, читается из project.
    """
    if scenarios is None and project and (project / "e2e" / "scenarios.trace.json").exists():
        scenarios = load(project / "e2e" / "scenarios.trace.json")
    sc_by_fr: dict = (scenarios or {}).get("requirements") or {}
    frs = {f["id"]: f for f in (req or {}).get("functional", [])}
    rows: dict[str, dict] = {fid: {"id": fid, "title": f["title"], "source": f.get("source", "customer"), "screens": [], "tests": []} for fid, f in frs.items()}
    for spec in specs:
        plat = spec["app"]["platform"]
        for s in spec["screens"]:
            refs = set(s.get("requirements", [])) | {r for sl in s.get("slots", []) for r in sl.get("requirements", [])}
            for r in sorted(refs):
                row = rows.setdefault(r, {"id": r, "title": "", "source": "?", "screens": [], "tests": []})
                row["screens"].append({"platform": plat, "screen": s["id"], "title": s["title"], "route": s["route"]})
                if plat == "web" and project:
                    spec_file = project / "src" / "app" / "generated" / "pages" / s["id"] / f"{s['id']}.page.spec.ts"
                    if spec_file.exists():
                        n = spec_file.read_text(encoding="utf-8").count("  it(")
                        row["tests"].append({"kind": "unit", "file": str(spec_file.relative_to(project)).replace("\\", "/"), "cases": n})
                    if ":" not in s["route"] and not s.get("public") and (project / "e2e" / "screens.spec.ts").exists():
                        row["tests"].append({"kind": "smoke", "file": "e2e/screens.spec.ts", "screen": s["id"],
                                             "case": f"экран {s['id']} открывается без ошибок консоли"})
    for fid, items in sc_by_fr.items():
        row = rows.setdefault(fid, {"id": fid, "title": "", "source": "?", "screens": [], "tests": []})
        for it in items:
            t = {"kind": "scenario", "file": "e2e/scenarios.spec.ts", "case": it["case"], "scenario": it.get("kind", "main")}
            if it.get("screen"):
                t["screen"] = it["screen"]
            if it.get("skip"):
                t["skip"] = it["skip"]
            row["tests"].append(t)
    # уверенность привязки из 03_ux/trace.<platform>.json: худшая по платформам
    rank = {"high": 3, "medium": 2, "low": 1, "none": 0}
    word = {"high": "уверенно", "medium": "вероятно", "low": "не уверено — проверить", "none": "не покрыто"}
    for plat_trace in (traces or {}).values():
        for fid, t in plat_trace.items():
            if fid in rows:
                cur = rows[fid].get("confidence")
                if cur is None or rank[t["confidence"]] < rank[cur]:
                    rows[fid]["confidence"], rows[fid]["confidenceReason"] = t["confidence"], t["reason"]
    covered = [r for r in rows.values() if r["screens"]]
    sure = [r for r in covered if r.get("confidence", "high") in ("high", "medium")]
    # «проверено сценарием» — есть основной или альтернативный сценарий, который не пропускается заранее;
    # запрет по роли (access) проверяет доступ к экрану, а не само требование, — не считается
    scenario_rows = [r for r in rows.values() if any(t["kind"] == "scenario" and t["scenario"] != "access" and not t.get("skip")
                                                     for t in r["tests"])]
    summary = {"total": len(rows), "withUi": len(covered), "confident": len(sure), "withScenario": len(scenario_rows)}
    L = ["# Трассируемость интерфейса", "",
         f"Требований с UI: {len(covered)} из {len(rows)}; привязано уверенно или вероятно: {len(sure)}; "
         f"проверено пользовательским сценарием: {len(scenario_rows)} из {len(rows)} "
         "(основной или альтернативный сценарий; одна проверка доступа по роли не считается).", "",
         "Уверенность — насколько экран по смыслу отвечает требованию (совпадение типа экрана, действия и предмета). "
         "«Не уверено» — привязка без явного совпадения, её нужно проверить вручную.", "",
         "Виды автотестов: **unit** — состояния страницы (загрузка, пусто, ошибка, нет прав, данные); "
         "**smoke** — экран открывается без ошибок консоли, действия пользователя не проверяются; "
         "**сценарий** — пользовательский сценарий против API (`e2e/scenarios.spec.ts`): основной — создание или действие "
         "с ответом сервера и обратной связью, альтернативный — ошибки ввода, доступ — запрет по роли. "
         "Сценарии одного требования: `npx playwright test e2e/scenarios.spec.ts --grep @FR-01`.", "",
         "| FR | Требование | Экраны | Уверенность | Автотесты |", "|---|---|---|---|---|"]
    for r in sorted(rows.values(), key=lambda x: x["id"]):
        scr = ", ".join(f"{x['title']} ({x['platform']})" for x in r["screens"]) or "—"
        base = [t for t in r["tests"] if t["kind"] != "scenario"]
        sc = [t for t in r["tests"] if t["kind"] == "scenario"]
        parts = list(dict.fromkeys(f"{t['kind']}: {t['file'].split('/')[-1]}" for t in base))
        for t in sc:
            note = f"; будет пропущен: {t['skip']}" if t.get("skip") else ""
            parts.append(f"сценарий ({SCENARIO_KIND.get(t['scenario'], t['scenario'])}{note}): {t['case']}")
        web = [x for x in r["screens"] if x["platform"] == "web"]
        if project and web:
            with_sc = {t.get("screen") for t in sc if t["scenario"] != "access"}
            if not any(t["scenario"] != "access" for t in sc):
                parts.append("нет сценария — только открытие экрана" if any(t["kind"] == "smoke" for t in base)
                             else "нет сценария")
            else:
                bare = [x["title"] for x in web if x["screen"] not in with_sc]
                if bare:
                    parts.append("нет сценария — только открытие экрана: " + ", ".join(bare))
        tst = "<br>".join(parts) or "—"
        conf = word.get(r.get("confidence", ""), "—") if r["screens"] or r.get("confidence") else "—"
        L.append(f"| {r['id']} | {r['title']} | {scr} | {conf} | {tst} |")
    return {"summary": summary, "requirements": sorted(rows.values(), key=lambda x: x["id"])}, "\n".join(L) + "\n"


def generate_docs(inp: Path, out: Path, project: Path | None = None, shots_dir: Path | None = None) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    req = load(inp / "01_requirements" / "requirements.json") or {}
    shots: dict = {}
    if shots_dir and (shots_dir / "shots.json").exists():
        for sh in load(shots_dir / "shots.json"):
            if Path(sh["path"]).exists():
                shots[(sh["screen"], sh["state"], sh["viewport"])] = sh["path"]
    specs, written = [], []
    for plat in ("web", "android"):
        spec = load(inp / "03_ux" / f"screen-spec.{plat}.json")
        if not spec:
            continue
        specs.append(spec)
        for name, text in ((f"user-guide.{plat}.md", user_guide(spec, req, shots if plat == "web" else {}, out)), (f"screens.{plat}.md", screens_md(spec))):
            (out / name).write_text(text, encoding="utf-8")
            written.append(out / name)
    traces = {plat: load(inp / "03_ux" / f"trace.{plat}.json") for plat in ("web", "android")}
    data, md = traceability(specs, req, project, {k: v for k, v in traces.items() if v})
    (out / "traceability.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "traceability.md").write_text(md, encoding="utf-8")
    return written + [out / "traceability.json", out / "traceability.md"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--project", type=Path)
    ap.add_argument("--shots", type=Path)
    a = ap.parse_args()
    for p in generate_docs(a.inp, a.out, a.project, a.shots):
        print(p)
