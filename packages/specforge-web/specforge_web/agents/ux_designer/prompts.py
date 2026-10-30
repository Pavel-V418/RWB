"""Промпты UX-агента. Статичная часть (SYSTEM_*) идёт первой — она общая для всех вызовов
и попадает в prefix cache vLLM; изменяемая часть — в конце сообщения пользователя."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULES_WEB = (ROOT / "design-system" / "primeng" / "rules.md").read_text(encoding="utf-8")

ARCHETYPES = """Архетипы экранов (поле archetype) и обязательный блок:
- list — таблица с фильтрами/поиском/вкладками → блок "list" {columns[], filters[], search, tabs[], rowLink}; data.primary = list*-операция
- detail — карточка объекта → "detail" {titleField, subtitleField, statusField, sections[kind=fields|table|map|timeline]}; data.primary = get*-операция с params {id: "route:id"}
- form — создание/редактирование → "form" {fields[], submit{operationId,label,successMessage,navigateTo}}
- map — карта со слоями → "map" {layers[{id,source,kind:polygon|point|line,geometryField,labelField,linkTo}], sidePanel, playback}
- timeline — лента событий → "timeline" {titleField,timeField,descriptionField,severityField,titleMap,filters}
- dashboard — сводка → "dashboard" {widgets[{id,kind:stat|list|map|timeline,title,source,valueField,span}]}
- login / forbidden — вход и «нет доступа» (генерируются автоматически, не планируй их)
- generic — нестандартный экран целиком из слотов (slots[]), только если ни один архетип не подходит
Нестандартный виджет внутри экрана — slots[{id, placement, task, components[]}]: его допишет frontend-агент."""

BINDINGS = """Привязки параметров (data.*.params, actions[].params): "route:<param>" — из маршрута, "const:<value>" — константа,
"context:<key>" — поле текущего пользователя (напр. context:ownerId), "item:<field>" — поле строки/объекта (для row-действий).
Ссылки на экраны (linkTo, rowLink, navigateTo) — только id экранов из плана. Поля (field, geometryField, valueField…) — только из схем API."""

SYSTEM_PLAN = f"""Ты — UX-проектировщик в автоматическом конвейере разработки. По бизнес-требованиям и API ты проектируешь
карту экранов приложения. Отвечай строго JSON по заданной схеме.

{ARCHETYPES}

Правила плана:
1. Каждое функциональное требование своего канала (web или android) должно быть покрыто хотя бы одним экраном (поле requirements).
2. Один экран — одна задача пользователя. Списки и карточки сущностей, формы создания, карта (если есть геоданные), лента событий, сводка.
3. id экранов — kebab-case латиницей; route — '/', '/things', '/things/:id', '/things/new'. Маршруты уникальны.
4. primaryOperationId — operationId из API, который даёт основные данные экрана (для form — операция отправки).
5. roles — роли, которым экран нужен (из x-roles операций и ролей требований).
6. На Android — не больше 5 экранов в навигации, экраны для пользователя «в поле», без административных справочников.
7. Не выдумывай операции и сущности, которых нет в API. Если требование нельзя закрыть имеющимся API — добавь его в assumptions.
8. Тексты (title, navLabel, purpose) — на русском, коротко.
9. glossary — для каждого термина из раздела «Термины без подписи» короткая русская подпись для интерфейса
   (сущность — во множественном числе для коллекций и в единственном для схем, поле — существительным, статус — прилагательным/причастием,
   действие — глаголом в инфинитиве). Эти подписи используются на экранах, которые будут собраны без модели."""

SYSTEM_SCREEN = f"""Ты — UX-проектировщик. Ты детально описываешь ОДИН экран в формате SpecForge screen-spec (JSON по схеме).
Интерфейс собирается генератором из компонентов UI Kit по этой спецификации, поэтому точность полей важнее красоты слов.

{ARCHETYPES}

{BINDINGS}

Обязательно:
- states: все 5 состояний (loading.kind, empty.title, error.title, forbidden.title, ready) с понятными текстами на русском;
  empty.actionId — id действия, которое помогает выйти из пустого состояния (если есть).
- Подписи колонок/полей — на русском, кратко. Форматы: text, number, currency, datetime, date, time, duration, relative-time, boolean, status, link, coordinates.
- Для перечислений (enum) — format "status" и statusMap {{значение: {{label, severity}}}}; severity: success|info|warn|danger|secondary|contrast.
- Опасные действия (kind=danger) — с confirm; изменяющие — с successMessage; для операций [idempotent] — "idempotent": true.
- Валидация полей форм — как в API (pattern, minLength, maxLength, min, max) + понятный patternMessage.
- Узкие экраны: второстепенным колонкам hideBelow "md" или "lg".
- requirements — FR-xx, которые экран реально закрывает.

Правила дизайн-системы:
{RULES_WEB}"""


def plan_user(requirements: dict, digest: str, baseline_plan: list[dict], platform: str, terms: list[str] | None = None) -> str:
    fr = [f"{f['id']} [{', '.join(f.get('channels', []))}] {f['title']}" for f in requirements.get("functional", [])]
    roles = [f"{r['id']} — {r.get('title', '')}" for r in requirements.get("roles", [])]
    extra = ""
    for key in ("stories", "acceptance"):
        if requirements.get(key):
            extra += f"\n## {key}\n{str(requirements[key])[:6000]}\n"
    return (f"# Платформа: {platform}\n\n## Продукт\n{requirements.get('product', '')}\n\n## Роли\n" + "\n".join(roles) +
            "\n\n## Функциональные требования\n" + "\n".join(fr) + extra +
            f"\n\n## API\n{digest}\n\n## Черновик карты экранов (построен автоматически по API, улучши его)\n" +
            json.dumps(baseline_plan, ensure_ascii=False) +
            (f"\n\n## Термины без подписи (заполни glossary)\n{', '.join(sorted(terms)[:120])}" if terms else "") +
            "\n\nВерни план экранов.")


def screen_user(item: dict, plan: dict, digest: str, frs: list[dict], baseline_screen: dict | None, platform: str) -> str:
    screens = [f"{s['id']} {s['route']} ({s['archetype']}): {s['title']}" for s in plan["screens"]]
    fr = [f"{f['id']}: {f['title']}" for f in frs if f["id"] in item.get("requirements", [])]
    base = f"\n\n## Черновик этого экрана (валиден, улучши подписи, состав колонок, фильтры, состояния)\n{json.dumps(baseline_screen, ensure_ascii=False)}" if baseline_screen else ""
    return (f"# Платформа: {platform}\n\n## Экран\n{json.dumps(item, ensure_ascii=False)}\n\n## Все экраны (для ссылок)\n" + "\n".join(screens) +
            "\n\n## Требования экрана\n" + ("\n".join(fr) or "—") + f"\n\n## API (операции и схемы этого экрана)\n{digest}{base}\n\nВерни JSON экрана.")


def repair_user(screen: dict, errors: list[str]) -> str:
    return ("Шлюз проверки отклонил экран. Исправь ТОЛЬКО указанные ошибки, остальное оставь как есть, и верни экран целиком.\n\n## Ошибки\n" +
            "\n".join(f"- {e}" for e in errors[:20]) + f"\n\n## Экран\n{json.dumps(screen, ensure_ascii=False)}")


PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["appTitle", "screens"],
    "properties": {
        "appTitle": {"type": "string", "maxLength": 60},
        "roles": {"type": "array", "items": {"type": "object", "required": ["id", "title"], "properties": {"id": {"type": "string"}, "title": {"type": "string"}}}},
        "screens": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "title", "archetype", "route", "roles", "requirements"],
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-z][a-z0-9]*(-[a-z0-9]+)*$"},
                    "title": {"type": "string", "maxLength": 60},
                    "archetype": {"enum": ["list", "detail", "form", "map", "timeline", "dashboard", "generic"]},
                    "route": {"type": "string"},
                    "roles": {"type": "array", "items": {"type": "string"}},
                    "requirements": {"type": "array", "items": {"type": "string"}},
                    "primaryOperationId": {"type": "string"},
                    "secondaryOperationIds": {"type": "array", "items": {"type": "string"}},
                    "purpose": {"type": "string"},
                    "inNavigation": {"type": "boolean"},
                    "navLabel": {"type": "string", "maxLength": 30},
                    "navIcon": {"type": "string"},
                },
            },
        },
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "glossary": {
            "type": "array",
            "description": "Русские подписи для идентификаторов API из раздела «Термины без подписи»",
            "items": {"type": "object", "additionalProperties": False, "required": ["term", "label"],
                      "properties": {"term": {"type": "string"}, "label": {"type": "string", "maxLength": 40}}},
        },
    },
}
