"""
Проверки, выводимые из OpenAPI (без LLM):
  authn  — защищённая операция без токена и с поддельным токеном → 401
  authz  — роль не из x-roles → 403; чужой id в пути → 403/404 (не 200)
  idem   — x-idempotent без Idempotency-Key → 400/422; повтор с тем же ключом → тот же статус
  rate   — всплеск запросов → 429 с Retry-After (только для операций с лимитом)
  5xx    — ни один запрос проверок не должен давать 5xx
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field

from .spec import Operation, build_request

# категория → CWE (по ней security-контур находит SEC-требование) и критичность
CATEGORY = {
    "authn": ("CWE-306", "7.5"),
    "authz": ("CWE-285", "7.5"),
    "authz-object": ("CWE-639", "7.5"),
    "rate_limiting": ("CWE-770", "5.3"),
    "idempotency": ("CWE-20", "4.0"),
    "robustness": ("CWE-755", "5.0"),
}
SEC_CATEGORY = {"authn": "authn", "authz": "authz", "authz-object": "authz", "rate_limiting": "rate_limiting",
                "idempotency": "input_validation", "robustness": "input_validation"}


@dataclass
class Result:
    check: str          # authn-missing, authz-role, ...
    category: str       # ключ CATEGORY
    op: str             # "GET /api/v1/..."
    title: str
    status: str         # passed | failed | skipped
    message: str = ""
    duration: float = 0.0
    reqs: list[str] = field(default_factory=list)
    severity: str | None = None   # CVSS для SARIF, если отличается от категории


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes

    def json(self):
        try:
            return json.loads(self.body or b"null")
        except ValueError:
            return None


class Client:
    def __init__(self, base: str, timeout: float = 15):
        self.base, self.timeout = base.rstrip("/"), timeout
        self.log: list[tuple[str, int]] = []   # (запрос, статус) — для проверки 5xx

    def send(self, method: str, path: str, query: dict | None = None, headers: dict | None = None,
             body: bytes | None = None, token: str | None = None) -> Response:
        url = self.base + path + (("?" + urllib.parse.urlencode(query)) if query else "")
        h = {"Accept": "application/json", **(headers or {})}
        if token:
            h["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=body, headers=h, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                resp = Response(r.status, {k.lower(): v for k, v in r.headers.items()}, r.read())
        except urllib.error.HTTPError as e:
            resp = Response(e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read())
        self.log.append((f"{method.upper()} {path}", resp.status))
        return resp


def find_login(ops: list[Operation]) -> Operation | None:
    """Анонимная POST-операция с паролем в теле."""
    for op in ops:
        props = json.dumps(op.body_schema or {})
        if op.method == "post" and not op.secured and re.search(r"password|парол", props + op.text, re.I):
            return op
    return None


def login(client: Client, doc: dict, op: Operation, user: str, password: str) -> str:
    from .spec import resolve
    schema = resolve(doc, op.body_schema or {})
    props = list(schema.get("properties", {}))
    ufield = next((p for p in props if re.search(r"login|user|email|name", p, re.I)), "login")
    pfield = next((p for p in props if re.search(r"pass", p, re.I)), "password")
    r = client.send("post", op.path, headers={"Content-Type": "application/json"},
                    body=json.dumps({ufield: user, pfield: password}).encode())
    data = r.json() or {}
    token = data.get("accessToken") or data.get("access_token") or data.get("token")
    if r.status >= 300 or not token:
        raise RuntimeError(f"вход {user}: HTTP {r.status}")
    return token


def _res(check, category, op, title, ok, msg, t0, reqs) -> Result:
    return Result(check, category, op.name, title, "passed" if ok else "failed", "" if ok else msg,
                  round(time.monotonic() - t0, 3), reqs)


def run_checks(doc: dict, ops: list[Operation], client: Client, tokens: dict[str, str], ids: dict[str, str],
               rate_ops: set[str], burst: int = 40, log=print) -> list[Result]:
    results: list[Result] = []
    secured = [o for o in ops if o.secured]
    log(f"  операций: {len(ops)}, защищённых: {len(secured)}, ролей с токеном: {sorted(tokens)}")

    for op in secured:
        # --- authn: без токена и с поддельным
        for check, token, title in (("authn-missing", None, "401 без токена"),
                                    ("authn-invalid", "invalid." + uuid.uuid4().hex + ".token", "401 с поддельным токеном")):
            t0 = time.monotonic()
            path, q, h, b = build_request(doc, op, ids)
            r = client.send(op.method, path, q, h, b, token=token)
            results.append(_res(check, "authn", op, title, r.status == 401, f"ожидали 401, получили {r.status}",
                                t0, op.requirements))
        # --- authz: роли не из x-roles
        if op.roles:
            for role, token in tokens.items():
                if role in op.roles:
                    continue
                t0 = time.monotonic()
                path, q, h, b = build_request(doc, op, ids)
                r = client.send(op.method, path, q, h, b, token=token)
                results.append(_res("authz-role", "authz", op, f"403 для роли {role}", r.status == 403,
                                    f"роль {role} не в x-roles {op.roles}, ожидали 403, получили {r.status}",
                                    t0, op.requirements))
        # --- authz: чужой объект (случайный id в пути) для разрешённых ролей
        id_params = [p["name"] for p in op.params if p.get("in") == "path"]
        if op.method == "get" and id_params:
            for role, token in tokens.items():
                if op.roles is not None and role not in op.roles:
                    continue
                t0 = time.monotonic()
                foreign = {name: str(uuid.uuid4()) for name in id_params}
                path, q, h, b = build_request(doc, op, foreign)
                r = client.send(op.method, path, q, h, b, token=token)
                res = _res("authz-object", "authz-object", op, f"несуществующий id → 403/404 ({role})",
                           r.status in (403, 404), f"случайный id, роль {role}: ожидали 403/404, "
                           f"получили {r.status}", t0, op.requirements)
                if 200 <= r.status < 300 and not (r.body or b"").strip():
                    # данных не отдали — не доказанный IDOR, а отсутствие проверки существования/владельца
                    res.message += " (ответ без данных: сервер не проверяет существование и владельца объекта)"
                    res.severity = "5.3"
                results.append(res)
        # --- идемпотентность
        if op.idempotent:
            role = next((r for r in tokens if op.roles is None or r in op.roles), None)
            if role is None:
                results.append(Result("idem-missing-key", "idempotency", op.name, "без Idempotency-Key → 400",
                                      "skipped", "нет токена подходящей роли"))
            else:
                t0 = time.monotonic()
                path, q, h, b = build_request(doc, op, ids, idempotency_key=None)
                r = client.send(op.method, path, q, h, b, token=tokens[role])
                results.append(_res("idem-missing-key", "idempotency", op, "без Idempotency-Key → 400/422",
                                    r.status in (400, 422), f"ожидали 400/422, получили {r.status}", t0,
                                    op.requirements))
                t0 = time.monotonic()
                key = uuid.uuid4().hex
                path, q, h, b = build_request(doc, op, ids, idempotency_key=key)
                s1 = client.send(op.method, path, q, h, b, token=tokens[role]).status
                s2 = client.send(op.method, path, q, h, b, token=tokens[role]).status
                results.append(_res("idem-replay", "idempotency", op, "повтор с тем же ключом → тот же ответ",
                                    s1 == s2, f"первый ответ {s1}, повтор {s2}", t0, op.requirements))

    # --- лимит частоты — последним, затем ждём Retry-After
    wait = 0
    for op in [o for o in ops if o.op_id in rate_ops or o.name in rate_ops]:
        t0 = time.monotonic()
        role = next((r for r in tokens if op.roles is None or r in op.roles), None)
        token = tokens.get(role) if op.secured else None
        got = None
        for _ in range(burst):
            path, q, h, b = build_request(doc, op, ids)
            r = client.send(op.method, path, q, h, b, token=token)
            if r.status == 429:
                got = r
                break
        ok = got is not None and str(got.headers.get("retry-after", "")).strip().isdigit()
        msg = (f"{burst} запросов подряд — ни одного 429" if got is None
               else "429 без заголовка Retry-After")
        results.append(_res("rate-limit", "rate_limiting", op, f"всплеск {burst} запросов → 429 + Retry-After",
                            ok, msg, t0, op.requirements))
        if got is not None:
            wait = max(wait, min(int(got.headers.get("retry-after", "5") or 5), 60))
    if wait:
        log(f"  жду Retry-After {wait} с, чтобы не мешать следующим тестам")
        time.sleep(wait)

    # --- 5xx среди всех запросов проверок
    errors = [(name, st) for name, st in client.log if st >= 500]
    by_op: dict[str, list[int]] = {}
    for name, st in errors:
        by_op.setdefault(name.split("?")[0], []).append(st)
    for name, statuses in by_op.items():
        results.append(Result("no-5xx", "robustness", name, "нет 5xx на некорректных запросах", "failed",
                              f"ответы {sorted(set(statuses))}"))
    results.append(Result("no-5xx", "robustness", "*", f"всего запросов: {len(client.log)}, 5xx: {len(errors)}",
                          "passed" if not errors else "failed",
                          "" if not errors else f"{len(errors)} ответов 5xx"))
    return results
