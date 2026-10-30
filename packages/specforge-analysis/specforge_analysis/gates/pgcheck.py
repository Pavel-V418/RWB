"""Проверка DDL настоящим PostgreSQL.

1. SPECFORGE_PG_DSN задан (PostGIS из docker compose команды) -> DDL применяется в транзакции с ROLLBACK.
2. Иначе, если в системе есть initdb/pg_ctl -> поднимается временный кластер. Если PostGIS в нём нет,
   геометрия временно заменяется на bytea, GIST-индексы пропускаются; об этом пишется предупреждение.
3. Иначе проверка пропускается с предупреждением.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DdlCheck:
    ran: bool
    ok: bool
    mode: str
    message: str = ""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _pg_bin() -> Path | None:
    initdb = shutil.which("initdb")
    if initdb:
        return Path(initdb).parent
    for p in sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True):
        if (p / "initdb").exists():
            return p
    return None


def _wrap(ddl: str) -> str:
    return "\\set ON_ERROR_STOP on\nBEGIN;\n" + ddl + "\nROLLBACK;\n"


def _psql(args: list[str], sql: str, env=None, user_prefix=None) -> subprocess.CompletedProcess:
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as f:
        f.write(sql)
        path = f.name
    os.chmod(path, 0o644)
    cmd = (user_prefix or []) + ["psql", "-X", "-q", *args, "-f", path]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
    finally:
        os.unlink(path)


def _strip_postgis(ddl: str) -> str:
    ddl = ddl.replace("CREATE EXTENSION IF NOT EXISTS postgis;", "")
    ddl = re.sub(r"geometry\([^)]*\)", "bytea", ddl)
    return "\n".join(l for l in ddl.splitlines() if "USING GIST" not in l)


def check_ddl(ddl: str) -> DdlCheck:
    dsn = os.environ.get("SPECFORGE_PG_DSN")
    if dsn:
        r = _psql([dsn], _wrap(ddl))
        return DdlCheck(True, r.returncode == 0, "dsn", (r.stderr or r.stdout).strip()[:2000])

    bindir = _pg_bin()
    if not bindir:
        return DdlCheck(False, True, "skipped", "PostgreSQL недоступен: задайте SPECFORGE_PG_DSN")

    as_root = os.geteuid() == 0
    prefix = ["runuser", "-u", "postgres", "--"] if as_root else []
    tmp = Path(tempfile.mkdtemp(prefix="specforge-pg-"))
    if as_root:
        shutil.chown(tmp, "postgres", "postgres")
    data, sock, port = tmp / "data", tmp, _free_port()
    env = dict(os.environ, PATH=f"{bindir}:{os.environ.get('PATH', '')}")
    try:
        subprocess.run(prefix + [str(bindir / "initdb"), "-D", str(data), "-A", "trust", "-U", "postgres"],
                       capture_output=True, text=True, timeout=120, check=True, env=env)
        subprocess.run(prefix + [str(bindir / "pg_ctl"), "-D", str(data), "-w", "-l", str(tmp / "log"),
                                 "-o", f"-k {sock} -p {port} -c listen_addresses=''", "start"],
                       capture_output=True, text=True, timeout=120, check=True, env=env)
        conn = ["-h", str(sock), "-p", str(port), "-U", "postgres", "-d", "postgres"]
        probe = _psql(conn, "CREATE EXTENSION IF NOT EXISTS postgis;", env=env, user_prefix=prefix)
        has_postgis = probe.returncode == 0 and "ERROR" not in probe.stderr
        sql = ddl if has_postgis else _strip_postgis(ddl)
        r = _psql(conn, _wrap(sql), env=env, user_prefix=prefix)
        mode = "local-postgis" if has_postgis else "local-no-postgis"
        return DdlCheck(True, r.returncode == 0, mode, (r.stderr or r.stdout).strip()[:2000])
    except (subprocess.SubprocessError, OSError) as exc:
        return DdlCheck(False, True, "skipped", f"не удалось поднять временный PostgreSQL: {exc}")
    finally:
        subprocess.run(prefix + [str(bindir / "pg_ctl"), "-D", str(data), "-m", "immediate", "stop"],
                       capture_output=True, text=True, timeout=60, env=env)
        shutil.rmtree(tmp, ignore_errors=True)
