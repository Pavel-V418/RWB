"""
Чтение OpenAPI 3.x (YAML или JSON) и подготовка запросов без LLM: пути, параметры, минимальное тело
по схеме. Ничего не знает о домене кейса.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

METHODS = ("get", "post", "put", "patch", "delete")


def load(path: Path | str) -> dict:
    """Файл YAML/JSON или URL (например, $STAND_URL/swagger/v1/swagger.json — без pyyaml на раннере)."""
    if str(path).startswith(("http://", "https://")):
        import urllib.request
        with urllib.request.urlopen(str(path), timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json" or text.lstrip().startswith("{"):
        return json.loads(text)
    import yaml  # pyyaml; на раннере без него используйте swagger.json стенда
    return yaml.safe_load(text)


def resolve(doc: dict, node):
    """Разворачивает $ref (локальные, #/components/...)."""
    seen = 0
    while isinstance(node, dict) and "$ref" in node and seen < 20:
        ref = node["$ref"]
        target = doc
        for part in ref.lstrip("#/").split("/"):
            target = target[part]
        extra = {k: v for k, v in node.items() if k != "$ref"}
        node = {**target, **extra}
        seen += 1
    return node


@dataclass
class Operation:
    method: str
    path: str
    op_id: str
    roles: list[str] | None          # None — x-roles не задан; [] — анонимно
    secured: bool
    idempotent: bool
    responses: set[str]
    params: list[dict] = field(default_factory=list)
    body_schema: dict | None = None
    body_type: str = "application/json"
    requirements: list[str] = field(default_factory=list)
    text: str = ""

    @property
    def name(self) -> str:
        return f"{self.method.upper()} {self.path}"


def operations(doc: dict) -> list[Operation]:
    global_sec = doc.get("security", [])
    ops = []
    for path, item in doc.get("paths", {}).items():
        common = item.get("parameters", [])
        for method in METHODS:
            op = item.get(method)
            if not op:
                continue
            sec = op.get("security", global_sec)
            params = [resolve(doc, p) for p in common + op.get("parameters", [])]
            body = resolve(doc, op.get("requestBody", {})) if op.get("requestBody") else None
            body_schema, body_type = None, "application/json"
            if body:
                content = body.get("content", {})
                body_type = next(iter(content), "application/json")
                body_schema = content.get(body_type, {}).get("schema")
            roles = op.get("x-roles")
            ops.append(Operation(
                method=method, path=path, op_id=op.get("operationId", f"{method}_{path}"),
                roles=list(roles) if isinstance(roles, list) else None,
                secured=bool(sec) and sec != [{}] and roles != [],
                idempotent=bool(op.get("x-idempotent")) or any(
                    p.get("in") == "header" and p.get("name", "").lower() == "idempotency-key" and p.get("required")
                    for p in params),
                responses={str(c) for c in op.get("responses", {})},
                params=params, body_schema=body_schema, body_type=body_type,
                requirements=[r for r in op.get("x-requirements", []) if isinstance(r, str)],
                text=f"{op.get('summary', '')} {op.get('description', '')}"))
    return ops


# ---------------------------------------------------------------- значения по схеме
def sample(doc: dict, schema: dict | None, depth: int = 0):
    """Минимальное валидное значение: examples/default/enum/const, иначе по типу (только required-поля)."""
    schema = resolve(doc, schema or {})
    if depth > 6:
        return None
    for key in ("const", "default"):
        if key in schema:
            return schema[key]
    if schema.get("examples"):
        return schema["examples"][0]
    if "example" in schema:
        return schema["example"]
    if schema.get("enum"):
        return schema["enum"][0]
    if "allOf" in schema:
        merged: dict = {"type": "object", "properties": {}, "required": []}
        for part in schema["allOf"]:
            part = resolve(doc, part)
            merged["properties"].update(part.get("properties", {}))
            merged["required"] += part.get("required", [])
        return sample(doc, merged, depth + 1)
    for key in ("oneOf", "anyOf"):
        if key in schema:
            options = [resolve(doc, s) for s in schema[key]]
            options = [o for o in options if o.get("type") != "null"] or options
            return sample(doc, options[0], depth + 1)
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), t[0])
    fmt = schema.get("format", "")
    if t == "object" or "properties" in schema:
        props = schema.get("properties", {})
        return {k: sample(doc, props.get(k, {}), depth + 1) for k in schema.get("required", [])}
    if t == "array":
        return [sample(doc, schema.get("items", {}), depth + 1)] * max(1, schema.get("minItems", 1))
    if t == "integer":
        return int(schema.get("minimum", 1))
    if t == "number":
        return float(schema.get("minimum", 0) if schema.get("minimum", 0) > 0 else 0.0)
    if t == "boolean":
        return False
    if fmt == "uuid":
        return str(uuid.uuid4())
    if fmt == "date-time":
        return "2026-01-01T00:00:00Z"
    if fmt == "date":
        return "2026-01-01"
    n = max(schema.get("minLength", 1), 1)
    return ("x" * n) if not schema.get("pattern") else _from_pattern(schema["pattern"], n)


def _from_pattern(pattern: str, n: int) -> str:
    """Очень простая догадка под типовые шаблоны; точность не нужна — проверяем права, а не бизнес."""
    if re.search(r"A-Za-z0-9_-", pattern):
        return "k" * max(n, 8)
    return "x" * n


def build_request(doc: dict, op: Operation, ids: dict[str, str] | None = None,
                  idempotency_key: str | None = "auto") -> tuple[str, dict, dict, bytes | None]:
    """(путь с подставленными id, query, headers, тело). ids — известные значения path-параметров."""
    ids = ids or {}
    path, query, headers = op.path, {}, {}
    for p in op.params:
        name, where = p.get("name"), p.get("in")
        if where == "path":
            value = ids.get(name) or str(sample(doc, p.get("schema")) or uuid.uuid4())
            path = path.replace("{" + name + "}", str(value))
        elif where == "query" and p.get("required"):
            query[name] = sample(doc, p.get("schema"))
        elif where == "header" and name.lower() == "idempotency-key":
            if idempotency_key == "auto":
                headers[name] = uuid.uuid4().hex
            elif idempotency_key:
                headers[name] = idempotency_key
    body = None
    if op.body_schema is not None:
        body = json.dumps(sample(doc, op.body_schema)).encode("utf-8")
        headers["Content-Type"] = op.body_type
    return path, query, headers, body
