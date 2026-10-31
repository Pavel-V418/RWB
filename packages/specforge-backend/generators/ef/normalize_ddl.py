"""Нормализация доменного DDL перед EF-scaffold (детерминированно, без LLM).

    python normalize_ddl.py <in.sql> <out.sql>

Enum-типы PostgreSQL (CREATE TYPE x AS ENUM (...)) EF-scaffold не поддерживает и молча пропускает
такие колонки. Заменяем их на varchar + CHECK (так же устроен эталон кейса 1):
  status parking_session_status NOT NULL DEFAULT 'active'
  → status varchar(N) NOT NULL DEFAULT 'active' CHECK (status IN ('active', ...))
Приведения 'x'::тип превращаются в 'x'::varchar. Остальной DDL не меняется.
"""

import re
import sys
from pathlib import Path

ENUM = re.compile(r"CREATE\s+TYPE\s+(?:\w+\.)?(\w+)\s+AS\s+ENUM\s*\(([^)]*)\)\s*;\s*\n?", re.IGNORECASE)


def main() -> None:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    sql = src.read_text(encoding="utf-8")
    enums = {m.group(1): re.findall(r"'((?:[^']|'')*)'", m.group(2)) for m in ENUM.finditer(sql)}
    sql = ENUM.sub("", sql)

    for name, values in enums.items():
        length = max(16, max((len(v) for v in values), default=1))
        allowed = ", ".join(f"'{v}'" for v in values)
        sql = re.sub(rf"::{name}\b", "::varchar", sql)
        # колонка: <имя> <тип>[[]] <остальные ограничения до запятой/конца строки>
        def column(m: re.Match) -> str:
            col, array, rest = m.group(1), m.group(2), m.group(3)
            if array:
                return f"{col} varchar({length})[]{rest}"
            return f"{col} varchar({length}){rest} CHECK ({col.strip()} IN ({allowed}))"
        sql = re.sub(rf"^(\s*\w+)\s+{name}\b(\[\])?([^,\n]*?)(?=,?\s*(?:--.*)?$)", lambda m: column(m), sql, flags=re.MULTILINE)

    dst.write_text(sql, encoding="utf-8")
    for name in enums:
        print(f"enum {name} → varchar + CHECK")


if __name__ == "__main__":
    main()
