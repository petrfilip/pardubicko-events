"""Test importu referenčních obcí a řízeného slovníku kategorií."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import import_repo  # noqa: E402

failures: list[str] = []


def check(label, actual, expected):
    if actual != expected:
        failures.append(
            f"{label}\n    očekáváno: {expected!r}\n    dostal:    {actual!r}")


connection = db.connect(":memory:")
stats = import_repo.import_all(connection)
manifest = json.loads((db.REPO_ROOT / "data/manifest.json").read_text(encoding="utf-8"))

check("počet obcí", stats["municipalities"], 899)
check("počet kategorií", stats["categories"], 18)
check("počet aliasů kategorií", stats["category_aliases"], 124)
check(
    "import převezme aktuální generated_at manifestu",
    connection.execute(
        "SELECT value FROM repo_meta WHERE key = 'manifest_generated_at'"
    ).fetchone()[0],
    manifest["generated_at"],
)
check(
    "manifest není v DB starší než žádný týden",
    connection.execute(
        "SELECT count(*) FROM week WHERE datetime(generated_at) > "
        "datetime((SELECT value FROM repo_meta "
        "WHERE key = 'manifest_generated_at'))"
    ).fetchone()[0],
    0,
)

check(
    "Chrudim je propojená kódem ČSÚ",
    connection.execute(
        "SELECT DISTINCT municipality_id FROM event "
        "WHERE municipality_name = 'Chrudim'").fetchone()[0],
    571164,
)
check(
    "název sídla mimo číselník se neodhaduje",
    connection.execute(
        "SELECT municipality_id FROM event "
        "WHERE municipality_name = 'Hrádek u Nechanic'").fetchone()[0],
    None,
)
check(
    "všechny publikované kategorie jsou namapované",
    connection.execute(
        "SELECT count(*) FROM event_category WHERE category_id IS NULL").fetchone()[0],
    0,
)
check(
    "každá kategorie má osu a pořadí pro PHP i statický frontend",
    connection.execute(
        "SELECT count(*) FROM category "
        "WHERE axis NOT IN ('kind', 'audience') OR sort_order IS NULL").fetchone()[0],
    0,
)
check(
    "event_category používá kanonické ID v obou sloupcích",
    connection.execute(
        "SELECT count(*) FROM event_category WHERE name <> category_id").fetchone()[0],
    0,
)
check(
    "alias klasická-hudba se sloučí do jediné kategorie hudba",
    connection.execute(
        "SELECT count(*) FROM event_category WHERE category_id = 'hudba'").fetchone()[0]
    > 0,
    True,
)
check(
    "fulltext obsahuje popisek i alias kategorie",
    connection.execute(
        "SELECT count(*) FROM event_fts WHERE event_fts MATCH 'koncert'").fetchone()[0]
    > 0,
    True,
)

# Import je idempotentní i po naplnění nových referenčních tabulek.
stats_again = import_repo.import_all(connection)
check("opakovaný import zachová počet obcí", stats_again["municipalities"], 899)
check("opakovaný import zachová počet kategorií", stats_again["categories"], 18)

# Nedestruktivní migrace existující databáze. V produkčním souboru
# jsou health/inbox data, proto se databáze kvůli novým sloupcům nezakládá znovu.
legacy = sqlite3.connect(":memory:")
legacy.row_factory = sqlite3.Row
legacy.executescript(
    "CREATE TABLE schema_migration (version INTEGER PRIMARY KEY, applied_at TEXT);"
    "INSERT INTO schema_migration VALUES (2, '2026-08-02T00:00:00+00:00');"
    "CREATE TABLE category (id TEXT PRIMARY KEY, label TEXT NOT NULL);"
)
db.apply_schema(legacy)
legacy_columns = {
    row["name"] for row in legacy.execute("PRAGMA table_info(category)")
}
check(
    "migrace doplní metadata kategorie",
    {"axis", "sort_order", "description"}.issubset(legacy_columns),
    True,
)
check(
    f"migrace zapíše verzi {db.SCHEMA_VERSION}",
    legacy.execute(
        "SELECT count(*) FROM schema_migration WHERE version = ?",
        (db.SCHEMA_VERSION,),
    ).fetchone()[0],
    1,
)

if failures:
    print("\n\n".join(failures), file=sys.stderr)
    raise SystemExit(1)

print("Import referenčních obcí a kategorií: OK")
