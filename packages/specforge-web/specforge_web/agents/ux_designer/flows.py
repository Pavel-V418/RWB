"""Пользовательские потоки из спеки экранов — артефакт этапа 03_ux (ТЗ, этап 3: «пайплайн должен сформировать
пользовательские потоки, экраны, состояния и компоненты»; «основные и альтернативные сценарии»).

Детерминированно, без LLM: потоки выводятся из того, что реально будет в интерфейсе (навигация, переходы,
формы, действия, роли, состояния) — документ не может разойтись с кодом, который сгенерирован из той же спеки.
Для каждой роли:
  • точки входа (меню) и схема переходов (Mermaid);
  • основные сценарии: создание через форму, действия над записью, просмотр (список → карточка, карта → карточка);
  • альтернативные: пустая/неверная форма, ошибка сервера, подтверждение опасного действия, пустой список,
    ошибка загрузки, нет доступа.
"""
from __future__ import annotations

import re


def _title(screens: dict, sid: str | None) -> str:
    return screens[sid]["title"] if sid in screens else (sid or "—")


def _links(screen: dict) -> list[tuple[str, str]]:
    """Переходы экрана: (куда, подпись). Ищутся во всех блоках спеки: rowLink, linkTo, navigateTo."""
    out: list[tuple[str, str]] = []

    def walk(node, label=""):
        if isinstance(node, dict):
            lbl = node.get("label") or node.get("title") or label
            for key in ("rowLink", "linkTo", "navigateTo"):
                if isinstance(node.get(key), str):
                    text = "строка списка" if key == "rowLink" else lbl or ("объект на карте" if "geometryField" in node else "переход")
                    out.append((node[key], text))
            for k, v in node.items():
                if k not in ("states",):
                    walk(v, lbl if isinstance(lbl, str) else "")
        elif isinstance(node, list):
            for v in node:
                walk(v, label)
    for part in ("list", "detail", "map", "timeline", "dashboard", "actions", "form"):
        walk(screen.get(part))
    seen, uniq = set(), []
    for t, lbl in out:  # одна стрелка на пару экранов: строка и ссылка в ячейке ведут в одно место
        if t not in seen:
            seen.add(t)
            uniq.append((t, lbl))
    return uniq


def _can(screen: dict, role: str, item: dict | None = None) -> bool:
    roles = (item or {}).get("roles") or screen.get("roles") or []
    return not roles or role in roles


def _mermaid_id(sid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", sid)


def _mm(text: str) -> str:
    """Текст для Mermaid без символов разметки."""
    return re.sub(r'["|\[\]{}<>]', " ", str(text)).strip()[:28] or "переход"


def user_flows(spec: dict, requirements: dict | None = None) -> str:
    screens = {s["id"]: s for s in spec["screens"]}
    roles = [r["id"] for r in spec.get("roles", [])] or sorted({r for s in spec["screens"] for r in s.get("roles", [])})
    titles = {r["id"]: r.get("title") or r["id"] for r in spec.get("roles", [])}
    frs = {f["id"]: f["title"] for f in (requirements or {}).get("functional", [])}
    nav = [n["screen"] for n in spec.get("navigation", []) if n["screen"] in screens]
    plat = spec.get("app", {}).get("platform", "web")
    L = [f"# Пользовательские потоки — {spec.get('app', {}).get('title', '')} ({plat})", "",
         "Сформировано из спеки экранов: те же экраны, переходы, роли и состояния, что в сгенерированном интерфейсе.", ""]
    login = next((s for s in spec["screens"] if s["archetype"] == "login"), None)
    for role in roles:
        mine = [s for s in spec["screens"] if not s.get("public") and _can(s, role)]
        if not mine:
            continue
        L += [f"## {titles.get(role, role)} (`{role}`)", ""]
        entry = [sid for sid in nav if _can(screens[sid], role)]
        L.append("**Точки входа:** " + (f"вход («{login['title']}») → " if login else "") +
                 (", ".join(f"«{_title(screens, sid)}»" for sid in entry) or "—"))
        L.append("")
        # схема переходов
        L += ["```mermaid", "flowchart LR"]
        ids = {s["id"] for s in mine}
        for s in mine:
            L.append(f'  {_mermaid_id(s["id"])}["{_mm(s["title"])}"]')
        for s in mine:
            for target, lbl in _links(s):
                if target in ids and target != s["id"]:
                    L.append(f'  {_mermaid_id(s["id"])} -->|{_mm(lbl)}| {_mermaid_id(target)}')
        L += ["```", ""]
        n = 0
        L.append("### Основные сценарии")
        L.append("")
        for s in mine:
            if s["archetype"] != "form":
                continue
            sub = (s.get("form") or {}).get("submit") or {}
            came = sorted((o for o in mine if any(t == s["id"] for t, _ in _links(o)) and o["id"] != s["id"]),
                          key=lambda o: o["archetype"] != "list")
            start = f"«{came[0]['title']}» → " if came else ""
            after = f" → «{_title(screens, sub.get('navigateTo'))}»" if sub.get("navigateTo") else ""
            n += 1
            req = ", ".join(r for r in s.get("requirements", []) if r in frs)
            L.append(f"{n}. **{s['title']}**{f' ({req})' if req else ''}: {start}«{s['title']}» → заполнить поля → "
                     f"«{sub.get('label', 'Сохранить')}» → уведомление: {sub.get('successMessage') or 'Сохранено'}{after}")
        for s in mine:
            acts = [a for a in s.get("actions", []) or [] if a.get("operationId") and _can(s, role, a)]
            if not acts:
                continue
            came = sorted((o for o in mine if any(t == s["id"] for t, _ in _links(o)) and o["id"] != s["id"]),
                          key=lambda o: o["archetype"] != "list")
            start = f"«{came[0]['title']}» → " if came else ""
            for a in acts:
                n += 1
                conf = " → подтверждение" if a.get("confirm") else ""
                L.append(f"{n}. **{a['label']}**: {start}«{s['title']}» → «{a['label']}»{conf} → уведомление: {a.get('successMessage') or 'Готово'}")
        for s in mine:
            if s["archetype"] in ("list", "map", "timeline", "dashboard"):
                targets = [(t, lbl) for t, lbl in _links(s) if t in ids and screens[t]["archetype"] == "detail"]
                if targets:
                    n += 1
                    L.append(f"{n}. **Просмотр**: «{s['title']}» → {targets[0][1]} → «{_title(screens, targets[0][0])}»")
        if n == 0:
            L.append("— (только просмотр)")
        L += ["", "### Альтернативные сценарии", ""]
        alt = []
        for s in mine:
            st = s.get("states") or {}
            if s["archetype"] == "form":
                fields = (s.get("form") or {}).get("fields", [])
                if any(f.get("required") for f in fields):
                    alt.append(f"«{s['title']}»: пустые обязательные поля → под полями «Обязательное поле», запрос не отправляется")
                for f in fields:
                    msg = (f.get("validation") or {}).get("patternMessage")
                    if msg:
                        alt.append(f"«{s['title']}»: неверный формат поля «{f['label']}» → «{msg}»")
                err = (st.get("error") or {}).get("title")
                alt.append(f"«{s['title']}»: сервер не сохранил → сообщение «{err or 'Не удалось сохранить'}», введённые данные остаются")
            for a in s.get("actions", []) or []:
                if a.get("confirm") and _can(s, role, a):
                    alt.append(f"«{s['title']}» → «{a['label']}»: отказ в подтверждении → ничего не меняется")
            if s["archetype"] in ("list", "timeline", "map") and (st.get("empty") or {}).get("title"):
                act = (st.get("empty") or {}).get("actionId")
                hint = next((f" → кнопка «{a['label']}»" for a in s.get("actions", []) or [] if a.get("id") == act), "")
                alt.append(f"«{s['title']}»: данных нет → «{st['empty']['title']}»{hint}")
        denied = [s for s in spec["screens"] if not s.get("public") and not _can(s, role)]
        for s in denied[:6]:
            fb = (s.get("states") or {}).get("forbidden", {}).get("title") or "Нет доступа"
            alt.append(f"«{s['title']}» недоступен роли → «{fb}»")
        alt.append("Любой экран: ошибка загрузки → сообщение и кнопка «Повторить»; сессия истекла → возврат на вход")
        L += [f"- {x}" for x in dict.fromkeys(alt)] + [""]
    return "\n".join(L) + "\n"
