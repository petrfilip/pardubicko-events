"""Export provozní databáze zpět do publikovaných dat.

Export je protějšek importu. Musí platit, že import následovaný exportem
vrátí soubory v `data/` v nezměněné podobě — to je kritérium přijetí
balíčku P1-1 a jediná pojistka proti tomu, aby se databáze a publikovaná
data tiše rozešly.

Export je jednosměrný a generovaný. Ručně se do vyexportovaných souborů
nezasahuje; oprava patří do databáze, nebo do importu.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import jsonfmt  # noqa: E402

REPO_ROOT = db.REPO_ROOT


def build_manifest(connection) -> dict:
    weeks = connection.execute(
        "SELECT id, date_from, date_to, file FROM week ORDER BY position"
    ).fetchall()

    return {
        "schema_version": int(db.get_meta(connection, "manifest_schema_version") or 1),
        "generated_at": db.get_meta(connection, "manifest_generated_at"),
        "weeks": [
            {
                "id": week["id"],
                "from": week["date_from"],
                "to": week["date_to"],
                "file": week["file"],
            }
            for week in weeks
        ],
    }


def build_event(connection, row, week_id: str) -> dict:
    categories = [
        item["category_id"] for item in connection.execute(
            "SELECT category_id FROM event_category "
            "WHERE event_id = ? ORDER BY position",
            (row["id"],),
        )
    ]

    price: dict = {"type": row["price_type"]}
    if row["price_amount"] is not None:
        price["amount"] = row["price_amount"]
    if row["price_currency"] is not None:
        price["currency"] = row["price_currency"]
    if row["price_text"] is not None:
        price["text"] = row["price_text"]

    event = {
        "id": row["id"],
        "week": week_id,
        "title": row["title"],
        "description": row["description"],
        "start_at": row["start_at"],
        "end_at": row["end_at"],
        "all_day": bool(row["all_day"]),
        "venue": row["venue"],
        "municipality": row["municipality_name"],
        "categories": categories,
        "price": price,
        "source": {"type": row["source_type"], "url": row["source_url"]},
    }

    # Klíč se vypisuje, jen když je hodnota známá. Prázdná hodnota se
    # v publikovaných datech zapisuje vynecháním, ne jako null.
    if row["last_verified_at"] is not None:
        event["last_verified_at"] = row["last_verified_at"]

    event["cancelled"] = bool(row["cancelled"])
    return event


def build_week(connection, week_row) -> dict:
    events = connection.execute(
        "SELECT e.* FROM event e "
        "JOIN event_week w ON w.event_id = e.id "
        "WHERE w.week_id = ? AND e.status = 'published' "
        "ORDER BY w.position",
        (week_row["id"],),
    ).fetchall()

    return {
        "schema_version": 1,
        "week": week_row["id"],
        "generated_at": week_row["generated_at"],
        "events": [build_event(connection, row, week_row["id"]) for row in events],
    }


def export_all(connection, root: Path | None = None) -> dict[str, int]:
    root = Path(root) if root else REPO_ROOT

    jsonfmt.dump_file(root / "data" / "manifest.json", build_manifest(connection))

    weeks = connection.execute("SELECT * FROM week ORDER BY position").fetchall()
    events_written = 0

    for week_row in weeks:
        payload = build_week(connection, week_row)
        events_written += len(payload["events"])
        jsonfmt.dump_file(
            root / week_row["file"], payload, inline_keys=jsonfmt.WEEK_INLINE_KEYS)

    return {"weeks": len(weeks), "event_rows": events_written}
