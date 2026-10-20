"""Database backups with rotation.

SQLite: an online, consistent copy via the sqlite3 backup API (safe while the app runs).
PostgreSQL: pg_dump in custom format (pg_dump must be on PATH).
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url


def backup_database(database_url: str, dest_dir: Path, keep: int = 14) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        src = Path(url.database or "")
        if not src.exists():
            raise FileNotFoundError(f"SQLite database not found: {src}")
        out = dest_dir / f"trade1_{stamp}.db"
        with sqlite3.connect(src) as s, sqlite3.connect(out) as d:
            s.backup(d)
        pattern = "trade1_*.db"
    elif url.get_backend_name() == "postgresql":
        if shutil.which("pg_dump") is None:
            raise RuntimeError("pg_dump not found on PATH; install the PostgreSQL client tools")
        out = dest_dir / f"trade1_{stamp}.dump"
        env = {"PGPASSWORD": url.password or ""}
        cmd = ["pg_dump", "-Fc", "-h", url.host or "localhost", "-p", str(url.port or 5432),
               "-U", url.username or "postgres", "-f", str(out), url.database or "trade1"]
        subprocess.run(cmd, check=True, env={**os.environ, **env}, capture_output=True)
        pattern = "trade1_*.dump"
    else:
        raise RuntimeError(f"unsupported database for backup: {url.get_backend_name()}")
    for old in sorted(dest_dir.glob(pattern))[:-keep]:
        old.unlink()
    return out
