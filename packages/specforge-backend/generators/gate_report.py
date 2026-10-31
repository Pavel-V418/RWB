"""Пишет gate.json для repair-агента: {"gate", "passed", "errors": [{"file", "line", "message"}]}.

    python gate_report.py <gate> <exit_code> <log_file> <out_json> [<base_dir>]

Разбирает ошибки dotnet/MSBuild, kotlinc, Python-traceback; остальное — последние строки лога одной ошибкой.
Пути делаются относительными к <base_dir>, дубликаты убираются, не больше 50 ошибок (экономим контекст LLM).
"""

import json
import re
import sys
from pathlib import Path

MAX_ERRORS = 50
PATTERNS = [
    # dotnet: /path/File.cs(12,5): error CS0103: The name 'x' does not exist [/path/proj.csproj]
    re.compile(r"^(?P<file>[^\s(][^(]*)\((?P<line>\d+),\d+\): error (?P<message>[A-Z]+\d+: .*?)(?: \[[^\]]+\])?$"),
    # kotlinc: e: file:///path/File.kt:12:5 Unresolved reference 'x'.
    re.compile(r"^e: (?:file://)?(?P<file>[^:]+):(?P<line>\d+):\d+ (?P<message>.*)$"),
    # Python: File "/path/x.py", line 12, in f  (+ сообщение — последняя строка трейсбэка)
    re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)'),
]


def parse(log: str, base: Path) -> list[dict]:
    errors, seen = [], set()
    lines = log.splitlines()
    for i, raw in enumerate(lines):
        line = raw.strip()
        for n, pattern in enumerate(PATTERNS):
            m = pattern.match(line)
            if not m:
                continue
            message = m.groupdict().get("message")
            if n == 2:  # Python: сообщение — первая строка без отступа после трейсбэка
                message = next((l.strip() for l in lines[i + 1:] if l and not l.startswith(" ")), "Traceback")
            file = m["file"]
            try:
                file = str(Path(file).resolve().relative_to(base))
            except ValueError:
                pass
            key = (file, m["line"], message)
            if key not in seen:
                seen.add(key)
                errors.append({"file": file, "line": int(m["line"]), "message": message})
            break
    return errors[-MAX_ERRORS:]


def main() -> None:
    gate, code, log_file, out = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    base = Path(sys.argv[5]).resolve() if len(sys.argv) > 5 else Path.cwd()
    log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
    errors = [] if code == 0 else parse(log, base)
    if code != 0 and not errors:
        tail = "\n".join(log.strip().splitlines()[-20:])
        errors = [{"file": None, "line": None, "message": tail or f"exit code {code}"}]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"gate": gate, "passed": code == 0, "errors": errors}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
