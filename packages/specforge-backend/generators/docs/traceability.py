"""Матрица трассируемости «требование → API → код → тест» по готовому backend — без LLM.

    python generators/docs/traceability.py <artifacts_dir>

Связи берутся из артефактов, ничего не угадывается:
- FR операции: `x-requirements` в 02_system/openapi.yaml, иначе строка «Реализует требования» страницы 02_system/api-methods;
- код: метод `<OperationId>Async` в 04_backend/src/App.Application/Features/<Tag>/*.cs (заглушка 501 помечается);
- тест: имя `Operation_Condition_Result` (так их пишет testgen) → операция → её FR; если у теста есть
  `[Trait("AC", "AC-FR-n-mm")]`, берутся FR этих критериев (точнее, чем все FR операции).
Выход: 04_backend/traceability.md (таблица для документации и жюри) и столбец `tests` в 02_system/traceability.csv
участника 2 (остальные столбцы не трогаются). Одинаковый вход — одинаковый выход.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

VERBS = ("get", "post", "put", "patch", "delete")
FEATURES = "04_backend/src/App.Application/Features"
TESTS = "04_backend/tests/App.IntegrationTests/Features"


@dataclass
class Operation:
    op_id: str
    endpoint: str
    tag: str
    frs: list[str]
    code: str = ""  # путь файла реализации
    stub: bool = False


@dataclass
class Test:
    name: str  # Класс.Метод
    skipped: bool
    acs: list[str] = field(default_factory=list)
    op: Operation | None = None


def pascal(s: str) -> str:
    return s[:1].upper() + s[1:]


def operations(art: Path) -> dict[str, Operation]:
    spec = yaml.safe_load((art / "02_system/openapi.yaml").read_text(encoding="utf-8"))
    ops: dict[str, Operation] = {}
    for path, item in spec.get("paths", {}).items():
        for verb in VERBS:
            op = item.get(verb)
            if not op or "operationId" not in op:
                continue
            frs = list(op.get("x-requirements") or [])
            page = art / "02_system/api-methods" / f"{op['operationId']}.md"
            if not frs and page.exists():
                line = re.search(r"Реализует требования.*", page.read_text(encoding="utf-8"))
                frs = re.findall(r"FR-\d+", line.group(0)) if line else []
            tag = re.sub(r"[^0-9A-Za-z]", "", pascal((op.get("tags") or ["Default"])[0]))
            ops[pascal(op["operationId"])] = Operation(op["operationId"], f"{verb.upper()} {path}", tag, frs)
    return ops


def method_body(text: str, start: int) -> str:
    i = text.find("{", start)
    depth, j = 1, i + 1
    while j < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        j += 1
    return text[i:j]


def link_code(art: Path, ops: dict[str, Operation]) -> None:
    for path in sorted((art / FEATURES).rglob("*.cs")):
        text = path.read_text(encoding="utf-8")
        for name, op in ops.items():
            m = re.search(rf"^[ \t]*public\b[^\n;=]*\b{name}Async\s*\(", text, re.MULTILINE)
            if m and not op.code:
                op.code = path.relative_to(art / "04_backend").as_posix()
                op.stub = "FeatureNotImplementedException" in method_body(text, m.end())


def tests(art: Path, ops: dict[str, Operation]) -> list[Test]:
    result = []
    by_lower = {k.lower(): v for k, v in ops.items()}
    for path in sorted((art / TESTS).glob("*Tests.cs")):
        text = path.read_text(encoding="utf-8")
        # атрибуты над методом: [Fact], [Fact(Skip = ...)], [Trait("AC", "...")] в любом порядке
        for m in re.finditer(r"((?:^[ \t]*\[[^\n]*\]\s*\n)+)[ \t]*public\s+async\s+Task\s+(\w+)\s*\(", text, re.MULTILINE):
            attrs, name = m.group(1), m.group(2)
            if "[Fact" not in attrs and "[Theory" not in attrs:
                continue
            t = Test(f"{path.stem}.{name}", "Skip" in attrs, re.findall(r'Trait\(\s*"AC"\s*,\s*"([^"]+)"', attrs))
            t.op = by_lower.get(name.split("_", 1)[0].lower())
            result.append(t)
    return result


def requirements(art: Path) -> tuple[list[tuple[str, str]], dict[str, str]]:
    path = art / "01_requirements/requirements.json"
    if not path.exists():
        return [], {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    frs = [(fr["id"], fr.get("title", "")) for fr in doc.get("functional", [])]
    ac_fr = {ac["id"]: ac.get("fr_id", "") for ac in doc.get("acceptance", [])}
    return frs, ac_fr


def test_frs(t: Test, ac_fr: dict[str, str]) -> list[str]:
    if t.acs:
        return sorted({ac_fr.get(ac) or re.sub(r"^AC-(FR-\d+).*", r"\1", ac) for ac in t.acs})
    return t.op.frs if t.op else []


def fr_sort(fr: str) -> tuple[int, str]:
    m = re.search(r"\d+", fr)
    return (int(m.group(0)) if m else 0, fr)


def render(art: Path) -> tuple[str, dict[str, list[str]]]:
    ops = operations(art)
    link_code(art, ops)
    all_tests = tests(art, ops)
    frs, ac_fr = requirements(art)
    known = {fr for fr, _ in frs}
    for op in ops.values():
        known.update(op.frs)
    titles = dict(frs)

    fr_ops = {fr: [op for op in ops.values() if fr in op.frs] for fr in known}
    fr_tests: dict[str, list[Test]] = {fr: [] for fr in known}
    for t in all_tests:
        for fr in test_frs(t, ac_fr):
            fr_tests.setdefault(fr, []).append(t)

    lines = [
        "# Трассируемость backend: требование → API → код → тест",
        "",
        "Сгенерировано `generators/docs/traceability.py` из OpenAPI, кода фич и интеграционных тестов.",
        "Тест «пропущен» — `[Fact(Skip=...)]`: агент не смог добиться прохождения, причина в тексте Skip.",
        "",
        "| Требование | Операции API | Реализация | Тесты |",
        "| --- | --- | --- | --- |",
    ]
    covered = implemented = 0
    tests_col: dict[str, list[str]] = {}
    for fr in sorted(known, key=fr_sort):
        ops_ = fr_ops.get(fr, [])
        ts = fr_tests.get(fr, [])
        api = "<br>".join(f"`{op.endpoint}`" for op in ops_) or "—"
        code = "<br>".join(sorted({f"`{op.code}`" + (" (заглушка 501)" if op.stub else "") for op in ops_ if op.code})) or "—"
        if ops_ and all(op.code and not op.stub for op in ops_):
            implemented += 1
        passed = [t for t in ts if not t.skipped]
        if passed:
            covered += 1
        cell = "<br>".join(f"`{t.name}`" + (" — пропущен" if t.skipped else "") for t in ts) or "—"
        lines.append(f"| {fr} {titles.get(fr, '')} | {api} | {code} | {cell} |")
        tests_col[fr] = [t.name for t in passed]
    unlinked = [t.name for t in all_tests if not test_frs(t, ac_fr)]
    lines += [
        "",
        f"Итого: требований {len(known)}, реализовано полностью {implemented}, покрыто тестами {covered}; "
        f"операций {len(ops)}, тестов {len(all_tests)} (пропущено {sum(t.skipped for t in all_tests)}).",
    ]
    if unlinked:
        lines += ["", "Тесты без связи с требованием (операция без FR или имя не по соглашению): "
                  + ", ".join(f"`{n}`" for n in unlinked) + "."]
    return "\n".join(lines) + "\n", tests_col


def fill_csv(path: Path, tests_col: dict[str, list[str]]) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "tests" not in rows[0] or "requirement" not in rows[0]:
        return False
    fields = list(rows[0].keys())
    for row in rows:
        if row["requirement"] in tests_col:
            row["tests"] = "; ".join(tests_col[row["requirement"]])
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(out.getvalue(), encoding="utf-8")
    return True


def main() -> None:
    art = Path(sys.argv[1]).resolve()
    markdown, tests_col = render(art)
    out = art / "04_backend/traceability.md"
    out.write_text(markdown, encoding="utf-8")
    csv_done = fill_csv(art / "02_system/traceability.csv", tests_col)
    print(f"traceability: {out.relative_to(art)}" + (", 02_system/traceability.csv (tests)" if csv_done else ""))


if __name__ == "__main__":
    main()
