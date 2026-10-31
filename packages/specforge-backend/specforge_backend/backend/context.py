"""Сборка компактного контекста для одного тега API (без LLM, детерминированно).

Источники:
  04_backend: Apis.cs (интерфейс I<Tag>Api), Models.cs (DTO), сущности EF, IAppDbContext, db/100_domain.sql
  02_system:  openapi.yaml (summary/description операций), api-methods/<operationId>.md (алгоритм, таблицы)
  01_requirements/requirements.json — тексты FR, на которые ссылаются страницы методов

Всё сжимается: убираются атрибуты сериализации, длинные пространства имён, XML-комментарии сводятся к строке.
Лимит модели — 32k токенов на вход и 30k токенов в минуту на ключ, поэтому каждый символ на счету.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

APIS = "src/App.Application/Contracts/Generated/Apis.cs"
MODELS = "src/App.Application/Contracts/Generated/Models.cs"
ENTITIES = "src/App.Domain/Entities/Generated"
DB_CONTEXT = "src/App.Application/Abstractions/IAppDbContext.Generated.cs"
DOMAIN_SQL = "db/100_domain.sql"
INTEGRATIONS = "src/App.Application/Integrations"

ANONYMOUS = {"anonymous", "public", "guest"}  # маркеры x-roles «без токена», как в generators/server/gen_aspnet.py
SHORT_NAMES = [
    ("System.Threading.Tasks.Task", "Task"),
    ("System.Threading.CancellationToken", "CancellationToken"),
    ("System.Collections.Generic.IReadOnlyList", "IReadOnlyList"),
    ("System.Text.Json.Nodes.JsonObject", "JsonObject"),
    ("System.Text.Json.JsonElement", "JsonElement"),
    ("NetTopologySuite.Geometries.Geometry", "Geometry"),
    ("System.DateTimeOffset", "DateTimeOffset"),
    ("System.DateOnly", "DateOnly"),
    ("System.Guid", "Guid"),
    ("global::App.Application.Contracts.", ""),
]


def feature_namespace(backend: Path, tag: str) -> str:
    """Namespace фичи. Тег, совпадающий с именем сущности (MovementHistory), получает суффикс Feature: иначе
    namespace Features.MovementHistory перекрывает тип App.Domain.Entities.MovementHistory во ВСЕХ соседних фичах (CS0118)."""
    entities = {p.stem for p in (backend / ENTITIES).glob("*.cs")}
    return f"App.Application.Features.{tag}Feature" if tag in entities else f"App.Application.Features.{tag}"


def shorten(code: str) -> str:
    for long, short in SHORT_NAMES:
        code = code.replace(long, short)
    return code


def compact_cs(code: str) -> str:
    """Убирает шум из сгенерированного C#: заголовки, using, атрибуты, многострочные summary."""
    code = re.sub(r"^// .*\n|^#nullable.*\n|^using .*\n|^namespace .*\n", "", code, flags=re.MULTILINE)
    # enum: [JsonStringEnumMemberName("active")] Active, → Active, // "active"
    code = re.sub(r'\[System\.Text\.Json\.Serialization\.JsonStringEnumMemberName\("([^"]*)"\)\]\n\s*(\w+),',
                  r'\2, // "\1"', code)
    code = re.sub(r"^\s*\[System\.Text\.Json\.Serialization\.JsonConverter\(.*\)\]\n", "", code, flags=re.MULTILINE)
    code = re.sub(r"^\s*\[System\.Text\.Json\.Serialization\.JsonPropertyName\(.*\)\]\n", "", code, flags=re.MULTILINE)
    code = re.sub(r"^\s*\[System\.ComponentModel\.DataAnnotations\..*\]\n", "", code, flags=re.MULTILINE)
    code = re.sub(r"(\s*)/// <summary>\s*\n((?:\s*/// .*\n)*?)\s*/// </summary>\n",
                  lambda m: m.group(1) + "// " + " ".join(l.strip()[4:] for l in m.group(2).splitlines() if l.strip()) + "\n",
                  code)
    code = re.sub(r"\n\s*\n", "\n", shorten(code))
    return code.strip() + "\n"


def top_level_blocks(code: str) -> dict[str, str]:
    """Models.cs/Apis.cs → {имя типа: блок кода с комментарием}."""
    blocks: dict[str, str] = {}
    # [^\n]* вместо .*: при DOTALL «.*» съел бы весь файл
    pattern = re.compile(r"((?:^///[^\n]*\n|^\[[^\n]*\]\n)*)^public (?:sealed record|enum|interface) (\w+)\n\{\n.*?^\}\n", re.MULTILINE | re.DOTALL)
    for m in pattern.finditer(code):
        blocks[m.group(2)] = m.group(0)
    return blocks


@dataclass
class Operation:
    method: str          # LookupZoneAsync
    operation_id: str    # lookupZone
    signature: str
    summary: str = ""
    description: str = ""
    path: str = ""
    algorithm: str = ""
    requirements: list[str] = field(default_factory=list)
    roles: list[str] | None = None  # x-roles; [] — анонимно, None — не задано


@dataclass
class TagContext:
    tag: str
    interface: str
    operations: list[Operation]
    dtos: str
    entities: str
    db_sets: str
    constraints: str
    integrations: str = ""

    def render(self) -> str:
        ops = []
        for op in self.operations:
            lines = [f"### {op.method} — {op.path}", op.summary]
            if op.roles is not None:
                lines.append("Роли: " + (", ".join(op.roles) if op.roles else "анонимно (без токена)"))
            if op.description and op.description != op.summary:
                lines.append(op.description)
            if op.algorithm:
                lines.append("Алгоритм (из спецификации аналитика):\n" + op.algorithm)
            if op.requirements:
                lines.append("Требования:\n" + "\n".join(f"- {r}" for r in op.requirements))
            ops.append("\n".join(l for l in lines if l))
        return "\n\n".join([
            f"## Интерфейс, который нужно реализовать\n```csharp\n{self.interface}```",
            "## Операции\n" + "\n\n".join(ops),
            f"## DTO контракта (namespace App.Application.Contracts)\n```csharp\n{self.dtos}```",
            f"## Сущности EF (namespace App.Domain.Entities)\n```csharp\n{self.entities}```",
            f"## DbSet-ы IAppDbContext\n```csharp\n{self.db_sets}```",
            f"## Ограничения БД (допустимые строковые значения и т. п.)\n```sql\n{self.constraints}```",
        ] + ([f"## Внешние интеграции (namespace App.Application.Integrations, внедряй через конструктор)\n```csharp\n{self.integrations}```"]
             if self.integrations else []))


class ContextBuilder:
    def __init__(self, artifacts: Path):
        self.artifacts = artifacts
        self.backend = artifacts / "04_backend"
        self.apis = top_level_blocks((self.backend / APIS).read_text(encoding="utf-8"))
        self.models = top_level_blocks((self.backend / MODELS).read_text(encoding="utf-8"))
        spec_path = artifacts / "02_system" / "openapi.yaml"
        self.spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) if spec_path.exists() else {"paths": {}}
        self.requirements = self._load_requirements()

    def tags(self) -> list[str]:
        return [name[1:-3] for name in self.apis if name.startswith("I") and name.endswith("Api")]

    def _load_requirements(self) -> dict[str, str]:
        path = self.artifacts / "01_requirements" / "requirements.json"
        if not path.exists():
            return {}
        doc = json.loads(path.read_text(encoding="utf-8"))
        return {fr["id"]: f'{fr["id"]} {fr.get("title", "")}: {fr.get("statement", "")}' for fr in doc.get("functional", [])}

    def _operations_by_method(self) -> dict[str, tuple[str, str, dict]]:
        result = {}
        for path, item in self.spec.get("paths", {}).items():
            for verb, op in item.items():
                if isinstance(op, dict) and "operationId" in op:
                    method = op["operationId"][:1].upper() + op["operationId"][1:] + "Async"
                    result[method.lower()] = (op["operationId"], f"{verb.upper()} {path}", op)
        return result

    def _method_doc(self, operation_id: str) -> tuple[str, list[str]]:
        page = self.artifacts / "02_system" / "api-methods" / f"{operation_id}.md"
        if not page.exists():
            return "", []
        text = page.read_text(encoding="utf-8")
        algorithm = ""
        m = re.search(r"^## 4\..*?\n(.*?)(?=^## 5\.|\Z)", text, re.MULTILINE | re.DOTALL)
        if m:
            algorithm = re.sub(r"\n{2,}", "\n", m.group(1)).strip()
        t = re.search(r"^### Взаимодействие с таблицами\n(.*?)(?=^#|\Z)", text, re.MULTILINE | re.DOTALL)
        if t:
            algorithm += "\nТаблицы:\n" + re.sub(r"\n{2,}", "\n", t.group(1)).strip()
        fr_ids = re.findall(r"FR-\d+", (re.search(r"Реализует требования.*", text) or [""])[0])
        return algorithm, [self.requirements[i] for i in fr_ids if i in self.requirements]

    def _dto_closure(self, roots: str) -> list[str]:
        names = [n for n in self.models if re.search(rf"\b{n}\b", roots)]
        seen: list[str] = []
        while names:
            name = names.pop(0)
            if name in seen:
                continue
            seen.append(name)
            names += [n for n in self.models if n not in seen and re.search(rf"\b{n}\b", self.models[name].split("\n", 1)[1])]
        return seen

    def build(self, tag: str) -> TagContext:
        interface = self.apis[f"I{tag}Api"]
        ops_meta = self._operations_by_method()
        operations = []
        for m in re.finditer(r"^\s*(?:///.*\n\s*)*(.+?\s(\w+Async)\(.*\);)$", interface, re.MULTILINE):
            signature, method = shorten(m.group(1)), m.group(2)
            op_id, path, op = ops_meta.get(method.lower(), (method[:-5], "", {}))
            algorithm, reqs = self._method_doc(op_id)
            # x-requirements контракта точнее страницы метода и есть даже там, где страниц нет
            x_reqs = [self.requirements[i] for i in op.get("x-requirements") or [] if i in self.requirements]
            reqs = x_reqs or reqs
            roles = op.get("x-roles")
            roles = [r for r in roles if r not in ANONYMOUS] if isinstance(roles, list) else None
            operations.append(Operation(method, op_id, signature, op.get("summary", ""),
                                        (op.get("description") or "").strip(), path, algorithm, reqs, roles))

        dtos = "".join(compact_cs(self.models[n]) for n in self._dto_closure(interface))
        entities = "".join(
            compact_cs(p.read_text(encoding="utf-8")) for p in sorted((self.backend / ENTITIES).glob("*.cs")))
        db_sets = "\n".join(l.strip() for l in (self.backend / DB_CONTEXT).read_text(encoding="utf-8").splitlines()
                            if l.strip().startswith("DbSet<"))
        integrations_dir = self.backend / INTEGRATIONS
        integrations = "".join(compact_cs(p.read_text(encoding="utf-8")) for p in sorted(integrations_dir.glob("*.cs"))) \
            if integrations_dir.exists() else ""
        return TagContext(tag, compact_cs(interface), operations, dtos, entities, db_sets, self._constraints(), integrations)

    def _constraints(self) -> str:
        sql_path = self.backend / DOMAIN_SQL
        if not sql_path.exists():
            return ""
        sql = sql_path.read_text(encoding="utf-8")
        lines = []
        # CHECK может растягиваться на несколько строк (длинный IN (...)) — ищем по балансу скобок во всём тексте
        tables = [(m.start(), m.group(1)) for m in re.finditer(r"CREATE TABLE (\w+)", sql, re.IGNORECASE)]
        for m in re.finditer(r"CHECK\s*\(", sql, re.IGNORECASE):
            depth, i = 1, m.end()
            while i < len(sql) and depth:
                depth += {"(": 1, ")": -1}.get(sql[i], 0)
                i += 1
            body = " ".join(sql[m.end():i - 1].split())
            table = next((name for pos, name in reversed(tables) if pos < m.start()), None)
            if table and re.search(r"\bIN\s*\(|~", body):
                lines.append(f"{table}: {body}")
        # Длины строк: значение длиннее varchar(n) — ошибка 22001 при сохранении (частая ошибка тестовых данных)
        for pos, table in tables:
            end = sql.find(";", pos)
            cols = [f"{c} {t}" for c, t in re.findall(r"^\s+(\w+)\s+(varchar\(\d+\)|char\(\d+\))", sql[pos:end], re.MULTILINE | re.IGNORECASE)]
            if cols:
                lines.append(f"{table}: " + ", ".join(cols))
        # Готовые SQL-функции схемы (например, геопоиск) — только сигнатуры и комментарии над ними
        for f in re.finditer(r"((?:--.*\n)*)CREATE (?:OR REPLACE )?FUNCTION ([^\n(]+\([^)]*\))\s*RETURNS ([^\n]*)", sql, re.IGNORECASE):
            comment = f.group(1).strip()
            lines.append((comment + "\n" if comment else "") + f"FUNCTION {f.group(2).strip()} RETURNS {f.group(3).strip()}")
        return "\n".join(lines) + "\n"


JOBS_TAG = "Jobs"
TIME_WORDS = re.compile(r"мин|час|врем|истек|истёк|окончан|просроч|таймер|напомин|предупрежд|срок|ежеднев|по расписан|timeout|expire", re.IGNORECASE)


@dataclass
class JobsContext:
    requirements: str
    entities: str
    db_sets: str
    constraints: str
    conventions: str
    integrations: str
    enums: str = ""

    def render(self) -> str:
        parts = [
            f"## Требования (сначала связанные со временем)\n{self.requirements}",
            f"## Сущности EF (namespace App.Domain.Entities)\n```csharp\n{self.entities}```",
            f"## DbSet-ы IAppDbContext\n```csharp\n{self.db_sets}```",
            f"## Ограничения БД\n```sql\n{self.constraints}```",
            f"## Соглашения уже написанного кода API (используй те же типы событий, топики, виды уведомлений)\n```csharp\n{self.conventions}```",
        ]
        if self.enums:
            parts.append(f"## Enum'ы контракта (значения для БД и событий — в комментариях)\n```csharp\n{self.enums}```")
        if self.integrations:
            parts.append(f"## Внешние интеграции\n```csharp\n{self.integrations}```")
        return "\n\n".join(parts)


def build_jobs(builder: "ContextBuilder") -> JobsContext:
    """Контекст для фоновых задач: требования про время, данные и соглашения соседнего кода."""
    reqs = list(builder.requirements.values())
    timed = [r for r in reqs if TIME_WORDS.search(r)]
    other = [r for r in reqs if r not in timed]
    requirements = "\n".join(f"- {r}" for r in timed + other[:10]) or "(требований нет — ориентируйся на схему БД: поля *_until, *_at, статусы)"

    conventions = []
    features = builder.backend / "src/App.Application/Features"
    for path in sorted(features.rglob("*.cs")) if features.exists() else []:
        if path.parent.name == JOBS_TAG:
            continue
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"internal sealed record \w+\([^)]*\)\s*:\s*IIntegrationEvent\s*\{.*?\n\}", text, re.DOTALL):
            conventions.append(re.sub(r"\n\s*\n", "\n", m.group(0)))
        for m in list(re.finditer(r"new Notification\s*\{.*?\};", text, re.DOTALL))[:2]:
            conventions.append(f"// {path.parent.name}: создание уведомления\n" + m.group(0))
        for m in list(re.finditer(r"new DomainEvent\s*\{.*?\};", text, re.DOTALL))[:1]:
            conventions.append(f"// {path.parent.name}: запись в журнал событий\n" + m.group(0))
    ctx = builder.build(builder.tags()[0]) if builder.tags() else None
    return JobsContext(requirements, ctx.entities if ctx else "", ctx.db_sets if ctx else "", ctx.constraints if ctx else "",
                       "\n\n".join(dict.fromkeys(conventions)) or "(соседнего кода нет)", ctx.integrations if ctx else "",
                       "".join(compact_cs(code) for name, code in builder.models.items() if code.lstrip("/ \n").startswith("public enum")
                               or "\npublic enum" in code))
