"""Test importu referenčních obcí a řízeného slovníku kategorií."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import import_repo  # noqa: E402
import pipeline as pipeline_cli  # noqa: E402

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

# Provozní kandidát nemá `source_file`: vznikl během běhu adaptéru a import
# repozitáře jej ani jeho čekající deduplikační rozhodnutí nesmí zahodit.
test_event_id = connection.execute(
    "SELECT id FROM event ORDER BY id LIMIT 1").fetchone()[0]
connection.execute(
    "INSERT INTO candidate (id, source_id, discovery_method, payload, state, created_at) "
    "VALUES ('adapter-import-survivor', 'pardubice-calendar', 'adapter', ?, "
    "'new', '2026-08-04T08:00:00+02:00')",
    (json.dumps({"normalized": {"title": "Testovací kandidát"}}),),
)
connection.execute(
    "INSERT INTO match_review "
    "(candidate_id, event_id, score, breakdown, created_at) "
    "VALUES ('adapter-import-survivor', ?, 0.75, '{}', "
    "'2026-08-04T08:00:00+02:00')",
    (test_event_id,),
)
connection.commit()

# Import je idempotentní i po naplnění nových referenčních tabulek.
stats_again = import_repo.import_all(connection)
check("opakovaný import zachová počet obcí", stats_again["municipalities"], 899)
check("opakovaný import zachová počet kategorií", stats_again["categories"], 18)
check(
    "opakovaný import zachová provozního kandidáta",
    connection.execute(
        "SELECT state FROM candidate WHERE id = 'adapter-import-survivor'"
    ).fetchone()[0],
    "new",
)
check(
    "opakovaný import zachová jeho rozhodovací frontu",
    connection.execute(
        "SELECT state FROM match_review "
        "WHERE candidate_id = 'adapter-import-survivor'"
    ).fetchone()[0],
    "pending",
)
check(
    "kurátorský výpis obsahuje provozní backlog",
    [item["id"] for item in pipeline_cli.operational_candidates(
        connection, states=["new"], limit=1000)],
    ["adapter-import-survivor"],
)
connection.execute(
    "INSERT INTO candidate (id, source_id, discovery_method, payload, state, created_at) "
    "VALUES ('adapter-week-33', 'pardubice-calendar', 'adapter', ?, "
    "'new', '2026-08-04T08:01:00+02:00')",
    (json.dumps({"normalized": {
        "title": "Týdenní kandidát",
        "start_at": "2026-08-12T18:00:00+02:00",
        "end_at": "2026-08-12T20:00:00+02:00",
    }}),),
)
connection.commit()
check(
    "limit výpisu nemění celkový počet backlogu",
    (
        len(pipeline_cli.operational_candidates(
            connection, states=["new"], limit=1)),
        pipeline_cli.count_operational_candidates(
            connection, states=["new"]),
    ),
    (1, 2),
)
check(
    "kurátorský výpis lze omezit na ISO týden",
    [item["id"] for item in pipeline_cli.operational_candidates(
        connection, states=["new"], week_id="2026-W33", limit=100)],
    ["adapter-week-33"],
)
check(
    "research kandidát s českým termínem se přiřadí k týdnu",
    pipeline_cli._research_week_match(
        {"date_text": "Sobota 15. 8. 2026 v 18:00–19:30"}, "2026-W33"),
    True,
)
check(
    "research kandidát bez termínu zůstane přiznaně nezařazený",
    pipeline_cli._research_week_match({"date_text": None}, "2026-W33"),
    None,
)

publication = {
    "candidate_id": "adapter-week-33",
    "events": [{
        "id": "bezpecny-publish-regresni-test-2026-08-12",
        "week": "2026-W33",
        "title": "Bezpečný publish – regresní test",
        "description": "Pouze in-memory návrh pro regresní test.",
        "start_at": "2026-08-12T18:00:00+02:00",
        "end_at": "2026-08-12T19:00:00+02:00",
        "all_day": False,
        "venue": "Testovací místo",
        "municipality": "Pardubice",
        "categories": ["zabava"],
        "price": {"type": "unknown", "text": "Neuvedeno"},
        "source": {"type": "official", "url": "https://example.test/event"},
        "cancelled": False,
    }],
}
_manifest, prepared_weeks, changed_weeks = pipeline_cli._prepare_publication(
    db.REPO_ROOT, publication)
check("publish preview určí změněný týden", changed_weeks, ["2026-W33"])
check(
    "publish preview připraví akci bez zápisu do repozitáře",
    prepared_weeks["2026-W33"]["events"][-1]["id"],
    "bezpecny-publish-regresni-test-2026-08-12",
)
check(
    "publish preview skutečný týden nezmění",
    any(event["id"] == "bezpecny-publish-regresni-test-2026-08-12"
        for event in json.loads(
            (db.REPO_ROOT / "data/weeks/2026-W33.json").read_text(encoding="utf-8")
        )["events"]),
    False,
)

pipeline_cli.resolve_operational_candidate(
    connection, "adapter-import-survivor", event_id=test_event_id,
    note="Ověřeno regresním testem.")
check(
    "Curator propojí kandidáta s produkční akcí",
    tuple(connection.execute(
        "SELECT state, event_id FROM candidate "
        "WHERE id = 'adapter-import-survivor'"
    ).fetchone()),
    ("imported", test_event_id),
)
check(
    "kurátorské rozhodnutí uzavře match review",
    connection.execute(
        "SELECT state FROM match_review "
        "WHERE candidate_id = 'adapter-import-survivor'"
    ).fetchone()[0],
    "merged",
)
import_repo.import_all(connection)
check(
    "další import zachová uzavřený provozní stav",
    tuple(connection.execute(
        "SELECT state, event_id FROM candidate "
        "WHERE id = 'adapter-import-survivor'"
    ).fetchone()),
    ("imported", test_event_id),
)

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
