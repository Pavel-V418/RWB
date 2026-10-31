"""Шлюз этапа 2: согласованность требований, доменной модели, API, событий и БД. Без LLM."""

from __future__ import annotations

import re

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ..contract import Issue
from ..render.contracts import Model, request_fields, reserved_columns
from .pgcheck import DdlCheck

SPECIAL_ROLES = {"anonymous", "system"}

# регламент: в путях только существительные, действие задаёт HTTP-метод
PATH_VERBS = {"get", "create", "add", "update", "edit", "delete", "remove", "set", "send", "start", "stop", "close",
              "open", "extend", "play", "run", "import", "export", "upload", "download", "login", "logout",
              "signin", "signout", "register", "read", "mark", "cancel", "confirm", "approve", "reject", "submit",
              "sync", "activate", "deactivate", "reset", "assign", "check", "validate", "calculate", "process",
              "execute", "save", "load", "fetch", "find", "list", "search", "publish", "notify", "refresh"}


# конструкции Python/PCRE, которых нет в ECMA-262 (диалект pattern в JSON Schema и OpenAPI)
_NON_ECMA = [(re.compile(r"\(\?P[<=>]"), "(?P<…>)"), (re.compile(r"(?<!\\)\\[AZz]"), "\\A/\\Z"),
             (re.compile(r"\(\?[aiLmsux]+[):]"), "inline-флаги (?i)"), (re.compile(r"\(\?#"), "комментарии (?#…)"),
             (re.compile(r"\[\[:[a-z]+:\]\]"), "POSIX-классы [[:alpha:]]"), (re.compile(r"\(\?>"), "атомарные группы"),
             (re.compile(r"[*+?}]\+"), "possessive-кванторы")]


def non_ecma(pattern: str) -> list[str]:
    return [name for rx, name in _NON_ECMA if rx.search(pattern)]


def path_verbs(path: str) -> list[str]:
    bad = []
    for seg in path.split("/"):
        if not seg or seg.startswith("{") or re.fullmatch(r"v\d+|api", seg):
            continue
        first = re.split(r"[-_]", seg)[0].lower()
        if first in PATH_VERBS:
            bad.append(seg)
    return bad


_PATH_RE = re.compile(r"^/api/v\d+(/[a-z0-9\-]+|/\{[A-Za-z0-9_]+\})+$")


# глагол в пути -> существительное (действие задаёт HTTP-метод); None — убрать сегмент (поиск = GET коллекции)
VERB_TO_NOUN = {
    "export": "exports", "import": "imports", "upload": "files", "download": "file", "login": "tokens",
    "signin": "tokens", "logout": "tokens", "signout": "tokens", "cancel": "cancellation",
    "confirm": "confirmation", "approve": "approval", "reject": "rejection", "submit": "submission",
    "assign": "assignment", "extend": "extensions", "close": "closure", "activate": "activation",
    "deactivate": "deactivation", "calculate": "calculation", "validate": "validation", "check": "checks",
    "sync": "synchronization", "publish": "publication", "notify": "notifications",
    "register": "registrations", "reset": "resets", "process": "processing", "execute": "executions",
    "search": None, "find": None, "list": None, "get": None, "fetch": None, "load": None,
    # действие уже выражено HTTP-методом — сегмент убирается (только последний сегмент пути)
    "create": None, "add": None, "update": None, "edit": None, "delete": None, "remove": None,
    "save": None, "set": None,
    # действие над ресурсом -> подресурс-существительное
    "start": "starts", "stop": "stops", "run": "runs", "play": "runs", "send": "sendings",
    "mark": "marks", "open": "openings", "read": "reads", "refresh": "refreshes",
    "download": "file", "upload": "files",
}

# метод, который подразумевает убираемый глагол (POST /orders/create -> POST /orders)
_DROP_METHODS = {"get": {"get"}, "search": {"get"}, "find": {"get"}, "list": {"get"}, "fetch": {"get"},
                 "load": {"get"}, "create": {"post"}, "add": {"post"}, "update": {"put", "patch"},
                 "edit": {"put", "patch"}, "save": {"put", "patch", "post"}, "set": {"put", "patch"},
                 "delete": {"delete"}, "remove": {"delete"}}


def fix_path_verbs(m: Model) -> list[Issue]:
    fixes: list[Issue] = []
    taken = {(o.method, o.path) for o in m.design.operations}
    for op in m.design.operations:
        if not path_verbs(op.path):
            continue
        segs = op.path.split("/")
        new = []
        ok = True
        for pos, seg in enumerate(segs):
            parts = re.split(r"([-_])", seg)
            head = parts[0].lower() if seg and not seg.startswith("{") else ""
            if head in PATH_VERBS:
                if head not in VERB_TO_NOUN:
                    ok = False
                    break
                noun = VERB_TO_NOUN[head]
                if noun is None:
                    if pos != len(segs) - 1 or op.method not in _DROP_METHODS.get(head, {"get"}):
                        ok = False
                        break
                    continue
                seg = noun + "".join(parts[1:])
            new.append(seg)
        path = "/".join(new)
        if not ok or path_verbs(path) or (op.method, path) in taken:
            continue
        taken.discard((op.method, op.path))
        taken.add((op.method, path))
        fixes.append(Issue("W_PATH_RENAMED", "warning", f"{op.path} -> {path}", op.operation_id))
        op.path = path
    return fixes


def _norm_role(r: str) -> str:
    r = r.strip().lower().removeprefix("role-").replace("-", "_").replace(" ", "_")
    return r[:-1] if r.endswith("s") and len(r) > 3 else r


_ROLE_SYNONYMS = {"admin": "administrator", "administrator": "admin", "anon": "anonymous", "guest": "anonymous",
                  "public": "anonymous", "service": "system", "scheduler": "system", "worker": "system"}


def _role_aliases(m: Model) -> dict[str, str]:
    """Нормализованное имя/синоним -> ключ роли из требований."""
    out: dict[str, str] = {}
    for r in list(m.req.roles):
        for a in (r.key, r.id, r.name):
            out[_norm_role(a)] = r.key
    for k in SPECIAL_ROLES:
        out[k] = k
    for syn, target in _ROLE_SYNONYMS.items():
        t = _norm_role(target)
        if t in out and syn not in out:
            out[syn] = out[t]
    return out


def autofix(m: Model) -> list[Issue]:
    fixes: list[Issue] = fix_path_verbs(m)
    # 503 — недоступность нашего сервиса; ошибка внешнего поставщика — 502
    for op in m.design.operations:
        if 503 in op.errors:
            op.errors = sorted((set(op.errors) - {503}) | {502})
        op.exceptions = [e.replace("→ 503", "→ 502") for e in op.exceptions]
    fr_ids = {f.id for f in m.req.functional}
    roles = {r.key for r in m.req.roles} | SPECIAL_ROLES

    for e in m.domain.entities:
        for f in e.fields:
            if f.ref and f.ref not in m.entities:
                fixes.append(Issue("W_FK_DROPPED", "warning", f"ссылка на неизвестную сущность {f.ref}", f"{e.name}.{f.name}"))
                f.ref = None
        e.fr = [x for x in e.fr if x in fr_ids]

    alias = _role_aliases(m)
    for op in m.design.operations:
        mapped = []
        for r in op.roles:
            key = r if r in roles else alias.get(_norm_role(r))
            if key and key != r:
                fixes.append(Issue("W_ROLE_MAPPED", "warning", f"роль {r!r} -> {key!r}", op.operation_id))
            if key:
                mapped.append(key)
            else:
                fixes.append(Issue("W_ROLE_DROPPED", "warning", f"удалена неизвестная роль {r!r}", op.operation_id))
        op.roles = list(dict.fromkeys(mapped))
        if not op.roles:
            # роли из акторов связанных требований; иначе — ни одной роли не будет и шлюз упадёт
            fr_roles = [a.removeprefix("ROLE-") for f in m.req.functional if f.id in op.fr for a in f.actors]
            fr_roles = [r for r in dict.fromkeys(fr_roles) if r in roles]
            if fr_roles:
                op.roles = fr_roles
                fixes.append(Issue("W_ROLE_FROM_FR", "warning",
                                   f"роли взяты из акторов требований {op.fr}: {fr_roles}", op.operation_id))
        bad = [x for x in op.fr if x not in fr_ids]
        if bad:
            op.fr = [x for x in op.fr if x in fr_ids]
            fixes.append(Issue("W_REF_DROPPED", "warning", f"удалены неизвестные FR {bad}", op.operation_id))
        for part, owner in ((op.request, "request"), (op.response, "response")):
            if part is None or not part.entity:
                continue
            if part.entity not in m.entities:
                fixes.append(Issue("W_ENTITY_DROPPED", "warning", f"{owner}: неизвестная сущность {part.entity}",
                                   op.operation_id))
                part.entity, part.fields = None, []
                continue
            bad = [n for n in part.fields if not m.field(part.entity, n)]
            if bad:
                part.fields = [n for n in part.fields if m.field(part.entity, n)]
                fixes.append(Issue("W_FIELD_DROPPED", "warning", f"{owner}: нет полей {bad} в {part.entity}",
                                   op.operation_id))
        # GET/DELETE не несут тела; пустое тело у остальных методов — тоже убираем, а не валим шлюз
        if op.request and (op.method in ("get", "delete") or not request_fields(m, op)):
            if op.method not in ("get", "delete"):
                fixes.append(Issue("W_EMPTY_BODY_DROPPED", "warning", "тело запроса без полей удалено",
                                   op.operation_id))
            op.request = None
    for ev in m.design.events:
        ev.fr = [x for x in ev.fr if x in fr_ids]
        if ev.entity and ev.entity not in m.entities:
            fixes.append(Issue("W_ENTITY_DROPPED", "warning", f"неизвестная сущность {ev.entity}", ev.name))
            ev.entity, ev.fields = None, []
        elif ev.entity:
            bad = [n for n in ev.fields if not m.field(ev.entity, n)]
            if bad:
                ev.fields = [n for n in ev.fields if m.field(ev.entity, n)]
                fixes.append(Issue("W_FIELD_DROPPED", "warning", f"нет полей {bad} в {ev.entity}", ev.name))
    m.design.no_api = [n for n in m.design.no_api if n.fr in fr_ids]
    return fixes


def coverage_map(m: Model) -> dict[str, dict[str, list[str]]]:
    out = {f.id: {"api": [], "events": [], "no_api": []} for f in m.req.functional}
    for op in m.design.operations:
        for x in op.fr:
            out.setdefault(x, {"api": [], "events": [], "no_api": []})["api"].append(
                f"{op.method.upper()} {op.path}")
    for ev in m.design.events:
        for x in ev.fr:
            out.setdefault(x, {"api": [], "events": [], "no_api": []})["events"].append(ev.name)
    for n in m.design.no_api:
        out.setdefault(n.fr, {"api": [], "events": [], "no_api": []})["no_api"].append(n.reason)
    return out


def unmapped_fr(m: Model) -> list[str]:
    cov = coverage_map(m)
    return [f.id for f in m.req.functional
            if f.priority == "must" and not (cov[f.id]["api"] or cov[f.id]["events"] or cov[f.id]["no_api"])]


def _walk_refs(node, out: list[str]):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                out.append(v)
            else:
                _walk_refs(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_refs(v, out)


def _resolve(doc: dict, ref: str) -> bool:
    if not ref.startswith("#/"):
        return False
    node = doc
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def check_openapi(doc: dict) -> list[Issue]:
    issues: list[Issue] = []
    refs: list[str] = []
    _walk_refs(doc, refs)
    for r in sorted(set(refs)):
        if not _resolve(doc, r):
            issues.append(Issue("E_OPENAPI_REF", "error", f"неразрешимая ссылка {r}"))
    seen: set[str] = set()
    for path, item in doc.get("paths", {}).items():
        declared_tpl = set(re.findall(r"\{([^}]+)\}", path))
        for method, op in item.items():
            oid = op.get("operationId")
            if oid in seen:
                issues.append(Issue("E_OPENAPI_OPID", "error", "повторяющийся operationId", oid))
            seen.add(oid)
            params = {p.get("name") for p in op.get("parameters", []) if p.get("in") == "path"}
            if declared_tpl != params:
                issues.append(Issue("E_OPENAPI_PARAM", "error",
                                    f"параметры пути {sorted(declared_tpl)} не совпадают с объявленными {sorted(params)}",
                                    oid))
            if not op.get("responses"):
                issues.append(Issue("E_OPENAPI_RESP", "error", "нет ответов", oid))
    for name, schema in doc.get("components", {}).get("schemas", {}).items():
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            issues.append(Issue("E_JSON_SCHEMA", "error", str(exc.message)[:300], name))
    return issues


def check_asyncapi(doc: dict | None) -> list[Issue]:
    if not doc:
        return []
    issues: list[Issue] = []
    refs: list[str] = []
    _walk_refs(doc, refs)
    for r in sorted(set(refs)):
        if not _resolve(doc, r):
            issues.append(Issue("E_ASYNCAPI_REF", "error", f"неразрешимая ссылка {r}"))
    for name, schema in doc.get("components", {}).get("schemas", {}).items():
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            issues.append(Issue("E_JSON_SCHEMA", "error", str(exc.message)[:300], name))
    return issues


def check(m: Model, openapi: dict, asyncapi: dict | None, ddl_check: DdlCheck | None) -> list[Issue]:
    issues: list[Issue] = []

    def err(code, msg, target=None):
        issues.append(Issue(code, "error", msg, target))

    def warn(code, msg, target=None):
        issues.append(Issue(code, "warning", msg, target))

    # --- доменная модель
    for e in m.domain.entities:
        names = [f.name for f in e.fields]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            err("E_DUP_FIELD", f"повторяющиеся поля {sorted(dup)}", e.name)
        for f in e.fields:
            if f.type == "enum" and not f.enum_values:
                err("E_ENUM_EMPTY", "enum без значений", f"{e.name}.{f.name}")
            if f.pattern:
                try:
                    re.compile(f.pattern)
                except re.error as exc:
                    err("E_REGEX", f"некорректное регулярное выражение: {exc}", f"{e.name}.{f.name}")
                else:
                    bad = non_ecma(f.pattern)
                    if bad:
                        err("E_REGEX", f"конструкции {bad} не поддерживаются в JSON Schema (ECMA-262): "
                                       "валидаторы в браузере и на backend поведут себя иначе", f"{e.name}.{f.name}")
            if f.pattern and f.example is not None:
                try:
                    if not re.fullmatch(f.pattern.lstrip("^").rstrip("$"), f.example):
                        warn("W_EXAMPLE_PATTERN", f"пример {f.example!r} не проходит pattern", f"{e.name}.{f.name}")
                except re.error:
                    pass
            if f.name.endswith("Id") and f.type == "uuid" and not f.ref and f.name != "id":
                warn("W_FK_MISSING", "поле похоже на внешний ключ, но ref не задан", f"{e.name}.{f.name}")
    for sm in m.domain.state_machines:
        if sm.entity not in m.entities:
            err("E_SM_ENTITY", "неизвестная сущность", sm.entity)
            continue
        f = m.field(sm.entity, sm.field)
        if not f or f.type != "enum":
            err("E_SM_FIELD", "поле состояния не найдено или не enum", f"{sm.entity}.{sm.field}")
        elif set(sm.states) - set(f.enum_values):
            err("E_SM_STATES", f"состояния {sorted(set(sm.states) - set(f.enum_values))} нет в enum",
                f"{sm.entity}.{sm.field}")

    # --- покрытие требований
    for fr in unmapped_fr(m):
        err("E_FR_UNMAPPED", "обязательное требование не связано ни с операцией API, ни с событием, "
                             "и нет обоснования no_api", fr)

    # --- операции
    keys: set[tuple[str, str]] = set()
    ids: set[str] = set()
    for op in m.design.operations:
        if op.operation_id in ids:
            err("E_DUP_OPID", "повторяющийся operationId", op.operation_id)
        ids.add(op.operation_id)
        k = (op.method, op.path)
        if k in keys:
            err("E_DUP_ROUTE", f"повторяющийся маршрут {op.method.upper()} {op.path}", op.operation_id)
        keys.add(k)
        if not _PATH_RE.match(op.path):
            err("E_PATH_FORMAT", "путь должен начинаться с /api/v1 и быть в kebab-case", op.path)
        verbs = path_verbs(op.path)
        if verbs:
            err("E_PATH_VERB", f"глагол в пути {verbs}: по регламенту действие задаёт HTTP-метод, "
                               "в пути — существительные", f"{op.method.upper()} {op.path}")
        if not op.algorithm:
            warn("W_OP_NO_ALGORITHM", "нет описания алгоритма работы метода (шаблон API)", op.operation_id)
        if not op.roles:
            err("E_OP_NO_ROLE", "у операции нет ролей", op.operation_id)
        if not op.fr:
            warn("W_OP_NO_FR", "операция не связана с требованием", op.operation_id)
        if op.method in ("put", "patch") and not op.request:
            warn("W_NO_BODY", "изменяющая операция без тела запроса", op.operation_id)
        if op.method == "post" and op.success_status == 201 and not op.idempotent:
            warn("W_NOT_IDEMPOTENT", "создание ресурса без Idempotency-Key", op.operation_id)
        if op.request and not request_fields(m, op):
            err("E_EMPTY_REQUEST", "тело запроса без полей", op.operation_id)

    used = {op.request.entity for op in m.design.operations if op.request and op.request.entity}
    used |= {op.response.entity for op in m.design.operations if op.response.entity}
    used |= {ev.entity for ev in m.design.events if ev.entity}
    for e in m.domain.entities:
        if e.name not in used:
            warn("W_ENTITY_NO_API", "сущность не видна ни в API, ни в событиях", e.name)

    # --- события
    if m.req.profile.needs_async_events and not m.design.events:
        err("E_NO_EVENTS", "профиль кейса требует событий, а модель событий пуста")
    for ev in m.design.events:
        if not ev.consumers:
            warn("W_EVENT_NO_CONSUMER", "у события нет потребителей", ev.name)

    # --- контракты и БД
    for ent, col in reserved_columns(m):
        warn("W_DDL_RESERVED_NAME", f"имя {col!r} — зарезервированное слово PostgreSQL, в DDL взято в кавычки; "
                                    "лучше переименовать поле в доменной модели", ent)
    issues += check_openapi(openapi)
    issues += check_asyncapi(asyncapi)
    if ddl_check:
        if not ddl_check.ran:
            warn("W_DDL_SKIPPED", ddl_check.message)
        elif not ddl_check.ok:
            err("E_DDL", ddl_check.message)
        elif ddl_check.mode == "local-no-postgis":
            warn("W_DDL_NO_POSTGIS", "DDL проверен во временном PostgreSQL без PostGIS (геометрия заменена на bytea); "
                                     "для полной проверки задайте SPECFORGE_PG_DSN")
    return issues
