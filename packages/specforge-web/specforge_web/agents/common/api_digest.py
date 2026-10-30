"""Компактное представление OpenAPI для промптов (в 5–10 раз меньше токенов, чем YAML).

  GET  listThings(page?, size?, q?, status?) → Page<Thing>  [admin, operator]
  Thing { id*: str, code*: str /^[A-Z]{3}$/, status*: new|done, location?: GeoPoint (GeoJSON) }
"""
from __future__ import annotations

from specforge_web.generators.angular.openapi_ts import TsGen, pascal

GEO_HINTS = ("Point", "Polygon", "LineString", "MultiPolygon")


class ApiDigest:
    def __init__(self, openapi: dict):
        self.gen = TsGen(openapi)
        self.doc = openapi

    # ------------------------------------------------------------ schema helpers
    def schema(self, name: str) -> dict:
        """Схема по имени; allOf раскрывается в один объект (properties + required)."""
        return self.flatten(self.gen.schemas.get(name) or self.gen.schemas.get(name[:1].lower() + name[1:]) or {})

    def flatten(self, s, depth: int = 0) -> dict:
        s = self.gen.resolve(s) if isinstance(s, dict) else {}
        if "allOf" not in s or depth > 8:
            return s
        props: dict = dict(s.get("properties") or {})
        req: list = list(s.get("required") or [])
        for sub in s["allOf"]:
            f = self.flatten(sub, depth + 1)
            props.update(f.get("properties") or {})
            req += [r for r in f.get("required", []) if r not in req]
        out = {k: v for k, v in s.items() if k != "allOf"}
        return out | {"type": "object", "properties": props, "required": req}

    def props(self, name: str) -> dict[str, dict]:
        s = self.schema(name)
        return {k: self.flatten(v) if isinstance(v, dict) else {} for k, v in (s.get("properties") or {}).items()}

    def required(self, name: str) -> set[str]:
        return set(self.schema(name).get("required", []))

    def is_geometry(self, prop: dict) -> bool:
        p = self.gen.resolve(prop)
        t = p.get("properties", {}).get("type", {})
        vals = [t.get("const")] + list(t.get("enum", []))
        return any(v in GEO_HINTS for v in vals if v)

    def enum_of(self, prop: dict) -> list:
        p = self.gen.resolve(prop)
        return list(p.get("enum", []))

    def kind_of(self, prop: dict) -> str:
        """Человекочитаемый вид поля → подсказка формата отображения."""
        p = self.gen.resolve(prop)
        if self.is_geometry(p):
            return "geometry"
        if p.get("enum"):
            return "enum"
        t = p.get("type")
        t = [x for x in t if x != "null"][0] if isinstance(t, list) and len([x for x in t if x != "null"]) else t
        fmt = p.get("format")
        if t == "string" and fmt == "date-time":
            return "datetime"
        if t == "string" and fmt == "date":
            return "date"
        if t in ("integer", "number"):
            return "number"
        if t == "boolean":
            return "boolean"
        if t == "string":
            return "text"
        if t == "array":
            return "array"
        return "object"

    # ------------------------------------------------------------ text digest
    def _type_short(self, prop: dict) -> str:
        p = prop
        name = self.gen.ref_name(p)
        if name:
            ref = self.schema(name)
            if ref.get("enum"):
                return "|".join(map(str, ref["enum"]))
            return name
        p = self.gen.resolve(p)
        if p.get("enum"):
            return "|".join(map(str, p["enum"]))
        t = self.gen.ts_type(p)
        return t.replace(" | null", "?").replace("string", "str").replace("number", "num")

    def schema_line(self, name: str) -> str:
        s = self.schema(name)
        if s.get("enum"):
            return f"{name} = {'|'.join(map(str, s['enum']))}"
        req = self.required(name)
        parts = []
        for k, v in (s.get("properties") or {}).items():
            v = v if isinstance(v, dict) else {}
            rv = self.gen.resolve(v)
            extra = ""
            if rv.get("pattern"):
                extra += f" /{rv['pattern']}/"
            if rv.get("maxLength"):
                extra += f" ≤{rv['maxLength']}"
            if self.is_geometry(v):
                extra += " (GeoJSON)"
            if rv.get("format") in ("date-time", "date"):
                extra += f" ({rv['format']})"
            parts.append(f"{k}{'*' if k in req else '?'}: {self._type_short(v)}{extra}")
        return f"{name} {{ {', '.join(parts)} }}"

    def op_line(self, op_id: str) -> str:
        m = self.gen.ops[op_id]
        params = []
        for n in m.path_params + [q for q in m.query_params if q not in m.path_params]:
            t = m.param_types.get(n, "string")
            enum = t if "\"" in t else ""
            params.append(f"{n}{'' if n in m.required_params else '?'}" + (f": {enum.replace(chr(34), '').replace(' | ', '|')}" if enum else ""))
        body = f" body: {m.body_type}" if m.body_type else ""
        resp = {"page": f"Page<{m.item_type}>", "array": f"{m.item_type}[]", "object": m.response_type, "none": "—"}[m.response_kind]
        flags = []
        if m.roles:
            flags.append(",".join(m.roles))
        if m.idempotent:
            flags.append("idempotent")
        return f"{m.method.upper():6} {m.op_id}({', '.join(params)}){body} → {resp}" + (f"  [{'; '.join(flags)}]" if flags else "")

    def text(self, ops: list[str] | None = None) -> str:
        ops = ops or list(self.gen.ops)
        used: set[str] = set()
        for o in ops:
            m = self.gen.ops[o]
            for t in (m.item_type, m.response_type, m.body_type or ""):
                used |= {x for x in self._names(t)}
        # транзитивно: вложенные $ref
        frontier = list(used)
        while frontier:
            n = frontier.pop()
            for v in (self.schema(n).get("properties") or {}).values():
                r = self.gen.ref_name(v) if isinstance(v, dict) else None
                items = self.gen.ref_name(self.gen.resolve(v).get("items", {})) if isinstance(v, dict) else None
                for x in (r, items):
                    if x and x not in used and x in {pascal(k) for k in self.gen.schemas}:
                        used.add(x)
                        frontier.append(x)
        lines = ["## Операции"] + [self.op_line(o) for o in ops] + ["", "## Схемы (* — обязательное)"]
        lines += [self.schema_line(n) for n in sorted(used) if n not in ("Problem",) and not n.endswith("Page")]
        return "\n".join(lines)

    def _names(self, t: str) -> set[str]:
        import re
        known = {pascal(k) for k in self.gen.schemas}
        return {x for x in re.findall(r"\b[A-Z][A-Za-z0-9]*\b", t) if x in known}

    @property
    def roles(self) -> list[str]:
        out: list[str] = []
        for m in self.gen.ops.values():
            for r in m.roles:
                if r not in out:
                    out.append(r)
        return out
