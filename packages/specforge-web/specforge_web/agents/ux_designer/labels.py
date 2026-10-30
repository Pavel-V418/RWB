"""Русские подписи для baseline-спеки — без LLM.

Порядок источников (первый найденный побеждает):
  1. аннотации OpenAPI: title / x-label / x-title, короткое description, summary операции,
     описание и x-displayName тега, x-enum-labels / x-enumNames / x-enum-descriptions / oneOf[const+title];
  2. словарь типовых полей и статусов корпоративных приложений (без предметной области);
  3. имя из API как есть (humanize) — последний шанс, UX-агент с моделью всё равно его перепишет.

Аналитику (участник 2) достаточно писать русские `title` у свойств схем и `x-enum-labels` у перечислений —
тогда baseline-экраны полностью на русском даже без модели.
"""
from __future__ import annotations

import re

# Типовые поля: ключ — имя поля в нижнем регистре без «_» и «-».
FIELDS = {
    "id": "ID", "name": "Название", "fullname": "ФИО", "firstname": "Имя", "lastname": "Фамилия", "middlename": "Отчество",
    "title": "Заголовок", "description": "Описание", "comment": "Комментарий", "comments": "Комментарии", "note": "Примечание",
    "notes": "Примечания", "message": "Сообщение", "text": "Текст", "reason": "Причина", "purpose": "Цель", "result": "Результат",
    "status": "Статус", "state": "Состояние", "type": "Тип", "kind": "Вид", "category": "Категория", "priority": "Приоритет",
    "severity": "Важность", "level": "Уровень", "stage": "Этап", "source": "Источник", "channel": "Канал", "tags": "Метки",
    "code": "Код", "number": "Номер", "serialnumber": "Серийный номер", "inventorynumber": "Инвентарный номер", "sku": "Артикул",
    "email": "Эл. почта", "phone": "Телефон", "address": "Адрес", "city": "Город", "country": "Страна", "location": "Местоположение",
    "position": "Позиция", "geometry": "Геометрия", "latitude": "Широта", "longitude": "Долгота", "radius": "Радиус",
    "quantity": "Количество", "qty": "Количество", "count": "Количество", "total": "Всего", "amount": "Сумма", "price": "Цена",
    "cost": "Стоимость", "currency": "Валюта", "rate": "Ставка", "discount": "Скидка", "balance": "Баланс",
    "unit": "Единица", "weight": "Вес", "volume": "Объём", "capacity": "Вместимость", "size": "Размер", "duration": "Длительность",
    "durationminutes": "Длительность, мин", "durationsec": "Длительность, с", "progress": "Прогресс", "percent": "Процент",
    "score": "Оценка", "rating": "Рейтинг", "version": "Версия", "model": "Модель", "brand": "Марка", "manufacturer": "Производитель",
    "owner": "Владелец", "author": "Автор", "assignee": "Исполнитель", "executor": "Исполнитель", "manager": "Руководитель",
    "employee": "Сотрудник", "user": "Пользователь", "customer": "Клиент", "client": "Клиент", "department": "Подразделение",
    "organization": "Организация", "company": "Компания", "team": "Команда", "role": "Роль", "roles": "Роли", "login": "Логин",
    "password": "Пароль", "active": "Активно", "enabled": "Включено", "isactive": "Активно", "archived": "В архиве",
    "date": "Дата", "time": "Время", "datetime": "Дата и время", "timestamp": "Время", "period": "Период",
    "createdat": "Создано", "updatedat": "Обновлено", "deletedat": "Удалено", "occurredat": "Время события", "recordedat": "Записано",
    "startedat": "Начало", "finishedat": "Окончание", "endedat": "Окончание", "completedat": "Завершено", "closedat": "Закрыто",
    "approvedat": "Подтверждено", "cancelledat": "Отменено", "expiresat": "Истекает", "dueat": "Срок", "duedate": "Срок",
    "deadline": "Срок", "startat": "Начало", "endat": "Окончание", "from": "С", "to": "По", "validfrom": "Действует с",
    "validto": "Действует до", "validuntil": "Действует до", "scheduledat": "Запланировано",
    "plannedat": "Запланировано", "birthdate": "Дата рождения",
    "createdby": "Создал", "updatedby": "Изменил", "url": "Ссылка", "link": "Ссылка", "photo": "Фото", "image": "Изображение",
    "file": "Файл", "attachments": "Вложения", "items": "Позиции", "lines": "Строки", "remaining": "Осталось",
    "q": "Поиск", "search": "Поиск", "page": "Страница",
}

# Типовые статусы и значения перечислений.
ENUMS = {
    "new": "Новый", "draft": "Черновик", "open": "Открыт", "opened": "Открыт", "pending": "Ожидает", "waiting": "Ожидает",
    "queued": "В очереди", "scheduled": "Запланирован", "planned": "Запланирован", "inprogress": "В работе", "processing": "Обрабатывается",
    "running": "Выполняется", "active": "Активен", "inactive": "Неактивен", "enabled": "Включён", "disabled": "Отключён",
    "approved": "Подтверждён", "confirmed": "Подтверждён", "accepted": "Принят", "rejected": "Отклонён", "declined": "Отклонён",
    "cancelled": "Отменён", "canceled": "Отменён", "done": "Выполнен", "completed": "Завершён", "finished": "Завершён",
    "closed": "Закрыт", "resolved": "Решён", "failed": "Ошибка", "error": "Ошибка", "success": "Успешно", "ok": "Норма",
    "expired": "Истёк", "paid": "Оплачен", "unpaid": "Не оплачен", "overdue": "Просрочен",
    "blocked": "Заблокирован", "suspended": "Приостановлен", "paused": "Приостановлен", "archived": "В архиве",
    "deleted": "Удалён", "available": "Доступен", "unavailable": "Недоступен", "busy": "Занят", "free": "Свободен",
    "reserved": "Зарезервирован", "booked": "Забронирован", "online": "В сети", "offline": "Не в сети", "idle": "Простаивает",
    "stopped": "Остановлен", "maintenance": "Обслуживание", "broken": "Неисправен",
    "low": "Низкий", "medium": "Средний", "normal": "Обычный", "high": "Высокий", "critical": "Критический", "urgent": "Срочный",
    "info": "Информация", "warning": "Предупреждение", "warn": "Предупреждение", "danger": "Опасно",
    "yes": "Да", "no": "Нет", "true": "Да", "false": "Нет", "other": "Другое", "unknown": "Неизвестно",
    "admin": "Администратор", "user": "Пользователь", "manager": "Руководитель", "employee": "Сотрудник", "operator": "Оператор",
    "viewer": "Наблюдатель", "guest": "Гость",
}

# Глаголы действий (последний сегмент пути POST /things/{id}/<verb>).
VERBS = {
    "approve": "Подтвердить", "confirm": "Подтвердить", "accept": "Принять", "reject": "Отклонить", "decline": "Отклонить",
    "cancel": "Отменить", "close": "Закрыть", "reopen": "Открыть заново", "open": "Открыть", "finish": "Завершить",
    "complete": "Завершить", "stop": "Остановить", "start": "Запустить", "pause": "Приостановить", "resume": "Возобновить",
    "archive": "В архив", "restore": "Восстановить", "block": "Заблокировать", "unblock": "Разблокировать", "delete": "Удалить",
    "remove": "Удалить", "revoke": "Отозвать", "assign": "Назначить", "unassign": "Снять назначение", "submit": "Отправить",
    "publish": "Опубликовать", "extend": "Продлить", "renew": "Продлить", "pay": "Оплатить", "refund": "Вернуть оплату",
    "check": "Проверить", "verify": "Проверить", "retry": "Повторить", "reset": "Сбросить", "activate": "Активировать",
    "deactivate": "Деактивировать", "enable": "Включить", "disable": "Отключить", "issue": "Выдать", "return": "Вернуть",
    "checkin": "Отметить приход", "checkout": "Отметить уход", "export": "Выгрузить", "import": "Загрузить", "send": "Отправить",
}

CYR = re.compile(r"[А-Яа-яЁё]")
VERB_PREFIX = re.compile(r"^(получить|вернуть|загрузить|запросить|показать|просмотреть)\s+", re.I)


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def humanize(field: str) -> str:
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(field)).replace("_", " ").replace("-", " ").split()
    return " ".join(words).capitalize() if words else str(field)


def short_text(s: object, limit: int = 40) -> str | None:
    """Короткая русская подпись из title/description: первая фраза, без точки в конце, только кириллица."""
    if not isinstance(s, str):
        return None
    s = s.strip().splitlines()[0].strip() if s.strip() else ""
    s = re.split(r"(?<=[^.])\.\s", s)[0].rstrip(". ")
    if not s or len(s) > limit or not CYR.search(s):
        return None
    return s[:1].upper() + s[1:]


def annotated(schema: dict | None) -> str | None:
    """Подпись из аннотаций схемы/свойства (title, x-label, x-title, короткое description)."""
    if not isinstance(schema, dict):
        return None
    for k in ("x-label", "x-title", "title"):
        v = short_text(schema.get(k), 60)
        if v:
            return v
    return short_text(schema.get("description"))


def field_label(name: str, prop: dict | None = None) -> str:
    return annotated(prop) or FIELDS.get(_key(name)) or _compound(name) or humanize(name)


def _compound(name: str) -> str | None:
    """«ownerName» → «Владелец», «startedAt»-подобные → по основе; только если основа в словаре."""
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(name)).split()
    if len(words) < 2:
        return None
    head, tail = _key("".join(words[:-1])), words[-1].lower()
    if tail in ("name", "title", "label", "id") and head in FIELDS:
        return FIELDS[head]
    if tail in ("count", "total") and head in FIELDS:
        return f"{FIELDS[head]}: количество"
    return None


def enum_labels(prop: dict | None, values: list, glossary: dict[str, str] | None = None) -> dict[str, str]:
    """value → подпись: x-enum-labels (dict или list) / x-enumNames / x-enum-descriptions / oneOf[const,title] /
    глоссарий модели / словарь / значение как есть."""
    p = prop or {}
    out: dict[str, str] = {}
    lbl = p.get("x-enum-labels") or p.get("x-enumLabels")
    if isinstance(lbl, dict):
        out |= {str(k): str(v) for k, v in lbl.items()}
    for key in ("x-enum-labels", "x-enumLabels", "x-enumNames", "x-enum-names", "x-enum-descriptions", "x-enumDescriptions"):
        lst = p.get(key)
        if isinstance(lst, list) and len(lst) == len(values):
            for v, l in zip(values, lst):
                out.setdefault(str(v), str(l))
    for alt in p.get("oneOf") or p.get("anyOf") or []:
        if isinstance(alt, dict) and "const" in alt and (alt.get("title") or alt.get("description")):
            out.setdefault(str(alt["const"]), str(alt.get("title") or alt.get("description")))
    for v in values:
        out.setdefault(str(v), (glossary or {}).get(str(v)) or ENUMS.get(_key(v)) or str(v))
    return out


def clean_glossary(items: object) -> dict[str, str]:
    """[{term, label}] от модели → {term: label}; только короткие русские подписи."""
    out: dict[str, str] = {}
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and isinstance(it.get("term"), str):
            lbl = short_text(it.get("label"), 40)
            if lbl:
                out[it["term"].strip()] = lbl
    return out


def verb_label(verb: str, summary: str = "", glossary: dict[str, str] | None = None) -> str:
    s = short_text(summary, 30)
    if s and len(s.split()) <= 4:
        return s
    return (glossary or {}).get(verb) or VERBS.get(_key(verb)) or humanize(verb)


def title_from_summary(summary: str) -> str | None:
    """Заголовок экрана из summary операции: русский, без глагола-префикса («Получить список …»), не длиннее 4 слов."""
    s = short_text(summary, 40)
    if not s:
        return None
    s = VERB_PREFIX.sub("", s).strip()
    if not s or len(s.split()) > 4:
        return None
    return s[:1].upper() + s[1:]
