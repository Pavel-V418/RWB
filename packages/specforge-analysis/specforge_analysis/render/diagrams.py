"""Диаграммы из доменной модели и дизайна API.

PlantUML — для SA-документа: регламент требует PlantUML, чтобы диаграммы можно было редактировать.
Mermaid — для сайта документации (MkDocs рендерит его без плагинов).
Граф компонентов строится один раз (graph) и рисуется в обоих форматах.
"""

from __future__ import annotations

import re

from .contracts import Model

_ID = re.compile(r"[^A-Za-z0-9_]")


def _nid(s: str) -> str:
    return _ID.sub("_", s) or "n"


def _lbl(s: str) -> str:
    # '#' и ';' в Mermaid имеют служебный смысл (коды сущностей, конец оператора)
    return s.replace("#", "#35;").replace('"', "'").replace("\n", " ").replace(";", ",")


def _pl(s: str) -> str:
    return s.replace('"', "'").replace("\n", " ")


EDGE_RULES = [  # (из вида, в вид, подпись)
    ("mobile", "backend", "REST /api/v1, HTTPS + JWT"),
    ("web", "backend", "REST /api/v1 + обновления в реальном времени"),
    ("backend", "database", "SQL"),
    ("worker", "database", "SQL"),
    ("backend", "mock", "адаптер, REST"),
    ("mobile", "external", "deeplink / ссылка"),
]

MOBILE_HINTS = ("мобильн", "mobile", "смартфон", "приложени")


def graph(m: Model) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
    """(роль -> компонент), (компонент -> компонент, подпись)."""
    comps = m.design.components
    by_kind: dict[str, list] = {}
    for c in comps:
        by_kind.setdefault(c.kind, []).append(c)
    role_edges = []
    for r in m.req.roles:
        text = f"{r.name} {r.description}".lower()
        wants_mobile = any(w in text for w in MOBILE_HINTS)
        targets = by_kind.get("mobile", []) if wants_mobile else by_kind.get("web", [])
        if not targets:
            targets = by_kind.get("web", []) or by_kind.get("mobile", [])
        for t in targets[:1]:
            role_edges.append((r.key, t.name))
    edges: list[tuple[str, str, str]] = []
    seen = set()
    names = {c.name for c in comps}
    for broker in by_kind.get("broker", [])[:1]:  # рёбра брокера — из производителей и потребителей событий
        for ev in m.design.events:
            for src, dst, label in ([(ev.producer, broker.name, "публикует события")]
                                    + [(broker.name, c, "события") for c in ev.consumers]):
                if src in names and dst in names and (src, dst) not in seen and src != dst:
                    seen.add((src, dst))
                    edges.append((src, dst, label))
    for a, b, label in EDGE_RULES:
        for ca in by_kind.get(a, []):
            for cb in by_kind.get(b, []):
                key = (ca.name, cb.name)
                if key in seen or ca.name == cb.name:
                    continue
                seen.add(key)
                edges.append((ca.name, cb.name, label))
    return role_edges, edges


# =========================================================================== Mermaid (сайт документации)


def components_mermaid(m: Model) -> str:
    lines = ["flowchart LR"]
    for r in m.req.roles:
        lines.append(f'    {_nid("role_" + r.key)}(["{_lbl(r.name)}"])')
    for c in m.design.components:
        shape = {"database": ('[("', '")]'), "broker": ('[["', '"]]'), "external": ('{{"', '"}}'),
                 "mock": ('[/"', '"/]')}.get(c.kind, ('["', '"]'))
        lines.append(f'    {_nid(c.name)}{shape[0]}{_lbl(c.name)}<br/><small>{_lbl(c.tech)}</small>{shape[1]}')
    role_edges, edges = graph(m)
    lines += [f"    {_nid('role_' + r)} --> {_nid(c)}" for r, c in role_edges]
    lines += [f'    {_nid(a)} -->|"{label}"| {_nid(b)}' for a, b, label in edges]
    return "\n".join(lines)


_ER_TYPE = {"uuid": "uuid", "string": "string", "text": "text", "int": "int", "bigint": "bigint",
            "decimal": "decimal", "bool": "bool", "date": "date", "datetime": "timestamptz", "enum": "enum",
            "json": "jsonb", "point": "point", "polygon": "polygon", "linestring": "linestring"}


def er_mermaid(m: Model) -> str:
    lines = ["erDiagram"]
    for e in m.domain.entities:
        lines.append(f"    {e.name} {{")
        for f in m.fields(e.name):
            if f.name in ("createdAt", "updatedAt"):
                continue
            key = " PK" if f.name == "id" else (" FK" if f.ref else (" UK" if f.unique else ""))
            lines.append(f"        {_ER_TYPE[f.type]} {f.name}{key}")
        lines.append("    }")
    for e in m.domain.entities:
        for f in e.fields:
            if f.ref and f.ref in m.entities:
                card = "||--o{" if f.required else "|o--o{"
                lines.append(f'    {f.ref} {card} {e.name} : "{f.name}"')
    return "\n".join(lines)


def sequence_mermaid(seq) -> str:
    lines = ["sequenceDiagram", "    autonumber"]
    parts: list[str] = []
    for s in seq.steps:
        for p in (s.source, s.target):
            if p not in parts:
                parts.append(p)
    for p in parts:
        lines.append(f"    participant {_nid(p)} as {_lbl(p)}")
    for s in seq.steps:
        arrow = "-->>" if s.reply else "->>"
        lines.append(f"    {_nid(s.source)}{arrow}{_nid(s.target)}: {_lbl(s.message)}")
    return "\n".join(lines)


def state_mermaid(sm) -> str:
    lines = ["stateDiagram-v2", f"    [*] --> {_nid(sm.states[0])}"]
    for t in sm.transitions:
        lines.append(f"    {_nid(t.from_state)} --> {_nid(t.to_state)}: {_lbl(t.trigger)}")
    return "\n".join(lines)


# =========================================================================== PlantUML (SA-документ)


_PUML_KIND = {"database": "database", "broker": "queue", "external": "cloud", "mock": "component",
              "mobile": "component", "web": "component", "backend": "component", "worker": "component"}


def components_plantuml(m: Model) -> str:
    lines = ["@startuml", "left to right direction", "skinparam componentStyle rectangle"]
    for r in m.req.roles:
        lines.append(f'actor "{_pl(r.name)}" as {_nid("role_" + r.key)}')
    for c in m.design.components:
        stereo = " <<mock>>" if c.kind == "mock" else ""
        lines.append(f'{_PUML_KIND.get(c.kind, "component")} "{_pl(c.name)}\\n[{_pl(c.tech)}]" as {_nid(c.name)}{stereo}')
    role_edges, edges = graph(m)
    lines += [f"{_nid('role_' + r)} --> {_nid(c)}" for r, c in role_edges]
    lines += [f"{_nid(a)} --> {_nid(b)} : {_pl(label)}" for a, b, label in edges]
    return "\n".join(lines + ["@enduml"])


def sequence_plantuml(seq) -> str:
    lines = ["@startuml", "autonumber"]
    if seq.title:
        lines.append(f"title {_pl(seq.title)}")
    parts: list[str] = []
    for s in seq.steps:
        for p in (s.source, s.target):
            if p not in parts:
                parts.append(p)
    for p in parts:
        lines.append(f'participant "{_pl(p)}" as {_nid(p)}')
    for s in seq.steps:
        arrow = "-->" if s.reply else "->"
        lines.append(f"{_nid(s.source)} {arrow} {_nid(s.target)} : {_pl(s.message)}")
    return "\n".join(lines + ["@enduml"])


def er_plantuml(m: Model) -> str:
    from .contracts import snake, table_name
    lines = ["@startuml", "hide circle", "skinparam linetype ortho"]
    for e in m.domain.entities:
        lines.append(f'entity "{table_name(e.name)}" as {e.name} {{')
        fields = m.fields(e.name)
        lines.append(f"  * id : uuid <<PK>>")
        lines.append("  --")
        for f in fields:
            if f.name == "id":
                continue
            mark = "* " if f.required else "  "
            key = " <<FK>>" if f.ref else (" <<UK>>" if f.unique else "")
            lines.append(f"  {mark}{snake(f.name)} : {_ER_TYPE[f.type]}{key}")
        lines.append("}")
    for e in m.domain.entities:
        for f in e.fields:
            if f.ref and f.ref in m.entities:
                card = "||--o{" if f.required else "|o--o{"
                lines.append(f"{f.ref} {card} {e.name} : {snake(f.name)}")
    return "\n".join(lines + ["@enduml"])


def state_plantuml(sm) -> str:
    lines = ["@startuml", f"[*] --> {_nid(sm.states[0])}"]
    for t in sm.transitions:
        lines.append(f"{_nid(t.from_state)} --> {_nid(t.to_state)} : {_pl(t.trigger)}")
    return "\n".join(lines + ["@enduml"])
