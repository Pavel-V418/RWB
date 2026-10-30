"""OpenAPI 3.1 → TypeScript-модели и ApiService (Angular HttpClient). Без LLM.

Имена методов = operationId (contracts/api/api-conventions.md §2), поэтому экраны и UX-спека
ссылаются на один и тот же идентификатор.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
IDENT = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def pascal(name: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", name)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def prop_key(name: str) -> str:
    return name if IDENT.match(name) else json.dumps(name)


@dataclass
class OpMeta:
    op_id: str
    method: str
    path: str
    summary: str
    path_params: list[str]
    query_params: list[str]
    required_params: list[str]
    param_types: dict[str, str]
    body_type: str | None
    body_kind: str | None  # json | multipart
    response_type: str
    response_kind: str  # page | array | object | none
    item_type: str
    idempotent: bool
    roles: list[str] = field(default_factory=list)

    @property
    def params_type(self) -> str:
        return f"{pascal(self.op_id)}Params"


class TsGen:
    def __init__(self, doc: dict):
        self.doc = doc
        self.schemas: dict = (doc.get("components") or {}).get("schemas") or {}
        self.ops: dict[str, OpMeta] = {}
        self._collect_ops()

    # ---------------------------------------------------------------- refs & types
    def resolve(self, node):
        seen = 0
        while isinstance(node, dict) and "$ref" in node and seen < 20:
            cur = self.doc
            for part in node["$ref"][2:].split("/"):
                cur = cur.get(part, {})
            node, seen = cur, seen + 1
        return node

    def ref_name(self, node) -> str | None:
        if isinstance(node, dict) and isinstance(node.get("$ref"), str) and node["$ref"].startswith("#/components/schemas/"):
            return pascal(node["$ref"].rsplit("/", 1)[1])
        return None

    def ts_type(self, s, depth: int = 0) -> str:
        if not isinstance(s, dict) or not s:
            return "unknown"
        name = self.ref_name(s)
        if name:
            return name
        if "$ref" in s:
            return self.ts_type(self.resolve(s), depth)
        if "const" in s:
            return json.dumps(s["const"], ensure_ascii=False)
        if "enum" in s:
            return " | ".join(json.dumps(v, ensure_ascii=False) for v in s["enum"])
        for comb, sep in (("allOf", " & "), ("oneOf", " | "), ("anyOf", " | ")):
            if comb in s:
                return sep.join(self._wrap(self.ts_type(x, depth + 1)) for x in s[comb])
        t = s.get("type")
        if isinstance(t, list):
            parts = [self.ts_type({**s, "type": x}, depth) for x in t]
            return " | ".join(dict.fromkeys(parts))
        if t == "null":
            return "null"
        if t == "string":
            return "string"
        if t in ("integer", "number"):
            return "number"
        if t == "boolean":
            return "boolean"
        if t == "array":
            return f"{self._wrap(self.ts_type(s.get('items', {}), depth + 1))}[]"
        if t == "object" or "properties" in s:
            props = s.get("properties") or {}
            if not props:
                ap = s.get("additionalProperties")
                return f"Record<string, {self.ts_type(ap, depth + 1) if isinstance(ap, dict) else 'unknown'}>"
            req = set(s.get("required", []))
            inner = "; ".join(f"{prop_key(k)}{'' if k in req else '?'}: {self.ts_type(v, depth + 1)}" for k, v in props.items())
            return "{ " + inner + " }"
        return "unknown"

    @staticmethod
    def _wrap(t: str) -> str:
        return f"({t})" if (" | " in t or " & " in t) and not t.startswith("{") else t

    # ---------------------------------------------------------------- operations
    def _collect_ops(self) -> None:
        for path, item in (self.doc.get("paths") or {}).items():
            common = item.get("parameters", [])
            for method in HTTP_METHODS:
                op = item.get(method)
                if not isinstance(op, dict) or "operationId" not in op:
                    continue
                params = [self.resolve(p) for p in common + op.get("parameters", [])]
                path_p = [p["name"] for p in params if p.get("in") == "path"]
                query_p = [p["name"] for p in params if p.get("in") == "query"]
                required = [p["name"] for p in params if p.get("in") in ("path", "query") and (p.get("required") or p.get("in") == "path")]
                ptypes = {p["name"]: self.ts_type(p.get("schema", {})) for p in params if p.get("in") in ("path", "query")}
                body_type, body_kind = None, None
                rb = self.resolve(op.get("requestBody", {}))
                if isinstance(rb, dict) and rb.get("content"):
                    if "application/json" in rb["content"]:
                        body_type, body_kind = self.ts_type(rb["content"]["application/json"].get("schema", {})), "json"
                    elif "multipart/form-data" in rb["content"]:
                        body_type, body_kind = "FormData", "multipart"
                rtype, rkind, itype = self._response(op)
                self.ops[op["operationId"]] = OpMeta(
                    op_id=op["operationId"], method=method, path=path, summary=op.get("summary", ""),
                    path_params=path_p, query_params=query_p, required_params=required, param_types=ptypes,
                    body_type=body_type, body_kind=body_kind, response_type=rtype, response_kind=rkind, item_type=itype,
                    idempotent=bool(op.get("x-idempotent")), roles=list(op.get("x-roles") or []),
                )

    def _response(self, op: dict) -> tuple[str, str, str]:
        """→ (TS-тип, вид, тип элемента). Вид: page | array | object | text | blob | none.
        Не-JSON ответы (text/*, файлы) получают responseType 'text'/'blob', иначе HttpClient упадёт на разборе JSON."""
        responses = op.get("responses", {})
        for code in ("200", "201"):
            r = self.resolve(responses.get(code, {}))
            content = (r or {}).get("content", {}) or {}
            json_ct = next((ct for ct in content if ct == "application/json" or ct.endswith("+json")), None)
            if not json_ct and content:
                cts = list(content)
                textual = all(ct.startswith("text/") or ct in ("application/xml", "application/yaml", "application/x-yaml") for ct in cts)
                return ("string", "text", "never") if textual else ("Blob", "blob", "never")
            if json_ct:
                sch = content[json_ct].get("schema", {})
                rtype = self.ts_type(sch)
                res = self.resolve(sch)
                props = res.get("properties", {}) if isinstance(res, dict) else {}
                if "items" in props and "total" in props:
                    item = self.resolve(props["items"]).get("items", {})
                    return rtype, "page", self.ts_type(item)
                if isinstance(res, dict) and res.get("type") == "array":
                    return rtype, "array", self.ts_type(res.get("items", {}))
                return rtype, "object", rtype
        return "void", "none", "never"

    # ---------------------------------------------------------------- emit
    def models_ts(self) -> str:
        out = ["// AUTO-GENERATED by SpecForge from OpenAPI. Не редактировать вручную.", ""]
        for raw_name, s in self.schemas.items():
            name = pascal(raw_name)
            s = self.resolve(s)
            desc = s.get("description")
            if desc:
                out.append(f"/** {desc.strip().splitlines()[0]} */")
            if (s.get("type") == "object" or "properties" in s) and s.get("properties"):
                req = set(s.get("required", []))
                out.append(f"export interface {name} {{")
                for k, v in s["properties"].items():
                    v = v if isinstance(v, dict) else {}
                    d = (self.resolve(v) or {}).get("description") if "$ref" not in v else None
                    if d:
                        out.append(f"  /** {d.strip().splitlines()[0]} */")
                    out.append(f"  {prop_key(k)}{'' if k in req else '?'}: {self.ts_type(v)};")
                out.append("}")
            else:
                out.append(f"export type {name} = {self.ts_type(s)};")
            out.append("")
        return "\n".join(out)

    def service_ts(self) -> str:
        used = set()
        for m in self.ops.values():
            for t in [m.response_type, m.body_type or "", *m.param_types.values()]:
                used |= set(re.findall(r"\b[A-Z][A-Za-z0-9]*\b", t))
        model_names = sorted(n for n in used if n in {pascal(k) for k in self.schemas})
        lines = [
            "// AUTO-GENERATED by SpecForge from OpenAPI. Не редактировать вручную.",
            "import { HttpClient, HttpContext, HttpHeaders, HttpParams } from '@angular/common/http';",
            "import { Injectable, inject } from '@angular/core';",
            "import { Observable } from 'rxjs';",
            "",
            "import { environment } from '../../../environments/environment';",
            "import { SILENT_ERRORS } from '../../core/http/problem';",
        ]
        if model_names:
            lines.append(f"import {{ {', '.join(model_names)} }} from './models';")
        lines += [
            "",
            "export interface CallOptions {",
            "  /** Для операций x-idempotent: один ключ на одно намерение пользователя (повторы — тем же ключом). */",
            "  idempotencyKey?: string;",
            "  /** Ошибку показывает вызывающий код — interceptor не выводит toast. */",
            "  silent?: boolean;",
            "}",
            "",
        ]
        for m in self.ops.values():
            names = m.path_params + [q for q in m.query_params if q not in m.path_params]
            if names:
                lines.append(f"export interface {m.params_type} {{")
                for n in names:
                    opt = "" if n in m.required_params else "?"
                    lines.append(f"  {prop_key(n)}{opt}: {m.param_types.get(n, 'string')};")
                lines.append("}")
            else:
                lines.append(f"// eslint-disable-next-line @typescript-eslint/no-empty-object-type\nexport interface {m.params_type} {{}}")
        lines += [
            "",
            "function toParams(src: object, names: readonly string[]): HttpParams {",
            "  let p = new HttpParams();",
            "  const rec = src as Record<string, unknown>;",
            "  for (const n of names) {",
            "    const v = rec[n];",
            "    if (v === undefined || v === null || v === '') continue;",
            "    if (Array.isArray(v)) v.forEach((x) => (p = p.append(n, String(x))));",
            "    else p = p.set(n, v instanceof Date ? v.toISOString() : String(v));",
            "  }",
            "  return p;",
            "}",
            "",
            "@Injectable({ providedIn: 'root' })",
            "export class ApiService {",
            "  private readonly http = inject(HttpClient);",
            "  private readonly base = environment.apiBaseUrl;",
            "",
            "  private options(params: object, query: readonly string[], opts: CallOptions) {",
            "    let headers = new HttpHeaders();",
            "    if (opts.idempotencyKey) headers = headers.set('Idempotency-Key', opts.idempotencyKey);",
            "    return { params: toParams(params, query), headers, context: new HttpContext().set(SILENT_ERRORS, !!opts.silent) };",
            "  }",
            "",
        ]
        for m in self.ops.values():
            url = re.sub(r"\{([^}]+)\}", lambda mt: "${encodeURIComponent(String(params." + mt.group(1) + "))}", m.path)
            q = "[" + ", ".join(f"'{x}'" for x in m.query_params) + "]"
            has_required = bool(m.required_params)
            pdecl = f"params: {m.params_type}" + ("" if has_required else " = {}")
            sig = []
            if m.body_type:
                sig.append(f"body: {m.body_type}")
            sig += [pdecl, "opts: CallOptions = {}"]
            rt = m.response_type if m.response_kind != "none" else "void"
            if m.summary:
                lines.append(f"  /** {m.summary} */")
            lines.append(f"  {m.op_id}({', '.join(sig)}): Observable<{rt}> {{")
            opt = f"this.options(params, {q}, opts)"
            gen = f"<{rt}>"
            if m.response_kind in ("text", "blob"):
                # перегрузки HttpClient с responseType не принимают параметр типа
                opt, gen = f"{{ ...{opt}, responseType: '{m.response_kind}' as const }}", ""
            if m.method in ("get", "delete"):
                lines.append(f"    return this.http.{m.method}{gen}(`${{this.base}}{url}`, {opt});")
            else:
                body = "body" if m.body_type else "null"
                lines.append(f"    return this.http.{m.method}{gen}(`${{this.base}}{url}`, {body}, {opt});")
            lines.append("  }")
            lines.append("")
        lines.append("}")
        return "\n".join(lines) + "\n"
