#!/usr/bin/env python3
"""Test doložených aliasů obcí a geografického coverage reportu."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import coverage  # noqa: E402
import db  # noqa: E402
import import_repo  # noqa: E402


connection = db.connect(Path(":memory:"))
stats = import_repo.import_all(connection)
assert stats["municipality_aliases"] == 1
row = connection.execute(
    "SELECT municipality_id FROM municipality_alias WHERE alias = 'Janderov'"
).fetchone()
assert row and row["municipality_id"] == 571164

report = coverage.build_report(connection, generated_at="2026-08-03T00:00:00+00:00")
assert report["municipalities_in_catalog"] == 899
assert report["aliases_in_catalog"] == 1
assert report["events"]["total"] == 77
assert report["events"]["linked_to_municipality"] == 76
assert report["parts_of_municipalities"][0]["alias"] == "Janderov"
assert report["unresolved_values"] == [
    {"municipality_name": "Hrádek u Nechanic", "events": 1},
]

print("Geografické aliasy a coverage report prošly.")
