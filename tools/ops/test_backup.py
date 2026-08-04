#!/usr/bin/env python3
"""Deterministický backup/restore test nad dočasnou SQLite databází."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from backup import create_backup  # noqa: E402
from restore import restore, verify  # noqa: E402


with tempfile.TemporaryDirectory(prefix="pardubicko-backup-test-") as temp:
    root = Path(temp)
    database = root / "source.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("INSERT INTO sample (value) VALUES ('auditovatelná hodnota')")
    connection.commit()
    connection.close()

    backup = create_backup(
        database, root / "backups",
        now=datetime(2026, 8, 3, tzinfo=timezone.utc), retention_days=14)
    verify(backup)
    restored = restore(backup, root / "restored.db")
    restored_connection = sqlite3.connect(restored)
    value = restored_connection.execute("SELECT value FROM sample").fetchone()[0]
    restored_connection.close()
    assert value == "auditovatelná hodnota"

print("SQLite backup, checksum a restore test prošly.")
