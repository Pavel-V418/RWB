"""Базовая спека экранов из OpenAPI — без LLM.

Роль в конвейере:
  1) черновик для UX-агента (LLM улучшает подписи, выбор колонок, фильтры, привязку к FR);
  2) страховка: если LLM не справилась с экраном за отведённые попытки, в спеку идёт базовый
     вариант этого экрана — сборка остаётся зелёной на любом кейсе.

Эвристики домен-независимы и опираются только на соглашения contracts/api/api-conventions.md.
"""
from __future__ import annotations

import re

from specforge_web.agents.common.api_digest import ApiDigest
from specforge_web.agents.ux_designer import labels as L
from specforge_web.agents.ux_designer import trace
from specforge_web.generators.angular.samples import from_pattern

SEVERITIES = ["info", "success", "warn", "danger", "secondary", "contrast"]
DANGER_VERBS = ("delete", "close", "finish", "cancel", "reject", "archive", "block", "stop", "remove", "revoke")
TEXT_HINTS = ("message", "title", "action", "description", "text", "comment", "note")
LABEL_HINTS = ("name", "title", "number", "code", "label", "plateNumber")
TIME_HINTS = ("occurredAt", "at", "createdAt", "timestamp", "time", "recordedAt")


def kebab(s: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", s)
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def humanize(field: str) -> str:
    """Русская подпись по имени (словарь типовых полей), иначе имя из API как есть."""
    return L.field_label(field)


def default_states(title: str = "Данных пока нет") -> dict:
    return {
        "loading": {"kind": "skeleton"},
        "empty": {"title": title},
        "error": {"title": "Не удалось загрузить данные", "retry": True},
        "forbidden": {"title": "Недостаточно прав для просмотра"},
        "ready": {},
    }


class Baseline:
    def __init__(self, openapi: dict, requirements: dict | None = None, platform: str = "web", glossary: dict[str, str] | None = None):
        self.glossary = dict(glossary or {})
        self.misses: set[str] = set()  # идентификаторы API без русской подписи → глоссарий от модели
        self.trace: dict[str, dict] = {}  # FR → экраны, уверенность привязки
        self.api = ApiDigest(openapi)
        self.gen = self.api.gen
        self.req = requirements or {}
        self.platform = platform
        self.screens: list[dict] = []
        self.resources: dict[str, dict] = {}
        self._collect()

    # ------------------------------------------------------------ resources
    def _collect(self) -> None:
        for m in self.gen.ops.values():
            parts = [p for p in m.path.strip("/").split("/") if p]
            if not parts:
                continue
            root = parts[0]
            r = self.resources.setdefault(root, {"root": root, "list": None, "get": None, "create": None, "nested": [], "actions": [],
                                                 "nested_create": [], "delete": None})
            has_param = any(p.startswith("{") for p in parts)
            if m.method == "get" and not has_param and m.response_kind in ("page", "array"):
                r["list"] = r["list"] or m
            elif m.method == "get" and len(parts) == 2 and has_param and m.response_kind == "object":
                r["get"] = m
            elif m.method == "post" and not has_param and m.body_type and m.response_kind != "none":
                r["create"] = m
            elif m.method == "get" and len(parts) == 3 and has_param and m.response_kind in ("page", "array"):
                r["nested"].append(m)
            elif m.method == "post" and len(parts) == 3 and has_param and m.body_type and self._has_body_fields(m):
                r["nested_create"].append(m)  # создание вложенной записи → форма, а не кнопка
            elif m.method in ("post", "put", "patch", "delete") and len(parts) == 3 and has_param:
                r["actions"].append(m)
            elif m.method == "delete" and len(parts) == 2 and has_param:
                r["delete"] = m

    def _has_body_fields(self, m) -> bool:
        return bool(m.body_type) and bool(self.api.props(m.body_type)) if m.body_type and m.body_type[0].isupper() else bool(m.body_type)

    # ------------------------------------------------------------ labels
    def raw_op(self, m) -> dict:
        return ((self.api.doc.get("paths") or {}).get(m.path) or {}).get(m.method) or {} if m else {}

    def tag_title(self, m) -> str | None:
        tags = self.raw_op(m).get("tags") or []
        if not tags:
            return None
        meta = next((t for t in self.api.doc.get("tags") or [] if isinstance(t, dict) and t.get("name") == tags[0]), {})
        return L.short_text(meta.get("x-displayName"), 40) or L.short_text(meta.get("description"), 40) or L.short_text(tags[0], 40)

    def raw_prop(self, item: str, field: str) -> dict:
        return ((self.api.schema(item).get("properties") or {}).get(field) or {}) if item else {}

    def _miss(self, term: str, label: str) -> str:
        if not L.CYR.search(label) and label not in ("ID",):
            self.misses.add(str(term))
        return label

    def flabel(self, item: str, field: str, resolved: dict | None = None) -> str:
        """Подпись поля: аннотации свойства (в т.ч. рядом с $ref) → аннотации типа → глоссарий → словарь → имя из API."""
        return L.annotated(self.raw_prop(item, field)) or L.annotated(resolved) or self.glossary.get(field) \
            or self._miss(field, L.field_label(field))

    def enum_names(self, prop: dict | None, values: list) -> dict[str, str]:
        names = L.enum_labels(prop, values, self.glossary)
        for v in values:
            self._miss(str(v), names[str(v)])
        return names

    def status_map(self, prop: dict, values: list) -> dict:
        names = self.enum_names(self.gen.resolve(prop), values)
        return {str(v): {"label": names[str(v)], "severity": SEVERITIES[i % len(SEVERITIES)]} for i, v in enumerate(values)}

    def plural_title(self, r) -> str:
        m = r["list"] or r["get"] or r["create"]
        item = (r["list"] or r["get"]).item_type if (r["list"] or r["get"]) else ""
        sch = self.api.schema(item) if item else {}
        return (L.short_text(self.raw_op(m).get("x-label"), 40) or self.tag_title(m) or L.short_text(sch.get("x-plural"), 40)
                or (L.title_from_summary(self.raw_op(r["list"]).get("summary", "")) if r["list"] else None)
                or self.glossary.get(r["root"]) or L.FIELDS.get(re.sub(r"[^a-z0-9]", "", r["root"].lower()))
                or self._miss(r["root"], L.humanize(r["root"].replace("-", " "))))

    def singular_title(self, r) -> str:
        item = (r["get"] or r["list"]).item_type if (r["get"] or r["list"]) else ""
        sch = self.api.schema(item) if item else {}
        if item and not L.annotated(sch) and item not in self.glossary:
            self.misses.add(item)
        return (L.annotated(sch) or (L.title_from_summary(self.raw_op(r["get"]).get("summary", "")) if r["get"] else None)
                or self.glossary.get(item) or self.plural_title(r))

    def roles_of(self, m) -> list[str]:
        roles = list(m.roles) if m and m.roles else []
        return roles or self.all_roles()

    def all_roles(self) -> list[str]:
        rs = [r["id"] for r in self.req.get("roles", [])] if self.req.get("roles") else []
        rs = rs or self.api.roles or ["user"]
        return [kebab(r) for r in rs]

    # ------------------------------------------------------------ field helpers
    def display_fields(self, item: str, limit: int = 6, skip: tuple = ("id",)) -> list[dict]:
        out = []
        for k, p in self.api.props(item).items():
            if k in skip or k.endswith("Id") and k != "id":
                continue
            kind = self.api.kind_of(p)
            if kind in ("geometry", "array", "object"):
                continue
            if limit <= 6 and kind == "text" and (self.gen.resolve(p).get("maxLength") or 0) > 200:
                continue  # длинный текст — не для колонки списка
            f = {"field": k, "label": self.flabel(item, k, p), "format": {"enum": "status", "datetime": "datetime", "date": "date",
                                                               "number": "number", "boolean": "boolean"}.get(kind, "text")}
            if kind == "enum":
                f["statusMap"] = self.status_map(p, self.api.enum_of(p))
            out.append(f)
            if len(out) >= limit:
                break
        return out

    def geometry_fields(self, item: str) -> list[tuple[str, str]]:
        """→ [(field, kind)], kind = polygon | point | line."""
        out = []
        for k, p in self.api.props(item).items():
            if self.api.is_geometry(p):
                t = self.gen.resolve(p).get("properties", {}).get("type", {})
                vals = [t.get("const")] + list(t.get("enum", []))
                kind = "polygon" if any(v and "Polygon" in v for v in vals) else "line" if "LineString" in vals else "point"
                out.append((k, kind))
        return out

    def label_field(self, item: str) -> str | None:
        props = self.api.props(item)
        for h in LABEL_HINTS:
            if h in props:
                return h
        for k, p in props.items():
            if k != "id" and self.api.kind_of(p) == "text":
                return k
        return None

    def is_timeline(self, item: str) -> tuple[str, str] | None:
        props = self.api.props(item)
        time = next((h for h in TIME_HINTS if h in props and self.api.kind_of(props[h]) in ("datetime", "date")), None)
        text = next((h for h in TEXT_HINTS if h in props), None)
        return (time, text) if time and text else None

    def query_filters(self, m) -> tuple[list[dict], dict | None]:
        filters, search = [], None
        params = set(m.query_params)
        for q in m.query_params:
            if q in ("page", "size", "sort"):
                continue
            if q == "q":
                search = {"param": "q", "placeholder": "Поиск"}
                continue
            ptype = m.param_types.get(q, "")
            enum_vals = re.findall(r'"([^"]+)"', ptype)
            psch = self.param_schema(m, q)
            if not enum_vals and ptype and ptype[0].isupper():
                psch = self.api.schema(ptype)
                enum_vals = [str(v) for v in psch.get("enum", [])]
            if enum_vals:
                names = self.enum_names(psch, enum_vals)
                label = L.annotated(self.raw_param(m, q)) or L.annotated(psch) or self.glossary.get(q) or self._miss(q, L.field_label(q))
                filters.append({"field": q, "label": label, "control": "select",
                                "options": [{"value": v, "label": names[str(v)]} for v in enum_vals]})
        if {"from", "to"} <= params:
            filters.append({"field": "period", "label": "Период", "control": "date-range", "param": "from,to"})
        return filters, search

    def raw_param(self, m, name: str) -> dict:
        path_item = (self.api.doc.get("paths") or {}).get(m.path) or {}
        for prm in list(path_item.get("parameters") or []) + list(self.raw_op(m).get("parameters") or []):
            prm = self.gen.resolve(prm) if isinstance(prm, dict) else {}
            if prm.get("name") == name:
                return prm
        return {}

    def param_schema(self, m, name: str) -> dict:
        return self.gen.resolve(self.raw_param(m, name).get("schema") or {})

    # ------------------------------------------------------------ screens
    def build(self) -> dict:
        web = self.platform == "web"
        if "login" in self.gen.ops:
            self.screens.append({
                "id": "login", "title": "Вход", "archetype": "login", "route": "/login", "roles": self.all_roles(), "public": True,
                "requirements": [], "states": default_states("—") | {"error": {"title": "Не удалось войти", "retry": False}},
                "form": {"fields": [{"name": "login", "label": "Логин", "control": "text", "required": True},
                                    {"name": "password", "label": "Пароль", "control": "password", "required": True}],
                         "submit": {"operationId": "login", "label": "Войти", "successMessage": "Вход выполнен", "idempotent": False}},
            })
        stats = [m for m in self.gen.ops.values() if m.method == "get" and not m.path_params and m.response_kind == "object"
                 and m.op_id not in ("getCurrentUser",) and self._numeric_props(m.item_type)]
        if stats:
            self.screens.append(self._dashboard(stats[0]))
        geo_lists = []
        for root, r in self.resources.items():
            if not r["list"]:
                continue
            item = r["list"].item_type
            if self.geometry_fields(item):
                geo_lists.append(r)
            tl = self.is_timeline(item)
            if tl and not r["get"]:
                self.screens.append(self._timeline(r, tl))
            else:
                self.screens.append(self._list(r))
            if r["create"]:
                self.screens.append(self._form(r))
            if r["get"]:
                self.screens.append(self._detail(r))
                if True:  # формы нужны и на мобильных: ввод данных «в поле»
                    for nc in r["nested_create"]:
                        sub = nc.path.rstrip("/").split("/")[-1]
                        self.screens.append(self._form(r, nc, sid=f"{kebab(r['root'])}-{kebab(sub)}-create",
                                                       route=f"/{r['root']}/:id/{sub}/new", back=f"{kebab(r['root'])}-detail",
                                                       params={"id": "route:id"}))
        if geo_lists and web:
            self.screens.insert(1 if self.screens and self.screens[0]["id"] == "login" else 0, self._map(geo_lists))
        if web:
            self.screens.append({"id": "forbidden", "title": "Нет доступа", "archetype": "forbidden", "route": "/forbidden",
                                 "roles": self.all_roles(), "public": True, "requirements": [], "states": default_states("—"),
                                 "actions": [{"id": "go-home", "label": "На главную", "kind": "primary",
                                              "navigateTo": next((s["id"] for s in self.screens if s["archetype"] not in ("login",)), "login")}]})
        self._dedupe_routes()
        self._assign_requirements()
        nav = [{"screen": s["id"], "label": s["title"][:30], "icon": self._icon(s)} for s in self.screens
               if s["archetype"] in ("dashboard", "map", "list", "timeline", "generic") and ":" not in s["route"]][: (7 if web else 5)]
        app = {"id": "admin" if web else "mobile", "platform": self.platform, "title": (self.req.get("product") or "Приложение")[:60],
               "locale": "ru-RU", "uiKit": "primeng@20" if web else "material3", "auth": {"required": True}}
        if any(s["id"] == "login" for s in self.screens):
            app["auth"]["loginScreen"] = "login"
        if any(s["id"] == "forbidden" for s in self.screens):
            app["auth"]["forbiddenScreen"] = "forbidden"
        roles = sorted({r for s in self.screens for r in s["roles"]})
        req_roles = {kebab(x["id"]): x.get("title") for x in self.req.get("roles", []) if isinstance(x, dict) and x.get("id")}
        return {"specVersion": "1.0", "app": app, "roles": [{"id": r, "title": req_roles.get(r) or L.ENUMS.get(re.sub(r"[^a-z0-9]", "", r)) or L.humanize(r)} for r in roles],
                "navigation": nav, "screens": self.screens}

    def _numeric_props(self, item: str) -> list[str]:
        return [k for k, p in self.api.props(item).items() if self.api.kind_of(p) == "number"]

    def _dashboard(self, m) -> dict:
        nums = self._numeric_props(m.item_type)[:8]
        span = max(3, 12 // max(1, min(len(nums), 4)))
        return {"id": "overview", "title": "Обзор", "archetype": "dashboard", "route": "/", "roles": self.roles_of(m), "requirements": [],
                "data": {"primary": {"operationId": m.op_id, "refreshSeconds": 30}},
                "dashboard": {"widgets": [{"id": kebab(n), "kind": "stat", "title": self.flabel(m.item_type, n), "source": "primary", "valueField": n, "span": span} for n in nums]},
                "states": default_states()}

    def _list(self, r) -> dict:
        m, item = r["list"], r["list"].item_type
        cols = self.display_fields(item)
        if r["get"] and cols:
            cols[0] = {**cols[0], "format": "link", "linkTo": f"{kebab(r['root'])}-detail", "linkParam": "id"}
            cols[0].pop("statusMap", None)
        filters, search = self.query_filters(m)
        lst: dict = {"columns": cols or [{"field": "id", "label": "ID"}]}
        if filters:
            lst["filters"] = filters
        if search:
            lst["search"] = search
        if r["get"]:
            lst["rowLink"], lst["rowLinkParam"] = f"{kebab(r['root'])}-detail", "id"
        s = {"id": f"{kebab(r['root'])}-list", "title": self.plural_title(r), "archetype": "list", "route": f"/{r['root']}",
             "roles": self.roles_of(m), "requirements": [],
             "data": {"primary": {"operationId": m.op_id, "pagination": "page" if {"page", "size"} <= set(m.query_params) else "none"}},
             "list": lst, "states": default_states()}
        if r["create"]:
            s["actions"] = [{"id": "create", "label": "Создать", "icon": "pi pi-plus", "kind": "primary", "navigateTo": f"{kebab(r['root'])}-create"}]
            s["states"]["empty"]["actionId"] = "create"
        return s

    def _timeline(self, r, tl) -> dict:
        m = r["list"]
        filters, _ = self.query_filters(m)
        props = self.api.props(m.item_type)
        t = {"titleField": "type" if "type" in props else tl[1], "timeField": tl[0]}
        if tl[1] != t["titleField"]:
            t["descriptionField"] = tl[1]
        if "severity" in props:
            t["severityField"] = "severity"
        if filters:
            t["filters"] = filters
        return {"id": f"{kebab(r['root'])}-log", "title": self.plural_title(r), "archetype": "timeline", "route": f"/{r['root']}",
                "roles": self.roles_of(m), "requirements": [],
                "data": {"primary": {"operationId": m.op_id, "pagination": "page" if {"page", "size"} <= set(m.query_params) else "none"}},
                "timeline": t, "states": default_states()}

    def _detail(self, r) -> dict:
        g, item = r["get"], r["get"].item_type
        sections: list[dict] = [{"title": "Сведения", "kind": "fields", "fields": self.display_fields(item, limit=12)}]
        if not sections[0]["fields"]:
            sections[0]["fields"] = [{"field": "id", "label": "ID"}]
        secondary: dict = {}
        geo = self.geometry_fields(item)
        if geo:
            f, kind = geo[0]
            sections.append({"title": "На карте", "kind": "map", "layers": [{"id": kebab(f), "source": "primary", "kind": kind, "geometryField": f}]})
        fk = re.sub(r"-([a-z])", lambda mt: mt.group(1).upper(), r["root"].rstrip("s")) + "Id"
        related = [(m, {"id": "route:id"}) for m in r["nested"]]
        for other in self.resources.values():
            lm = other["list"]
            if lm and lm is not r["list"] and fk in lm.query_params:
                related.append((lm, {fk: "route:id"}))
        for m, params in related[:3]:
            owner = next((rr for rr in self.resources.values() if rr["list"] is m), None)
            sec_title = self.plural_title(owner) if owner else (self.tag_title(m) or L.title_from_summary(self.raw_op(m).get("summary", "")))
            key = re.sub(r"[^A-Za-z0-9]", "", m.op_id.replace("list", "", 1))
            key = key[:1].lower() + key[1:] or "related"
            ds = {"operationId": m.op_id, "params": dict(params)}
            if "size" in m.query_params:
                ds["params"]["size"] = "const:20"
            secondary[key] = ds
            tl = self.is_timeline(m.item_type)
            if tl:
                props = self.api.props(m.item_type)
                tdef = {"titleField": "type" if "type" in props else tl[1], "timeField": tl[0]}
                if tl[1] != tdef["titleField"]:
                    tdef["descriptionField"] = tl[1]
                sections.append({"title": sec_title or humanize(key), "kind": "timeline", "source": key, "timeline": tdef, "emptyText": "Записей нет"})
            else:
                cols = self.display_fields(m.item_type, limit=5)
                sections.append({"title": sec_title or humanize(key), "kind": "table", "source": key, "columns": cols or [{"field": "id", "label": "ID"}], "emptyText": "Записей нет"})
        actions = []
        for a in r["actions"]:
            verb = a.path.rstrip("/").split("/")[-1]
            danger = verb.lower() in DANGER_VERBS or a.method == "delete"
            label = self._miss(verb, L.verb_label(verb, self.raw_op(a).get("summary", ""), self.glossary))
            act = {"id": kebab(verb), "label": label, "kind": "danger" if danger else "secondary", "operationId": a.op_id,
                   "params": {"id": "route:id"}, "successMessage": f"«{label}» — выполнено", "idempotent": a.idempotent}
            if danger:
                act["confirm"] = f"Подтвердите действие «{label}»"
            if a.roles:
                act["roles"] = [kebab(x) for x in a.roles]
            actions.append(act)
        for nc in r["nested_create"]:
            sub = nc.path.rstrip("/").split("/")[-1]
            actions.append({"id": f"add-{kebab(sub)}", "label": "Добавить", "icon": "pi pi-plus", "kind": "primary",
                            "navigateTo": f"{kebab(r['root'])}-{kebab(sub)}-create", "params": {"id": "route:id"}})
        if r["delete"]:
            d = r["delete"]
            act = {"id": "delete", "label": "Удалить", "icon": "pi pi-trash", "kind": "danger", "operationId": d.op_id,
                   "params": {"id": "route:id"}, "confirm": "Удалить запись? Действие нельзя отменить.", "successMessage": "Удалено",
                   "idempotent": d.idempotent}
            if r["list"]:
                act["navigateTo"] = f"{kebab(r['root'])}-list"
            if d.roles:
                act["roles"] = [kebab(x) for x in d.roles]
            actions.append(act)
        label = self.label_field(item)
        detail: dict = {"sections": sections}
        if label:
            detail["titleField"] = label
        s = {"id": f"{kebab(r['root'])}-detail", "title": self.singular_title(r), "archetype": "detail", "route": f"/{r['root']}/:id",
             "roles": self.roles_of(g), "requirements": [],
             "data": {"primary": {"operationId": g.op_id, "params": {"id": "route:id"}}} | ({"secondary": secondary} if secondary else {}),
             "detail": detail, "states": default_states("Запись не найдена")}
        if actions:
            s["actions"] = actions
        return s

    def _form(self, r, c=None, sid: str | None = None, route: str | None = None, back: str | None = None, params: dict | None = None) -> dict:
        c = c or r["create"]
        body = c.body_type or ""
        props = self.api.props(body)
        req = self.api.required(body)
        fields = []
        for k, p in props.items():
            kind = self.api.kind_of(p)
            f: dict = {"name": k, "label": self.flabel(body, k, p), "required": k in req}
            rp = self.gen.resolve(p)
            v = {}
            for src, dst in (("pattern", "pattern"), ("minLength", "minLength"), ("maxLength", "maxLength"), ("minimum", "min"), ("maximum", "max")):
                if src in rp:
                    v[dst] = rp[src]
            if "pattern" in v:
                # «Неверный формат» пользователю не помогает — показываем пример правильного значения
                ex = next((x for x in (rp.get("examples") or []) if isinstance(x, str)), None) or (rp.get("example") if isinstance(rp.get("example"), str) else None)
                ex = ex or from_pattern(v["pattern"])
                if ex:
                    v["patternMessage"] = f"Неверный формат. Пример: {ex}"
            if kind == "enum":
                vals = self.api.enum_of(p)
                names = self.enum_names(self.gen.resolve(p), vals)
                f |= {"control": "select", "options": [{"value": x, "label": names[str(x)]} for x in vals]}
            elif kind == "number":
                f["control"] = "number"
            elif kind == "boolean":
                f["control"] = "toggle"
            elif kind == "date":
                f["control"] = "date"
            elif kind == "datetime":
                f["control"] = "datetime"
            elif kind in ("geometry", "array", "object"):
                continue
            else:
                f["control"] = "textarea" if rp.get("maxLength", 0) > 255 else "text"
                ref_root = None
                if k.endswith("Id"):
                    base = k[:-2]
                    ref_root = next((rr for rr in self.resources.values() if rr["list"] and rr["root"].replace("-", "").rstrip("s").lower() == base.lower()), None)
                if ref_root:
                    lm = ref_root["list"]
                    if not (L.annotated(self.raw_prop(body, k)) or self.glossary.get(k)):
                        f["label"] = self.singular_title(ref_root)
                    f |= {"control": "select", "optionsFrom": {"operationId": lm.op_id, "params": {"size": "const:100"} if "size" in lm.query_params else {}},
                          "optionLabel": self.label_field(lm.item_type) or "id", "optionValue": "id"}
                    v = {}
            if v:
                f["validation"] = v
            fields.append(f)
        back = back or f"{kebab(r['root'])}-list"
        submit = {"operationId": c.op_id, "label": "Сохранить", "successMessage": "Сохранено", "navigateTo": back, "idempotent": True}
        cancel = {"id": "cancel", "label": "Отмена", "kind": "secondary", "navigateTo": back}
        if params:
            submit["params"] = dict(params)
            cancel["params"] = dict(params)
        what = self.plural_title(r)
        title = L.title_from_summary(self.raw_op(c).get("summary", "")) or (f"{what}: новая запись" if L.CYR.search(what) else "Новая запись")
        return {"id": sid or f"{kebab(r['root'])}-create", "title": title, "archetype": "form", "route": route or f"/{r['root']}/new",
                "roles": self.roles_of(c), "requirements": [],
                "form": {"mode": "create", "fields": fields or [{"name": "name", "label": "Название", "control": "text"}], "submit": submit},
                "actions": [cancel],
                "states": default_states("—") | {"error": {"title": "Не удалось сохранить", "retry": False}}}

    def _map(self, geo_lists: list[dict]) -> dict:
        primary, rest = geo_lists[0], geo_lists[1:3]
        data: dict = {"primary": {"operationId": primary["list"].op_id}}
        if "size" in primary["list"].query_params:
            data["primary"]["params"] = {"size": "const:100"}
        layers = []
        for i, r in enumerate([primary] + rest):
            key = "primary" if i == 0 else re.sub(r"[^A-Za-z0-9]", "", r["root"])
            if i:
                data.setdefault("secondary", {})[key] = {"operationId": r["list"].op_id} | ({"params": {"size": "const:100"}} if "size" in r["list"].query_params else {})
            for f, kind in self.geometry_fields(r["list"].item_type)[:1]:
                layer = {"id": kebab(r["root"]), "source": key, "kind": kind, "geometryField": f}
                lbl = self.label_field(r["list"].item_type)
                if lbl:
                    layer["labelField"] = lbl
                if r["get"]:
                    layer["linkTo"] = f"{kebab(r['root'])}-detail"
                layers.append(layer)
        return {"id": "map", "title": "Карта", "archetype": "map", "route": "/map", "roles": self.roles_of(primary["list"]),
                "requirements": [], "data": data, "map": {"fitToData": True, "layers": layers}, "states": default_states()}

    def _dedupe_routes(self) -> None:
        seen = set()
        for s in self.screens:
            base, n = s["route"], 2
            while s["route"] in seen:
                s["route"] = f"{base}-{n}" if base != "/" else f"/home-{n}"
                n += 1
            seen.add(s["route"])

    def _assign_requirements(self) -> None:
        """Привязка FR к экранам по смыслу (agents/ux_designer/trace.py); неуверенные помечаются в self.trace."""
        nav = {s["id"]: s["title"] for s in self.screens}
        self.trace = trace.assign(self.req, self.screens, self.platform, nav)

    @staticmethod
    def _icon(s: dict) -> str:
        return {"dashboard": "pi pi-home", "map": "pi pi-map", "timeline": "pi pi-history", "generic": "pi pi-th-large"}.get(s["archetype"], "pi pi-list")


def baseline_spec(openapi: dict, requirements: dict | None = None, platform: str = "web") -> dict:
    return Baseline(openapi, requirements, platform).build()
