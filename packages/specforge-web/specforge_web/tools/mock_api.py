"""Mock-API из OpenAPI без Prism: ответы строятся по examples/enum/format схем.

Используется spa_server (--mock openapi.yaml) для UI-ревью и демо, когда нет npm/Prism и backend.
Списки (Page) возвращают ITEMS элементов, которые различаются: id, перечисления по кругу, числа и даты сдвигаются,
цифры в строковых примерах меняются с сохранением шаблона, координаты точек смещаются. Геометрия без примера
(полигоны, линии) строится вокруг первой точки-примера из самой спеки — без констант предметной области.
POST/PUT — 201/200 с примером; login — токен.
Созданное через POST в коллекцию запоминается (в памяти процесса): оно первым возвращается в списке этой
коллекции и по GET /<коллекция>/{id}; списки понимают поиск q и фильтры по полям — чтобы e2e-сценарий
«создал → нашёл в списке» можно было прогнать локально. На стенде то же проверяется против настоящего backend.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from urllib.parse import parse_qs
from datetime import datetime, timedelta, timezone

from specforge_web.agents.ux_designer.labels import field_label
from specforge_web.generators.angular.openapi_ts import TsGen

NOW = datetime.now(timezone.utc).replace(microsecond=0)
ITEMS = 5
GEO_TYPES = ("Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon")


def _is_pair(v) -> bool:
    return isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)


def _bump_digits(text: str, i: int) -> str:
    """«X123YZ45» → «X124YZ45»: меняется первая группа цифр, длина группы сохраняется — шаблон (pattern) тоже."""
    g = re.search(r"\d+", text)
    if not g:
        return text
    width = len(g.group())
    n = (int(g.group()) + i) % (10 ** width)
    if g.group()[0] != "0" and width > 1 and n < 10 ** (width - 1):
        n += 10 ** (width - 1)  # без ведущего нуля, если его не было в примере
    return text[:g.start()] + str(n).zfill(width) + text[g.end():]


FUTURE_TIME = re.compile(r"(end|until|expire|due|finish|deadline|valid_?to|^to$)", re.I)


class MockApi:
    def __init__(self, openapi: dict, base: str = "/api/v1"):
        self.gen = TsGen(openapi)
        self.seed = self._find_seed(openapi)
        self.store: dict[str, list[dict]] = {}  # путь коллекции → созданные записи (новые первыми)
        self.lock = threading.Lock()
        self.base = base.rstrip("/")
        self.routes = []
        for m in self.gen.ops.values():
            rx = "^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(m.path)) + "$"
            self.routes.append((m.method.upper(), re.compile(rx), m))
        self.routes.sort(key=lambda r: -len(r[1].pattern))

    @staticmethod
    def _find_seed(doc) -> list[float]:
        """Первая пара координат из examples спеки — центр синтетической геометрии (иначе [0, 0])."""
        stack = [doc]
        while stack:
            node = stack.pop(0)
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in ("examples", "example"):
                        for ex in (v if k == "examples" and isinstance(v, list) else [v]):
                            if _is_pair(ex):
                                return [float(ex[0]), float(ex[1])]
                            if isinstance(ex, dict) and _is_pair(ex.get("coordinates")):
                                return [float(x) for x in ex["coordinates"]]
                    else:
                        stack.append(v)
            elif isinstance(node, list):
                stack.extend(node)
        return [0.0, 0.0]

    def _geometry(self, kind: str, i: int) -> dict:
        x, y = self.seed[0] + 0.006 * (i % 3) - 0.006, self.seed[1] + 0.004 * (i // 3) - 0.002
        d = 0.0022
        r = lambda a, b: [round(a, 6), round(b, 6)]  # noqa: E731
        ring = [r(x - d, y - d), r(x + d, y - d), r(x + d, y + d), r(x - d, y + d), r(x - d, y - d)]
        line = [r(x - d, y - d), r(x, y + d / 2), r(x + d, y)]
        x, y = r(x, y)
        return {"Point": {"type": "Point", "coordinates": [x, y]},
                "MultiPoint": {"type": "MultiPoint", "coordinates": [[x, y]]},
                "LineString": {"type": "LineString", "coordinates": line},
                "MultiLineString": {"type": "MultiLineString", "coordinates": [line]},
                "Polygon": {"type": "Polygon", "coordinates": [ring]},
                "MultiPolygon": {"type": "MultiPolygon", "coordinates": [[ring]]}}[kind]

    def _geo_kind(self, s: dict) -> str | None:
        props = s.get("properties") or {}
        if "coordinates" not in props or "type" not in props:
            return None
        t = self.gen.resolve(props["type"]) if isinstance(props["type"], dict) else {}
        vals = [t.get("const")] + list(t.get("enum") or []) + list(t.get("examples") or [])
        return next((v for v in vals if v in GEO_TYPES), None)

    def _vary(self, v, i: int, name: str, s: dict):
        """Сдвиг примера для i-го элемента списка (i=0 — пример как есть)."""
        if not i:
            return v
        if _is_pair(v):
            return [round(v[0] + 0.003 * i, 6), round(v[1] + 0.002 * i, 6)]
        if isinstance(v, bool):
            return v if i % 2 == 0 else not v
        if isinstance(v, int):
            return v + i
        if isinstance(v, float):
            return round(v * (1 + 0.15 * i), 4)
        if isinstance(v, str):
            fmt = s.get("format")
            if name == "id" or fmt == "uuid" or name.endswith("Id"):
                return str(uuid.UUID(int=i + 1))
            if fmt in ("date-time", "date"):
                try:
                    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                    dt = dt - timedelta(minutes=37 * i) if fmt == "date-time" else dt + timedelta(days=i)
                    return dt.isoformat().replace("+00:00", "Z") if fmt == "date-time" else dt.date().isoformat()
                except ValueError:
                    return v
            out = _bump_digits(v, i)
            if out != v and s.get("pattern") and not re.search(s["pattern"], out):
                return v
            return out
        if isinstance(v, dict) and self._geo_kind(s) == "Point" and _is_pair(v.get("coordinates")):
            return {**v, "coordinates": self._vary(v["coordinates"], i, "coordinates", {})}
        return v

    def example(self, s, i: int = 0, depth: int = 0, name: str = ""):
        s = self.gen.resolve(s) if isinstance(s, dict) else {}
        if depth > 6:
            return None
        geo = self._geo_kind(s)
        if geo and geo != "Point" and not (s.get("examples") and isinstance(s["examples"][0], dict)
                                           and s["examples"][0].get("coordinates")):
            return self._geometry(geo, i)
        if "examples" in s and s["examples"]:
            ex = s["examples"]
            if len(ex) > 1:
                return ex[i % len(ex)]
            return self._vary(ex[0], i, name, s)
        if "const" in s:
            return s["const"]
        if s.get("enum"):
            return s["enum"][i % len(s["enum"])]
        for comb in ("allOf", "oneOf", "anyOf"):
            if s.get(comb):
                merged = {}
                for sub in s[comb]:
                    v = self.example(sub, i, depth + 1)
                    if isinstance(v, dict):
                        merged.update(v)
                    elif comb != "allOf":
                        return v
                return merged
        t = s.get("type")
        if isinstance(t, list):
            t = next((x for x in t if x != "null"), "null")
        if t == "object" or "properties" in s:
            if geo == "Point":
                c = self.gen.resolve((s.get("properties") or {}).get("coordinates") or {})
                if not c.get("examples"):
                    return self._geometry("Point", i)
            return {k: self.example(v, i, depth + 1, k) for k, v in (s.get("properties") or {}).items()}
        if t == "array":
            return [self.example(s.get("items", {}), j, depth + 1) for j in range(2)] if s.get("items") else []
        if t == "string":
            fmt = s.get("format")
            if fmt == "date-time":
                dt = NOW + timedelta(hours=2 + i) if FUTURE_TIME.search(name) else NOW - timedelta(minutes=17 * (i + 1))
                return dt.isoformat().replace("+00:00", "Z")
            if fmt == "date":
                return (NOW + timedelta(days=i + 1)).date().isoformat()
            if fmt == "uuid" or name.endswith("id") or name.endswith("Id"):
                return str(uuid.UUID(int=i + 1))
            if fmt == "uri":
                return "https://example.org/"
            return f"{field_label(name)} {i + 1}" if name else f"Значение {i + 1}"
        if t == "integer":
            return max(s.get("minimum", 0), 1) + i
        if t == "number":
            return float(max(s.get("minimum", 0), 1) * 10 + i)
        if t == "boolean":
            return i % 2 == 0
        return None

    @staticmethod
    def _collection(path: str) -> str:
        return path.rstrip("/")

    def _filter(self, items: list, query: dict[str, list[str]]) -> list:
        out = items
        q = (query.get("q") or [""])[0].strip().lower()
        if q:
            out = [x for x in out if isinstance(x, dict) and any(isinstance(v, str) and q in v.lower() for v in x.values())]
        for k, vals in query.items():
            if k in ("q", "page", "size", "sort", "from", "to") or not vals:
                continue
            out = [x for x in out if not isinstance(x, dict) or k not in x or str(x[k]) == vals[0]]
        return out

    def _list(self, m, query: dict[str, list[str]]) -> list:
        item = self.gen.schemas.get(m.item_type) or {}
        stored = self.store.get(self._collection(m.path), [])
        return self._filter(stored + [self.example(item, i) for i in range(ITEMS)], query)

    def handle(self, method: str, path: str, body: bytes | None = None) -> tuple[int, dict | list | None]:
        query = parse_qs(path.split("?", 1)[1]) if "?" in path else {}
        p = path.split("?", 1)[0]
        if not p.startswith(self.base):
            return 404, {"title": "Not found", "status": 404}
        p = p[len(self.base):] or "/"
        for meth, rx, m in self.routes:
            if meth == method and rx.match(p):
                if m.op_id == "login":
                    return 200, {"accessToken": "mock-token", "expiresIn": 3600,
                                 "user": {"id": str(uuid.UUID(int=1)), "name": "Демо", "roles": sorted({r for o in self.gen.ops.values() for r in o.roles}) or ["admin"]}}
                if m.response_kind == "page":
                    items = self._list(m, query)
                    return 200, {"items": items[:20], "total": len(items), "page": 1, "size": 20}
                if m.response_kind == "array":
                    return 200, self._list(m, query)
                if m.response_kind == "text":
                    return 200, f"Содержимое ответа {m.op_id} (mock)"
                if m.response_kind == "blob":
                    return 200, b""
                if m.response_kind == "object":
                    sch = self.gen.schemas.get(m.response_type) or {}
                    if method == "POST" and not m.path_params:
                        return 201, self._create(m, sch, body)
                    if method == "GET" and m.path_params:
                        found = self._find(p)
                        if found is not None:
                            return 200, found
                    return 200, self.example(sch, 0)
                return 202, None
        return 404, {"title": "Not found", "status": 404}

    def _create(self, m, sch: dict, body: bytes | None) -> dict:
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            data = {}
        item = self.example(sch, 0)
        if isinstance(item, dict) and isinstance(data, dict):
            item.update({k: v for k, v in data.items() if v is not None})
            item["id"] = str(uuid.uuid4())
            with self.lock:
                self.store.setdefault(self._collection(m.path), []).insert(0, item)
        return item

    def _find(self, path: str) -> dict | None:
        coll, _, ident = path.rstrip("/").rpartition("/")
        for x in self.store.get(coll, []):
            if str(x.get("id")) == ident:
                return x
        return None

    def bytes(self, method: str, path: str, body: bytes | None = None) -> tuple[int, bytes, str]:
        code, body = self.handle(method, path, body)
        if isinstance(body, bytes):
            return code, body, "application/octet-stream"
        if isinstance(body, str):
            return code, body.encode("utf-8"), "text/plain; charset=utf-8"
        ctype = "application/problem+json" if code >= 400 else "application/json"
        return code, (json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""), ctype
