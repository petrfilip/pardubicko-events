#!/usr/bin/env python3
"""Vygeneruje auditovatelný report geografického pokrytí z SQLite."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import db


def build_report(connection, *, generated_at: str | None = None) -> dict:
    municipalities = connection.execute(
        "SELECT COUNT(*) AS n FROM municipality").fetchone()["n"]
    aliases = connection.execute(
        "SELECT COUNT(*) AS n FROM municipality_alias").fetchone()["n"]
    linked = connection.execute(
        "SELECT COUNT(*) AS n FROM event WHERE municipality_id IS NOT NULL"
    ).fetchone()["n"]
    unresolved_rows = connection.execute(
        "SELECT municipality_name, COUNT(*) AS events FROM event "
        "WHERE municipality_id IS NULL GROUP BY municipality_name "
        "ORDER BY municipality_name"
    ).fetchall()
    parts = connection.execute(
        "SELECT a.alias, a.municipality_id, m.name AS municipality_name, "
        "COUNT(e.id) AS events FROM municipality_alias a "
        "JOIN municipality m ON m.id = a.municipality_id "
        "LEFT JOIN event e ON e.municipality_name = a.alias "
        "GROUP BY a.alias, a.municipality_id, m.name ORDER BY a.alias"
    ).fetchall()
    total = connection.execute("SELECT COUNT(*) AS n FROM event").fetchone()["n"]
    return {
        "schema_version": 1,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "municipalities_in_catalog": municipalities,
        "aliases_in_catalog": aliases,
        "events": {
            "total": total,
            "linked_to_municipality": linked,
            "unresolved": sum(row["events"] for row in unresolved_rows),
        },
        "parts_of_municipalities": [dict(row) for row in parts],
        "unresolved_values": [dict(row) for row in unresolved_rows],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=db.REPO_ROOT / "stats" / "coverage.json")
    parser.add_argument("--generated-at", help="pevný ISO čas pro reprodukovatelný běh")
    args = parser.parse_args()
    connection = db.connect(args.database, create=False)
    report = build_report(connection, generated_at=args.generated_at)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
