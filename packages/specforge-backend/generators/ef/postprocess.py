"""Пост-обработка результата dotnet ef scaffold.

    python postprocess.py <AppDbContext.Generated.cs> <entities_dir>

Литеральные значения по умолчанию (`.HasDefaultValue(true)`, `.HasDefaultValue((short)20)`) переносятся
из fluent-конфигурации в инициализатор свойства сущности. Иначе EF считает CLR-значение по умолчанию
(false, 0) «не задано» и подставляет DEFAULT из БД: `IsActive = false` молча сохранится как true.
`HasDefaultValueSql(...)` (now(), gen_random_uuid()) не трогаем — это генерируемые БД значения.
"""

import re
import sys
from pathlib import Path

ENTITY = re.compile(r"modelBuilder\.Entity<(\w+)>\(entity =>")
PROPERTY_DEFAULT = re.compile(r"(entity\.Property\(e => e\.(\w+)\))\s*\n\s*\.HasDefaultValue\((.+?)\)(\s*\n\s*\.|;)")


def main() -> None:
    context, entities = Path(sys.argv[1]), Path(sys.argv[2])
    text = context.read_text(encoding="utf-8")
    moved: list[tuple[str, str, str]] = []

    # Блоки modelBuilder.Entity<T>(...) идут подряд — режем текст по их началам
    starts = [(m.start(), m.group(1)) for m in ENTITY.finditer(text)] + [(len(text), "")]
    out, pos = [], 0
    for (start, entity), (end, _) in zip(starts, starts[1:]):
        out.append(text[pos:start])
        block = text[start:end]

        def repl(m: re.Match, entity: str = entity) -> str:
            moved.append((entity, m.group(2), m.group(3)))
            tail = m.group(4)
            return m.group(1) + ("\n                ." if tail.strip() == "." else ";")

        out.append(PROPERTY_DEFAULT.sub(repl, block))
        pos = end
    out.append(text[pos:])
    context.write_text("".join(out), encoding="utf-8")

    for entity, prop, value in moved:
        path = entities / f"{entity}.cs"
        src = path.read_text(encoding="utf-8")
        pattern = re.compile(rf"(public \S+ {prop} \{{ get; set; \}})(?! =)")
        src, n = pattern.subn(rf"\1 = {value};", src)
        if n != 1:
            sys.exit(f"postprocess: не нашёл свойство {entity}.{prop}")
        path.write_text(src, encoding="utf-8")
        print(f"default moved to initializer: {entity}.{prop} = {value}")


if __name__ == "__main__":
    main()
