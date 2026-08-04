#!/usr/bin/env python3
"""Ověří zálohu SQLite a explicitně ji obnoví do zvolené cesty."""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from backup import sha256


def verify(backup: Path) -> None:
    sidecar = backup.with_suffix(backup.suffix + ".sha256")
    if not sidecar.is_file():
        raise FileNotFoundError(f"Chybí checksum: {sidecar}")
    expected = sidecar.read_text(encoding="ascii").split()[0]
    actual = sha256(backup)
    if actual != expected:
        raise ValueError(f"Checksum nesouhlasí: očekáván {expected}, nalezen {actual}")
    connection = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        connection.close()
    if result != "ok":
        raise ValueError(f"Integrity check selhal: {result}")


def restore(backup: Path, target: Path, *, replace: bool = False) -> Path:
    verify(backup)
    if target.exists() and not replace:
        raise FileExistsError("Cíl existuje; pro vědomé přepsání použij --replace.")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(backup, temporary)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("--target", type=Path, default=Path("/data/pardubicko.db"))
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.verify_only:
        verify(args.backup)
        print("Záloha je v pořádku.")
    else:
        print(restore(args.backup, args.target, replace=args.replace))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
