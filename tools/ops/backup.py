#!/usr/bin/env python3
"""Konzistentní SQLite záloha přes VACUUM INTO, checksum a retence."""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(database: Path, output_dir: Path, *, now: datetime | None = None,
                  retention_days: int = 14) -> Path:
    if retention_days < 1:
        raise ValueError("retention_days musí být alespoň 1")
    moment = now or datetime.now(timezone.utc)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"pardubicko-{moment:%Y%m%dT%H%M%SZ}.sqlite3"
    if target.exists():
        raise FileExistsError(f"Záloha už existuje: {target}")

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        escaped = str(target.resolve()).replace("'", "''")
        connection.execute(f"VACUUM INTO '{escaped}'")
    finally:
        connection.close()

    check = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if result != "ok":
        raise RuntimeError(f"Integrity check zálohy selhal: {result}")
    target.with_suffix(target.suffix + ".sha256").write_text(
        f"{sha256(target)}  {target.name}\n", encoding="ascii")

    cutoff = moment - timedelta(days=retention_days)
    for old in sorted(output_dir.glob("pardubicko-*.sqlite3")):
        if old == target:
            continue
        modified = datetime.fromtimestamp(old.stat().st_mtime, timezone.utc)
        if modified < cutoff:
            old.unlink()
            sidecar = old.with_suffix(old.suffix + ".sha256")
            if sidecar.exists():
                sidecar.unlink()
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("/data/pardubicko.db"))
    parser.add_argument("--output-dir", type=Path, default=Path("/backups"))
    parser.add_argument("--retention-days", type=int, default=14)
    args = parser.parse_args()
    print(create_backup(args.database, args.output_dir,
                        retention_days=args.retention_days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
