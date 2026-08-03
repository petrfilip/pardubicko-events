#!/usr/bin/env python3
"""Rozhraní k provozní databázi fáze 2.

Spuštění:

    python3 tools/pipeline/pipeline.py import      # repozitář -> databáze
    python3 tools/pipeline/pipeline.py export      # databáze -> repozitář
    python3 tools/pipeline/pipeline.py roundtrip   # ověření bezeztrátovosti
    python3 tools/pipeline/pipeline.py stats       # obsah databáze

`roundtrip` do repozitáře nic nezapisuje. Exportuje do dočasného adresáře
a porovná ho bajt po bajtu se skutečnými soubory.
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import export_repo  # noqa: E402
import import_repo  # noqa: E402

REPO_ROOT = db.REPO_ROOT
EXPORTED_FILES = ("data/manifest.json",)


def _exported_paths(root: Path) -> list[str]:
    paths = list(EXPORTED_FILES)
    paths += sorted(
        path.relative_to(root).as_posix()
        for path in (root / "data" / "weeks").glob("*.json")
    )
    return paths


def cmd_import(args) -> int:
    connection = db.connect(args.database)
    stats = import_repo.import_all(connection, args.root)
    for key, value in stats.items():
        print(f"{key}: {value}")
    print(f"\nDatabáze: {args.database or db.DEFAULT_DB_PATH}")
    return 0


def cmd_export(args) -> int:
    connection = db.connect(args.database, create=False)
    stats = export_repo.export_all(connection, args.root)
    for key, value in stats.items():
        print(f"{key}: {value}")
    return 0


def cmd_roundtrip(args) -> int:
    """Import z repozitáře, export do dočasného adresáře, porovnání."""
    root = Path(args.root) if args.root else REPO_ROOT

    connection = db.connect(":memory:")
    import_repo.import_all(connection, root)

    with tempfile.TemporaryDirectory() as name:
        target = Path(name)
        export_repo.export_all(connection, target)

        expected = _exported_paths(root)
        produced = _exported_paths(target)

        missing = sorted(set(expected) - set(produced))
        extra = sorted(set(produced) - set(expected))
        differing = [
            relative for relative in expected
            if relative in produced
            and not filecmp.cmp(root / relative, target / relative, shallow=False)
        ]

        for relative in missing:
            print(f"CHYBÍ    {relative}")
        for relative in extra:
            print(f"NAVÍC    {relative}")
        for relative in differing:
            print(f"LIŠÍ SE  {relative}")
            if args.diff:
                _print_diff(root / relative, target / relative)

        if missing or extra or differing:
            print(f"\nKolotoč nesedí: {len(missing)} chybí, {len(extra)} navíc, "
                  f"{len(differing)} se liší.")
            return 1

        print(f"Kolotoč sedí. Ověřeno souborů: {len(expected)}.")
        return 0


def _print_diff(original: Path, produced: Path) -> None:
    import difflib

    diff = difflib.unified_diff(
        original.read_text(encoding="utf-8").splitlines(keepends=True),
        produced.read_text(encoding="utf-8").splitlines(keepends=True),
        fromfile=f"repozitář/{original.name}", tofile=f"export/{produced.name}",
        n=2,
    )
    shown = 0
    for line in diff:
        print("    " + line.rstrip("\n"))
        shown += 1
        if shown > 60:
            print("    … zkráceno")
            break


def cmd_stats(args) -> int:
    connection = db.connect(args.database, create=False)
    tables = (
        "week", "event", "event_week", "event_category", "event_source",
        "source", "facebook_page", "candidate", "municipality", "category",
        "inbox", "source_fetch", "source_health", "pipeline_run",
        "pipeline_source_run",
    )
    for table in tables:
        count = connection.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
        print(f"{table:<16} {count}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, default=None,
                        help=f"Cesta k databázi (výchozí {db.DEFAULT_DB_PATH}).")
    parser.add_argument("--root", type=Path, default=None, help="Kořen repozitáře.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("import", help="Načíst repozitář do databáze.")
    sub.add_parser("export", help="Zapsat databázi do repozitáře.")
    roundtrip = sub.add_parser("roundtrip", help="Ověřit bezeztrátovost migrace.")
    roundtrip.add_argument("--diff", action="store_true",
                           help="Vypsat rozdíl u lišících se souborů.")
    sub.add_parser("stats", help="Vypsat obsah databáze.")

    args = parser.parse_args()
    return {
        "import": cmd_import, "export": cmd_export,
        "roundtrip": cmd_roundtrip, "stats": cmd_stats,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
