"""Шлюз UX-этапа: детерминированная проверка screen-spec без LLM.

Проверки (код ошибки → смысл):
  UX_SCHEMA              нарушение screen-spec.schema.json
  UX_DUPLICATE           повтор id экрана / маршрута / действия
  UX_BAD_REF             ссылка на несуществующий экран, действие, роль или источник данных
  UX_ROUTE_PARAM         binding route:x без :x в маршруте экрана
  UX_UNKNOWN_OPERATION   operationId отсутствует в OpenAPI (с подсказкой ближайшего)
  UX_UNKNOWN_PARAM       параметр/фильтр не объявлен в операции
  UX_UNKNOWN_FIELD       поле отсутствует в схеме ответа / тела запроса
  UX_FR_UNKNOWN          ссылка на несуществующее требование
  UX_FR_UNCOVERED        требование канала платформы не покрыто ни одним экраном
  UX_UNKNOWN_COMPONENT   компонент слота не из манифеста UI Kit
  UX_ROLE_MISMATCH       (warning) роль экрана не допущена к операции в x-roles
  UX_FORM_INCOMPLETE     (warning) обязательное поле тела запроса не выведено в форму
  UX_VALIDATION_GAP      (warning) в API есть pattern/длина, а в форме нет
  UX_NAV_LIMIT           на Android больше 5 пунктов нижней навигации

Запуск:
  python -m agents.ux_designer.gate --spec S.json --openapi O.yaml --requirements R.json
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[2]
from specforge_web.contracts.agent.agent_contract import GateError, GateResult  # noqa: E402

SCHEMA_PATH = ROOT / "contracts" / "ux" / "screen-spec.schema.json"
MANIFESTS = {
    "primeng@20": ROOT / "design-system" / "primeng" / "kit-manifest.json",
    "material3": ROOT / "design-system" / "material3" / "kit-manifest.json",
}
PAGE_PARAMS = {"page", "size"}
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def suggest(word: str, options) -> str:
    """Подсказка для ремонтника: до 3 ближайших вариантов."""
    close = difflib.get_close_matches(word, list(options), n=3, cutoff=0.4)
    return f"; возможно: {', '.join(repr(c) for c in close)}" if close else ""


# ---------------------------------------------------------------- OpenAPI helpers
class OpenApi:
    def __init__(self, doc: dict):
        self.doc = doc
        self.ops: dict[str, dict] = {}
        for path, item in (doc.get("paths") or {}).items():
            common = item.get("parameters", [])
            for method, op in item.items():
                if method not in HTTP_METHODS or not isinstance(op, dict) or "operationId" not in op:
                    continue
                params = [self.resolve(p) for p in common + op.get("parameters", [])]
                self.ops[op["operationId"]] = {"path": path, "method": method, "op": op, "params": params}

    def resolve(self, node: Any) -> Any:
        seen = 0
        while isinstance(node, dict) and "$ref" in node and seen < 20:
            ref = node["$ref"]
            if not ref.startswith("#/"):
                return {}
            cur: Any = self.doc
            for part in ref[2:].split("/"):
                cur = cur.get(part, {}) if isinstance(cur, dict) else {}
            node, seen = cur, seen + 1
        return node

    def param_names(self, op_id: str) -> set[str]:
        return {p.get("name") for p in self.ops[op_id]["params"] if p.get("in") in ("query", "path")}

    def response_schema(self, op_id: str) -> dict:
        responses = self.ops[op_id]["op"].get("responses", {})
        for code in ("200", "201", "202"):
            r = self.resolve(responses.get(code, {}))
            content = r.get("content", {}) if isinstance(r, dict) else {}
            for ctype in ("application/json", "application/problem+json"):
                if ctype in content:
                    return self.resolve(content[ctype].get("schema", {}))
        return {}

    def item_schema(self, op_id: str) -> dict:
        """Для Page-ответа — схема элемента, иначе — сама схема."""
        s = self.response_schema(op_id)
        props = s.get("properties", {})
        if "items" in props and "total" in props:
            return self.resolve(self.resolve(props["items"]).get("items", {}))
        if s.get("type") == "array":
            return self.resolve(s.get("items", {}))
        return s

    def request_schema(self, op_id: str) -> dict:
        body = self.resolve(self.ops[op_id]["op"].get("requestBody", {}))
        content = body.get("content", {}) if isinstance(body, dict) else {}
        for ctype in ("application/json", "multipart/form-data"):
            if ctype in content:
                return self.resolve(content[ctype].get("schema", {}))
        return {}

    def has_field(self, schema: dict, path: str) -> bool:
        cur = schema
        for part in path.split("."):
            cur = self.resolve(cur)
            if not isinstance(cur, dict):
                return False
            for comb in ("allOf", "oneOf", "anyOf"):  # упрощённо: ищем в любой ветке
                if comb in cur:
                    merged: dict = {}
                    for sub in cur[comb]:
                        merged.update(self.resolve(sub).get("properties", {}))
                    cur = {"properties": {**cur.get("properties", {}), **merged}}
            props = cur.get("properties")
            if props is None:
                return True  # схема без описанных свойств — не можем проверить, не ругаемся
            if part not in props:
                return False
            cur = props[part]
        return True


# ---------------------------------------------------------------- gate
class Gate:
    def __init__(self, spec: dict, api: OpenApi | None, requirements: dict | None):
        self.spec, self.api, self.req = spec, api, requirements
        self.errors: list[GateError] = []

    def err(self, code: str, where: str, msg: str, severity: str = "error") -> None:
        self.errors.append(GateError(message=f"{where}: {msg}", file=where, code=code, severity=severity))

    # ---- entry
    def run(self) -> list[GateError]:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        schema_errors = sorted(validator.iter_errors(self.spec), key=lambda e: list(e.absolute_path))
        for e in schema_errors[:50]:
            path = "/".join(str(p) for p in e.absolute_path) or "<root>"
            self.err("UX_SCHEMA", path, e.message[:300])
        if schema_errors:
            return self.errors  # структура сломана — остальные проверки дадут шум
        self.check_refs()
        if self.api:
            self.check_api()
        if self.req:
            self.check_coverage()
        self.check_components()
        return self.errors

    # ---- references inside the spec
    def check_refs(self) -> None:
        screens = self.spec["screens"]
        ids = [s["id"] for s in screens]
        self.screen_ids = set(ids)
        role_ids = {r["id"] for r in self.spec["roles"]}
        for dup in {i for i in ids if ids.count(i) > 1}:
            self.err("UX_DUPLICATE", f"screens[{dup}]", "повторяющийся id экрана")
        routes = [s["route"] for s in screens]
        for dup in {r for r in routes if routes.count(r) > 1}:
            self.err("UX_DUPLICATE", f"route {dup}", "повторяющийся маршрут")

        auth = self.spec["app"].get("auth", {})
        for key in ("loginScreen", "forbiddenScreen"):
            if auth.get(key) and auth[key] not in self.screen_ids:
                self.err("UX_BAD_REF", f"app.auth.{key}", f"экран '{auth[key]}' не найден")
        for i, nav in enumerate(self.spec["navigation"]):
            if nav["screen"] not in self.screen_ids:
                self.err("UX_BAD_REF", f"navigation[{i}]", f"экран '{nav['screen']}' не найден")
        if self.spec["app"]["platform"] == "android" and len(self.spec["navigation"]) > 5:
            self.err("UX_NAV_LIMIT", "navigation", "на Android не более 5 пунктов нижней навигации")

        for s in screens:
            where = f"screens[{s['id']}]"
            for r in s["roles"]:
                if r not in role_ids:
                    self.err("UX_BAD_REF", where, f"роль '{r}' не объявлена в roles")
            action_ids = [a["id"] for a in s.get("actions", [])]
            for dup in {a for a in action_ids if action_ids.count(a) > 1}:
                self.err("UX_DUPLICATE", where, f"повторяющийся id действия '{dup}'")
            for a in s.get("actions", []):
                for r in a.get("roles", []):
                    if r not in role_ids:
                        self.err("UX_BAD_REF", f"{where}.actions[{a['id']}]", f"роль '{r}' не объявлена")
            for key, st in s["states"].items():
                if isinstance(st, dict) and st.get("actionId") and st["actionId"] not in action_ids:
                    self.err("UX_BAD_REF", f"{where}.states.{key}", f"действие '{st['actionId']}' не объявлено в actions")
            for n in s.get("notifications", []):
                if n.get("actionId") and n["actionId"] not in action_ids:
                    self.err("UX_BAD_REF", f"{where}.notifications", f"действие '{n['actionId']}' не объявлено")
            for loc, target in self._screen_links(s):
                if target not in self.screen_ids:
                    self.err("UX_BAD_REF", f"{where}.{loc}", f"экран '{target}' не найден")
            sources = {"primary"} | set((s.get("data") or {}).get("secondary", {}).keys())
            for loc, src in self._sources(s):
                if src not in sources:
                    self.err("UX_BAD_REF", f"{where}.{loc}", f"источник '{src}' не объявлен в data (есть: {', '.join(sorted(sources))})")
            route_params = set(re.findall(r":([A-Za-z]+)", s["route"]))
            for loc, binding in self._bindings(s):
                kind, _, name = binding.partition(":")
                if kind == "route" and name not in route_params:
                    self.err("UX_ROUTE_PARAM", f"{where}.{loc}", f"'{binding}', но в маршруте '{s['route']}' нет :{name}")

    @staticmethod
    def _walk(node: Any, path: str = "") -> Iterator[tuple[str, Any]]:
        yield path, node
        if isinstance(node, dict):
            for k, v in node.items():
                yield from Gate._walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from Gate._walk(v, f"{path}[{i}]")

    def _screen_links(self, s: dict) -> Iterator[tuple[str, str]]:
        for path, node in self._walk(s):
            if isinstance(node, dict):
                for key in ("linkTo", "rowLink", "navigateTo"):
                    if isinstance(node.get(key), str):
                        yield f"{path}.{key}", node[key]

    def _sources(self, s: dict) -> Iterator[tuple[str, str]]:
        for path, node in self._walk({k: v for k, v in s.items() if k != "data"}):
            if isinstance(node, dict) and isinstance(node.get("source"), str):
                yield f"{path}.source", node["source"]

    def _bindings(self, s: dict) -> Iterator[tuple[str, str]]:
        for path, node in self._walk(s):
            if isinstance(node, dict):
                for key in ("params", "body"):
                    if isinstance(node.get(key), dict):
                        for pk, pv in node[key].items():
                            if isinstance(pv, str) and re.match(r"^(route|query|filter|const|item|context):", pv):
                                yield f"{path}.{key}.{pk}", pv

    # ---- OpenAPI consistency
    def _op(self, op_id: str, where: str) -> bool:
        if op_id in self.api.ops:
            return True
        verb = re.match(r"^[a-z]+", op_id).group(0)
        same_verb = sorted(o for o in self.api.ops if o.startswith(verb))
        hint = f"; операции {verb}*: {', '.join(same_verb[:10])}" if same_verb else suggest(op_id, self.api.ops)
        self.err("UX_UNKNOWN_OPERATION", where, f"operationId '{op_id}' нет в OpenAPI{hint}")
        return False

    def _check_params(self, op_id: str, params: dict, where: str) -> None:
        names = self.api.param_names(op_id)
        for p in params:
            if p not in names:
                self.err("UX_UNKNOWN_PARAM", where, f"параметр '{p}' не объявлен в {op_id} (есть: {', '.join(sorted(names)) or '—'})")

    def _check_fields(self, schema: dict, fields: list[str], where: str, what: str) -> None:
        for f in fields:
            if f and not self.api.has_field(schema, f):
                props = sorted((self.api.resolve(schema) or {}).get("properties", {}).keys())
                self.err("UX_UNKNOWN_FIELD", where, f"поле '{f}' нет в {what}{suggest(f, props)}")

    def check_api(self) -> None:
        for s in self.spec["screens"]:
            where = f"screens[{s['id']}]"
            data = s.get("data") or {}
            src_ops: dict[str, str] = {}
            all_sources = {"primary": data.get("primary")} | dict(data.get("secondary", {}))
            for key, ds in all_sources.items():
                if not ds:
                    continue
                if self._op(ds["operationId"], f"{where}.data.{key}"):
                    src_ops[key] = ds["operationId"]
                    self._check_params(ds["operationId"], ds.get("params", {}), f"{where}.data.{key}")
                    if ds.get("pagination") == "page" and not PAGE_PARAMS <= self.api.param_names(ds["operationId"]):
                        self.err("UX_UNKNOWN_PARAM", f"{where}.data.{key}", f"pagination=page, но {ds['operationId']} не принимает page/size")
                    self._check_roles(s, ds["operationId"], f"{where}.data.{key}")

            def item(src: str) -> dict | None:
                return self.api.item_schema(src_ops[src]) if src in src_ops else None

            prim = src_ops.get("primary")
            # list
            if "list" in s and prim:
                lst, sch = s["list"], item("primary")
                self._check_fields(sch, [c["field"] for c in lst["columns"]], f"{where}.list.columns", f"ответе {prim}")
                self._check_fields(sch, [c["linkParam"] for c in lst["columns"] if c.get("linkParam")], f"{where}.list.columns.linkParam", f"ответе {prim}")
                names = self.api.param_names(prim)
                for f in lst.get("filters", []):
                    for p in (f.get("param") or f["field"]).split(","):
                        if p not in names:
                            self.err("UX_UNKNOWN_PARAM", f"{where}.list.filters[{f['field']}]", f"фильтр '{p}' не принимается {prim}")
                    if f.get("optionsFrom"):
                        self._op(f["optionsFrom"]["operationId"], f"{where}.list.filters[{f['field']}].optionsFrom")
                if lst.get("search") and lst["search"]["param"] not in names:
                    self.err("UX_UNKNOWN_PARAM", f"{where}.list.search", f"'{lst['search']['param']}' не принимается {prim}")
                for t in lst.get("tabs", []):
                    for p in t["params"]:
                        if p not in names:
                            self.err("UX_UNKNOWN_PARAM", f"{where}.list.tabs[{t['label']}]", f"'{p}' не принимается {prim}")
            # detail
            if "detail" in s and prim:
                d = s["detail"]
                base = [d.get("titleField"), d.get("subtitleField"), (d.get("statusField") or {}).get("field")]
                self._check_fields(item("primary"), [b for b in base if b], f"{where}.detail", f"ответе {prim}")
                for i, sec in enumerate(d["sections"]):
                    src = sec.get("source", "primary")
                    sch = item(src)
                    if sch is None:
                        continue
                    fields = [f["field"] for f in sec.get("fields", []) + sec.get("columns", [])]
                    fields += [f["linkParam"] for f in sec.get("fields", []) + sec.get("columns", []) if f.get("linkParam")]
                    if sec.get("timeline"):
                        fields += self._timeline_fields(sec["timeline"])
                    self._check_fields(sch, fields, f"{where}.detail.sections[{i}]", f"ответе {src_ops[src]}")
                    for layer in sec.get("layers", []):
                        self._check_layer(layer, item, src_ops, f"{where}.detail.sections[{i}].layers[{layer['id']}]")
            # map
            if "map" in s:
                for layer in s["map"]["layers"]:
                    self._check_layer(layer, item, src_ops, f"{where}.map.layers[{layer['id']}]")
                sp = s["map"].get("sidePanel")
                if sp and sp["source"] in src_ops:
                    fields = [f["field"] for f in sp.get("fields", [])] + (self._timeline_fields(sp["timeline"]) if sp.get("timeline") else [])
                    self._check_fields(item(sp["source"]), fields, f"{where}.map.sidePanel", f"ответе {src_ops[sp['source']]}")
                pb = s["map"].get("playback")
                if pb and pb["source"] in src_ops:
                    self._check_fields(item(pb["source"]), [pb["timeField"], pb.get("geometryField")], f"{where}.map.playback", f"ответе {src_ops[pb['source']]}")
            # timeline
            if "timeline" in s and prim:
                tl = s["timeline"]
                self._check_fields(item("primary"), self._timeline_fields(tl), f"{where}.timeline", f"ответе {prim}")
                names = self.api.param_names(prim)
                for f in tl.get("filters", []):
                    for p in (f.get("param") or f["field"]).split(","):
                        if p not in names:
                            self.err("UX_UNKNOWN_PARAM", f"{where}.timeline.filters[{f['field']}]", f"фильтр '{p}' не принимается {prim}")
                    if f.get("optionsFrom"):
                        self._op(f["optionsFrom"]["operationId"], f"{where}.timeline.filters[{f['field']}].optionsFrom")
            # dashboard
            if "dashboard" in s:
                for w in s["dashboard"]["widgets"]:
                    ww = f"{where}.dashboard.widgets[{w['id']}]"
                    if w["source"] not in src_ops:
                        continue
                    sch = self.api.response_schema(src_ops[w["source"]]) if w["kind"] == "stat" else item(w["source"])
                    fields = [w.get("valueField")] + [f["field"] for f in w.get("fields", [])]
                    if w.get("timeline"):
                        fields += self._timeline_fields(w["timeline"])
                    self._check_fields(sch, [f for f in fields if f], ww, f"ответе {src_ops[w['source']]}")
                    for layer in w.get("layers", []):
                        self._check_layer(layer, item, src_ops, f"{ww}.layers[{layer['id']}]")
            # form
            if "form" in s:
                sub = s["form"]["submit"]
                if self._op(sub["operationId"], f"{where}.form.submit"):
                    self._check_roles(s, sub["operationId"], f"{where}.form.submit")
                    req = self.api.request_schema(sub["operationId"])
                    names = [f["name"] for f in s["form"]["fields"]]
                    self._check_fields(req, names, f"{where}.form.fields", f"теле запроса {sub['operationId']}")
                    for r in req.get("required", []):
                        if r not in names:
                            self.err("UX_FORM_INCOMPLETE", f"{where}.form", f"обязательное поле '{r}' запроса не выведено в форму", "warning")
                    props = req.get("properties", {})
                    for f in s["form"]["fields"]:
                        p = self.api.resolve(props.get(f["name"], {}))
                        v = f.get("validation", {})
                        if p.get("pattern") and not v.get("pattern"):
                            self.err("UX_VALIDATION_GAP", f"{where}.form.fields[{f['name']}]", "в API задан pattern, в форме — нет", "warning")
                        if p.get("maxLength") and not v.get("maxLength"):
                            self.err("UX_VALIDATION_GAP", f"{where}.form.fields[{f['name']}]", "в API задан maxLength, в форме — нет", "warning")
                if s["form"].get("load"):
                    self._op(s["form"]["load"]["operationId"], f"{where}.form.load")
            # actions
            for a in s.get("actions", []):
                if a.get("operationId") and self._op(a["operationId"], f"{where}.actions[{a['id']}]"):
                    self._check_params(a["operationId"], a.get("params", {}), f"{where}.actions[{a['id']}]")
                    self._check_roles(s, a["operationId"], f"{where}.actions[{a['id']}]", a.get("roles"))
                    meta = self.api.ops[a["operationId"]]
                    if meta["method"] != "get" and not a.get("successMessage"):
                        self.err("UX_BAD_REF", f"{where}.actions[{a['id']}]", "изменяющее действие без successMessage", "warning")
                    if meta["op"].get("x-idempotent") and not a.get("idempotent"):
                        self.err("UX_BAD_REF", f"{where}.actions[{a['id']}]", "операция идемпотентна (x-idempotent), а действие — нет", "warning")

    @staticmethod
    def _timeline_fields(tl: dict) -> list[str]:
        return [f for f in (tl.get("titleField"), tl.get("timeField"), tl.get("descriptionField"), tl.get("severityField"), tl.get("linkParam")) if f]

    def _check_layer(self, layer: dict, item, src_ops: dict, where: str) -> None:
        if layer["source"] not in src_ops:
            return
        fields = [layer["geometryField"], layer.get("labelField"), layer.get("colorBy")] + [f["field"] for f in layer.get("popupFields", [])]
        self._check_fields(item(layer["source"]), [f for f in fields if f], where, f"ответе {src_ops[layer['source']]}")

    def _check_roles(self, s: dict, op_id: str, where: str, roles: list[str] | None = None) -> None:
        allowed = self.api.ops[op_id]["op"].get("x-roles")
        if not allowed or s.get("public"):
            return
        missing = [r for r in (roles or s["roles"]) if r not in allowed]
        if missing:
            self.err("UX_ROLE_MISMATCH", where, f"роли {missing} не допущены к {op_id} (x-roles: {allowed}) — экран покажет «нет доступа»", "warning")

    # ---- requirements coverage
    def check_coverage(self) -> None:
        platform = self.spec["app"]["platform"]
        fr = {f["id"]: f for f in self.req.get("functional", [])}
        covered: set[str] = set()
        for s in self.spec["screens"]:
            refs = set(s["requirements"]) | {r for sl in s.get("slots", []) for r in sl.get("requirements", [])}
            for r in refs:
                if r.startswith("FR-") and r not in fr:
                    self.err("UX_FR_UNKNOWN", f"screens[{s['id']}]", f"требование {r} не найдено в requirements")
            covered |= refs
        self.coverage_total = [i for i, f in fr.items() if platform in f.get("channels", [])]
        self.coverage_missing = [i for i in self.coverage_total if i not in covered]
        for i in self.coverage_missing:
            self.err("UX_FR_UNCOVERED", i, f"«{fr[i]['title']}» относится к {platform}, но не покрыто ни одним экраном")

    # ---- UI kit
    def check_components(self) -> None:
        kit = self.spec["app"]["uiKit"]
        manifest = json.loads(MANIFESTS[kit].read_text(encoding="utf-8"))
        tags = {c["tag"] for c in manifest["components"]}
        for s in self.spec["screens"]:
            for sl in s.get("slots", []):
                for c in sl.get("components", []):
                    if c not in tags:
                        self.err("UX_UNKNOWN_COMPONENT", f"screens[{s['id']}].slots[{sl['id']}]", f"компонента '{c}' нет в манифесте {kit}{suggest(c, tags)}")


def run_gate(spec: dict, openapi: dict | None = None, requirements: dict | None = None) -> GateResult:
    api = OpenApi(openapi) if openapi else None
    gate = Gate(spec, api, requirements)
    errors = gate.run()
    total = getattr(gate, "coverage_total", [])
    missing = getattr(gate, "coverage_missing", [])
    metrics = {
        "screens": len(spec.get("screens", [])) if isinstance(spec, dict) else 0,
        "requirementsInScope": len(total),
        "requirementsCovered": len(total) - len(missing),
        "coverage": round((len(total) - len(missing)) / len(total), 3) if total else None,
        "warnings": sum(1 for e in errors if e.severity == "warning"),
    }
    return GateResult(passed=not any(e.severity == "error" for e in errors), errors=errors, metrics=metrics)


def _load(path: Path | None) -> Any:
    if not path:
        return None
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text) if path.suffix in (".yaml", ".yml") else json.loads(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="UX gate")
    ap.add_argument("--spec", type=Path, required=True)
    ap.add_argument("--openapi", type=Path)
    ap.add_argument("--requirements", type=Path)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = run_gate(_load(a.spec), _load(a.openapi), _load(a.requirements))
    if a.json:
        print(res.to_json())
    else:
        for e in res.errors:
            print(f"{e.severity.upper():7} {e.code:22} {e.message}")
        print(f"UX gate: {'PASSED' if res.passed else 'FAILED'} | {json.dumps(res.metrics, ensure_ascii=False)}")
    return 0 if res.passed else 1


if __name__ == "__main__":
    sys.exit(main())
