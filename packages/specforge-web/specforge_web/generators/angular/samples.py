"""Тестовые значения для полей форм — для e2e-сценариев и UI-ревью (без LLM, детерминированно).

Значение должно пройти ту же валидацию, что и в форме: pattern, minLength/maxLength, min/max.
Для pattern строится строка по регулярному выражению (поддерживается подмножество, которого хватает
для полей API: литералы, классы [...], \\d \\w \\s, квантификаторы ? * + {n} {n,m}, группы, первая
ветка «|»). Результат проверяется re.fullmatch — если не совпало, значения нет, и тест честно
пропускается с причиной, а не заполняется наугад.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

PREFERRED = {r"\d": "1", r"\w": "a", r"\s": " "}


class _Gen:
    def __init__(self, pattern: str):
        self.p = pattern.strip()
        if self.p.startswith("^"):
            self.p = self.p[1:]
        if self.p.endswith("$") and not self.p.endswith(r"\$"):
            self.p = self.p[:-1]
        self.i = 0

    def parse(self) -> str:
        out = self.alt()
        if self.i != len(self.p):
            raise ValueError("лишние символы")
        return out

    def alt(self) -> str:
        first = self.seq()
        while self.i < len(self.p) and self.p[self.i] == "|":
            self.i += 1
            self.seq()  # остальные ветки разбираем, но берём первую
        return first

    def seq(self) -> str:
        out = ""
        while self.i < len(self.p) and self.p[self.i] not in "|)":
            atom = self.atom()
            out += self.quant(atom)
        return out

    def atom(self) -> str:
        c = self.p[self.i]
        if c == "(":
            self.i += 1
            if self.p.startswith("?:", self.i):
                self.i += 2
            inner = self.alt()
            if self.i >= len(self.p) or self.p[self.i] != ")":
                raise ValueError("нет )")
            self.i += 1
            return inner
        if c == "[":
            return self.cls()
        if c == "\\":
            esc = self.p[self.i:self.i + 2]
            self.i += 2
            return PREFERRED.get(esc, esc[1])
        if c == ".":
            self.i += 1
            return "a"
        if c in "^$":
            self.i += 1
            return ""
        self.i += 1
        return c

    def cls(self) -> str:
        end = self.i + 1
        while end < len(self.p) and (self.p[end] != "]" or end == self.i + 1):
            end += 2 if self.p[end] == "\\" else 1
        body = self.p[self.i + 1:end]
        self.i = end + 1
        if body.startswith("^"):
            for ch in "a1A":
                if re.fullmatch(f"[{body}]", ch):
                    return ch
            raise ValueError("отрицательный класс")
        if body.startswith("\\"):
            return PREFERRED.get(body[:2], body[1])
        return body[0]

    def quant(self, atom: str) -> str:
        if self.i >= len(self.p):
            return atom
        c = self.p[self.i]
        if c in "?*":  # необязательный элемент — пропускаем
            self.i += 1
            self._lazy()
            return ""
        if c == "+":
            self.i += 1
            self._lazy()
            return atom
        if c == "{":
            end = self.p.index("}", self.i)
            spec = self.p[self.i + 1:end]
            self.i = end + 1
            self._lazy()
            n = int(spec.split(",")[0] or 0)
            return atom * n
        return atom

    def _lazy(self) -> None:
        if self.i < len(self.p) and self.p[self.i] == "?":
            self.i += 1


def from_pattern(pattern: str) -> str | None:
    """Строка, подходящая под pattern, или None, если выражение вне поддерживаемого подмножества."""
    try:
        s = _Gen(pattern).parse()
    except (ValueError, IndexError):
        return None
    try:
        return s if re.search(pattern, s) else None
    except re.error:
        return None


def fits(value: str, v: dict) -> bool:
    if "pattern" in v:
        try:
            if not re.search(v["pattern"], value):
                return False
        except re.error:
            return False
    return v.get("minLength", 0) <= len(value) <= v.get("maxLength", 10 ** 9)


FUTURE = re.compile(r"(end|until|expire|due|finish|deadline|to$)", re.I)


def field_plan(f: dict, example: object = None) -> dict:
    """План заполнения поля: {kind, value?, marker?, skip?}.
    kind: text | password | number | select | multiselect | date | datetime | toggle.
    marker=True — в поле кладётся уникальная метка прогона (по ней тест ищет созданную запись в списке)."""
    c, v, name = f.get("control", "text"), f.get("validation") or {}, f["name"]
    plan: dict = {"name": name, "kind": c, "required": bool(f.get("required"))}
    if c in ("text", "textarea"):
        if "pattern" in v:
            val = example if isinstance(example, str) and fits(example, v) else from_pattern(v["pattern"])
            if val is None or not fits(val, v):
                plan["skip"] = f"не удалось построить значение по шаблону {v['pattern']}"
            else:
                plan["value"] = val
        else:
            plan["marker"] = True
            plan["minLength"], plan["maxLength"] = v.get("minLength", 0), v.get("maxLength", 200)
    elif c == "password":
        val = "E2e-Passw0rd!"
        plan["value"] = (val * 4)[: max(v.get("minLength", 0), min(len(val), v.get("maxLength", len(val))))]
    elif c == "number":
        lo, hi = v.get("min"), v.get("max")
        val = example if isinstance(example, (int, float)) and not isinstance(example, bool) else (lo if lo is not None else 1)
        if lo is not None and val < lo:
            val = lo
        if hi is not None and val > hi:
            val = hi
        plan["value"] = val
    elif c in ("date", "datetime"):
        d = date.today() + timedelta(days=2 if FUTURE.search(name) else 1)
        plan["value"] = d.strftime("%d.%m.%Y") + (" 12:00" if c == "datetime" else "")
    elif c in ("select", "multiselect"):
        plan["fromApi"] = bool(f.get("optionsFrom"))
        if not f.get("optionsFrom") and not f.get("options"):
            plan["skip"] = "нет вариантов выбора"
    return plan
