"""Привязка функциональных требований к экранам — по смыслу, без LLM, с честной оценкой уверенности.

Используется в трёх местах:
  1. baseline (страховка без модели) — выбирает экраны для каждого FR;
  2. UX-агент — дозакрепляет FR, которые модель пропустила, и проверяет привязки модели;
  3. документация — колонка «уверенность» в матрице трассируемости.

Оценка пары (FR, экран):
  • совпадение предметных слов FR с текстами экрана (заголовок, подписи колонок/полей/действий/виджетов);
  • намерение FR («на карте», «сводка», «журнал», «вход», «создать», «список с фильтром») ↔ архетип экрана;
  • глагол действия FR («назначить», «отменить», «завершить») ↔ действие на экране.
Уверенность: high — намерение/действие и предмет совпали; medium — совпало одно из них; low — привязка
по умолчанию (совпадений нет). Low не скрывается: шлюз выдаёт предупреждение UX_FR_WEAK, а в матрице
трассируемости стоит «не уверено». Метрика coverageConfident считает только high и medium.
"""
from __future__ import annotations

import re

# Намерение → (основы слов в тексте FR, архетипы/признаки экрана, которые его закрывают)
INTENTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "map": (("карт", "геол", "местоп", "маршр", "коорд", "трек"), ("map",)),
    "dashboard": (("сводк", "итог", "стати", "показа", "аналит", "дашб", "отчёт", "отчет", "kpi"), ("dashboard",)),
    "timeline": (("журна", "событ", "лент", "реальном врем", "уведо", "хрони"), ("timeline",)),
    "history": (("истор", "архив"), ("timeline", "list", "detail")),
    "auth": (("вход ", "входа", "вход,", "логин", "авториз", "учётн", "учетн", "аутен", "парол"), ("login", "forbidden")),
    "create": (("созда", "добав", "оформ", "заполн", "регис", "ввод", "внест", "внос", "принима"), ("form",)),
    "list": (("списо", "катал", "фильт", "поиск", "реест", "просм", "переч"), ("list",)),
    "detail": (("карто", "подро", "детал", "сведе"), ("detail",)),
}
STOP = {"котор", "может", "можно", "нужно", "нужен", "нужна", "долже", "систе", "польз", "через", "также", "своих", "своё",
        "свой", "своей", "каждо", "всех", "любой", "указа", "видет", "видит", "видны", "отобр", "показ", "получ", "иметь",
        "имеет", "работ", "данны", "инфор", "сервис", "прило", "интер", "экран", "функц", "возмо", "для", "при", "или",
        "без", "под", "над", "это", "его", "она", "они"}
LEVEL = {"high": 2, "medium": 1, "low": 0}


ENDINGS = sorted("""ениями ениях ением ения ение ений аниями ания ание аний ости ость остью ться ется ются ивать ывать
ами ями ого его ому ему ыми ими ах ях ам ям ом ем ой ей ый ий ая яя ое ее ые ие ых их ую юю ов ев ть ет ют ит ят ат ут
ла ло ли а я ы и е у ю о ь""".split(), key=len, reverse=True)


def stem(word: str) -> str:
    """Лёгкий стемминг: «карте»/«карта» → «карт», «заказов» → «заказ»; основа не короче 4 букв, не длиннее 6."""
    w = word.lower()
    if re.fullmatch(r"[а-яё]+", w):
        for e in ENDINGS:
            if w.endswith(e) and len(w) - len(e) >= 4:
                w = w[: -len(e)]
                break
    return w[:6]


def stems(text: str) -> set[str]:
    words = re.findall(r"[a-zа-яё]{3,}", str(text).lower())
    return {st for st in (stem(w) for w in words) if st[:5] not in STOP and st not in STOP}


VERB_END = re.compile(r"(ть|ться|ет|ют|ит|ят|ат|ут|ется|ются)$")


def verb_stems(text: str) -> set[str]:
    """Основы глаголов FR («назначить», «отменяет»), а не существительных («назначения»)."""
    # у глаголов окончания разные («отменяет»/«Отменить», «закрывать»/«Закрыть») — сравниваем по 5 первым буквам
    return {w[:5] for w in re.findall(r"[а-яё]{4,}", str(text).lower()) if VERB_END.search(w)}


def intents_of(title: str) -> set[str]:
    t = title.lower()
    return {name for name, (keys, _) in INTENTS.items() if any(k in t for k in keys)}


def screen_bag(screen: dict, nav_labels: dict[str, str] | None = None) -> tuple[set[str], set[str]]:
    """→ (основы предметных слов экрана, основы глаголов его действий)."""
    texts = [screen.get("title", ""), (nav_labels or {}).get(screen.get("id", ""), ""), screen.get("purpose", "")]
    acts: list[str] = []

    def walk(node, key=""):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("label", "title", "placeholder", "emptyText", "navLabel") and isinstance(v, str):
                    texts.append(v)
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
    for part in ("list", "detail", "form", "map", "timeline", "dashboard"):
        walk(screen.get(part))
    for a in screen.get("actions", []) or []:
        texts.append(a.get("label", ""))
        if a.get("operationId"):  # «Отмена» формы — навигация, а не бизнес-действие
            acts.append(a.get("label", ""))
    for layer in (screen.get("map") or {}).get("layers", []) or []:
        texts.append(str(layer.get("id", "")).replace("-", " "))
    for sl in screen.get("slots", []) or []:
        texts.append(sl.get("task", ""))
    verb = {w[:5] for a in acts for w in re.findall(r"[a-zа-яё]{4,}", a.lower())[:1]}
    return stems(" ".join(texts)), verb


def score(fr_title: str, screen: dict, role_stems: set[str], nav: dict[str, str] | None = None) -> tuple[int, str]:
    """→ (баллы, объяснение). ≥5 — уверенно, 3–4 — вероятно, <3 — слабо."""
    fr = stems(fr_title) - role_stems
    bag, verbs = screen_bag(screen, nav)
    subject = fr & bag
    arch = screen.get("archetype", "")
    map_section = any(sec.get("kind") == "map" for sec in (screen.get("detail") or {}).get("sections", []))
    wanted = intents_of(fr_title)
    hit_intents = [i for i in wanted if arch in INTENTS[i][1]]
    verb_hit = sorted(verb_stems(fr_title) & verbs)
    # тип экрана, прямо названный в FR («на карте», «сводка», «журнал»), весит больше общих слов
    pts = 2 * min(len(subject), 2) + (4 if hit_intents else 0) + (3 if verb_hit else 0)
    if "map" in wanted and not hit_intents and map_section:
        pts += 1  # карта внутри карточки — слабее, чем экран-карта
    why = []
    if hit_intents:
        why.append("намерение: " + ", ".join(hit_intents))
    if verb_hit:
        why.append("действие: " + ", ".join(verb_hit))
    if subject:
        why.append("предмет: " + ", ".join(sorted(subject)[:4]))
    return pts, "; ".join(why) or "совпадений нет"


def confidence(pts: int) -> str:
    return "high" if pts >= 5 else "medium" if pts >= 3 else "low"


def candidates(fr: dict, screens: list[dict], roles: list[dict] | None = None, nav: dict[str, str] | None = None) -> list[tuple[int, dict, str]]:
    role_stems = stems(" ".join(f"{r.get('id', '')} {r.get('title', '')}" for r in roles or []))
    want_auth = "auth" in intents_of(fr["title"])
    # служебные экраны (вход, «нет доступа») закрывают только требование входа; бизнес-требование на них
    # не вешаем — если рабочих экранов нет, FR остаётся непокрытым и честно срабатывает страховка
    pool = [s for s in screens if s.get("archetype") != "forbidden" and (not s.get("public") or (want_auth and s.get("archetype") == "login"))]
    scored = [(*score(fr["title"], s, role_stems, nav),) for s in pool]
    return sorted(((p, s, why) for (p, why), s in zip(scored, pool)), key=lambda x: -x[0])


def choose(fr: dict, screens: list[dict], roles: list[dict] | None = None, nav: dict[str, str] | None = None,
           limit: int = 3) -> tuple[list[dict], str, str]:
    """Экраны для FR: лучший + близкие к нему (≥ 60% баллов лучшего, не меньше 3). → (экраны, уверенность, причина)."""
    ranked = candidates(fr, screens, roles, nav)
    if not ranked:
        return [], "low", "нет экранов"
    best, top, why = ranked[0]
    if best < 3:
        # совпадений нет — привязываем к ближайшему рабочему экрану и честно помечаем
        return [top], "low", f"привязка по умолчанию ({why})"
    # дополнительные экраны — только с тем же видом доказательства (намерение или действие), а не по общим словам
    picked = [top] + [s for p, s, w in ranked[1:] if p >= max(3, 0.8 * best) and ("намерение" in w or "действие" in w)]
    return picked[:limit], confidence(best), why


def link_actions(fr: dict, screen: dict) -> list[str]:
    """FR с глаголом действия экрана («Отменить заказ» → действие «Отменить») пишется в action.requirements,
    чтобы сценарий этого действия ссылался именно на это требование, а не на все требования экрана."""
    verbs = verb_stems(fr["title"])
    linked = []
    for a in screen.get("actions", []) or []:
        if not a.get("operationId"):
            continue
        first = re.findall(r"[a-zа-яё]{4,}", str(a.get("label", "")).lower())[:1]
        if first and first[0][:5] in verbs:
            reqs = a.setdefault("requirements", [])
            if fr["id"] not in reqs:
                reqs.append(fr["id"])
            linked.append(a["id"])
    return linked


def assign(requirements: dict, screens: list[dict], platform: str, nav: dict[str, str] | None = None) -> dict[str, dict]:
    """Проставить screens[].requirements для FR канала platform. → трасса {FR: {screens, confidence, reason}}."""
    roles = requirements.get("roles", [])
    trace: dict[str, dict] = {}
    for fr in requirements.get("functional", []):
        if platform not in fr.get("channels", [platform]):
            continue
        picked, conf, why = choose(fr, screens, roles, nav)
        for s in picked:
            if fr["id"] not in s["requirements"]:
                s["requirements"].append(fr["id"])
            link_actions(fr, s)
        trace[fr["id"]] = {"title": fr["title"], "screens": [s["id"] for s in picked], "confidence": conf, "reason": why, "by": "heuristic"}
    return trace


def assess(spec: dict, requirements: dict, by: str = "spec") -> dict[str, dict]:
    """Оценить уже сделанные привязки (кем угодно — моделью или эвристикой): насколько экран отвечает FR."""
    platform = spec.get("app", {}).get("platform", "web")
    nav = {n["screen"]: n.get("label", "") for n in spec.get("navigation", [])}
    roles = requirements.get("roles", [])
    role_stems = stems(" ".join(f"{r.get('id', '')} {r.get('title', '')}" for r in roles))
    out: dict[str, dict] = {}
    for fr in requirements.get("functional", []):
        if platform not in fr.get("channels", [platform]):
            continue
        linked = [s for s in spec["screens"] if fr["id"] in s.get("requirements", []) or
                  any(fr["id"] in sl.get("requirements", []) for sl in s.get("slots", []) or [])]
        if not linked:
            out[fr["id"]] = {"title": fr["title"], "screens": [], "confidence": "none", "reason": "не покрыто", "by": by}
            continue
        best = max((score(fr["title"], s, role_stems, nav) for s in linked), key=lambda x: x[0])
        out[fr["id"]] = {"title": fr["title"], "screens": [s["id"] for s in linked], "confidence": confidence(best[0]),
                         "reason": best[1], "by": by}
    return out


def confident_share(trace: dict[str, dict]) -> float | None:
    if not trace:
        return None
    return round(sum(1 for t in trace.values() if t["confidence"] in ("high", "medium")) / len(trace), 3)
