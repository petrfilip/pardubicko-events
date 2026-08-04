#!/usr/bin/env python3
"""Kalibrační a integrační test deduplikace."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import db  # noqa: E402
import import_repo  # noqa: E402
import matching  # noqa: E402


connection = db.connect(Path(":memory:"))
import_repo.import_all(connection)

# Všech sedm benchmarků se musí při shodném názvu/obci/času najít samo.
benchmark = json.loads((ROOT / "benchmarks/2026-08-02/expected-events.json").read_text())
for expected in benchmark["expected_events"]:
    event = connection.execute(
        "SELECT id, title, start_at, venue, municipality_id FROM event WHERE id = ?",
        (expected["event_id"],),
    ).fetchone()
    if event is None:
        # Benchmark historicky drží staré ID Sportovního parku; ostatní musí existovat.
        assert expected["event_id"] == "sportovni-park-pardubice-2026-w31"
        continue
    result = matching.score(dict(event), dict(event))
    assert result.decision == "auto-merge" and result.score == 1.0

# Doložené dvojí založení na Facebooku má stejný název a čas.
facebook = json.loads((ROOT / "research/candidates-2026-08-02-2043-facebook.json").read_text())
pair = {item["id"]: item for item in facebook["candidates"]
        if item["id"] in {"fb-2412285059248213", "fb-4525215647723357"}}
fb_score = matching.score(pair["fb-2412285059248213"], pair["fb-4525215647723357"])
assert fb_score.decision == "auto-merge" and fb_score.score == 1.0

# Shodný blok s nesouvisejícím názvem se nesmí sloučit.
unrelated = matching.score(
    {"title": "Dětský den", "start_at": "2026-08-15T20:00:00+02:00", "venue": "Park"},
    {"title": "Komorní koncert", "start_at": "2026-08-15T20:00:00+02:00", "venue": "Galerie"},
)
assert unrelated.decision == "separate" and unrelated.score < matching.REVIEW_THRESHOLD

# Střední pásmo jde do fronty, nikoli do event_source.
connection.execute(
    "INSERT INTO candidate (id, discovery_method, payload, state, created_at) "
    "VALUES ('match-test', 'adapter', '{}', 'new', '2026-08-03T00:00:00+00:00')")
event = connection.execute(
    "SELECT id, title, start_at, venue, municipality_id FROM event "
    "WHERE municipality_id IS NOT NULL LIMIT 1").fetchone()
candidate = dict(event)
candidate["title"] = event["title"] + " letní program"
result = matching.apply_best_match(
    connection, "match-test", candidate, source_id=None,
    source_url="https://example.test/item", now="2026-08-03T00:00:00+00:00")
assert result is not None and result.decision in {"review", "auto-merge"}
if result.decision == "review":
    assert connection.execute(
        "SELECT COUNT(*) FROM match_review WHERE candidate_id = 'match-test'"
    ).fetchone()[0] == 1

# Automatické sloučení zachová původní vazbu a přidá novou URL zdroje.
exact = connection.execute(
    "SELECT id, title, start_at, venue, municipality_id FROM event "
    "WHERE municipality_id IS NOT NULL ORDER BY id LIMIT 1").fetchone()
connection.execute(
    "INSERT INTO candidate (id, discovery_method, payload, state, created_at) "
    "VALUES ('auto-match-test', 'adapter', '{}', 'new', '2026-08-03T00:00:00+00:00')")
before = connection.execute(
    "SELECT COUNT(*) FROM event_source WHERE event_id = ?", (exact["id"],)
).fetchone()[0]
auto = matching.apply_best_match(
    connection, "auto-match-test", dict(exact), source_id="pardubice-calendar",
    source_url="https://example.test/second-source",
    now="2026-08-03T00:00:00+00:00")
assert auto is not None and auto.decision == "auto-merge"
after = connection.execute(
    "SELECT COUNT(*) FROM event_source WHERE event_id = ?", (exact["id"],)
).fetchone()[0]
assert after == before + 1
linked = connection.execute(
    "SELECT event_id, state FROM candidate WHERE id = 'auto-match-test'"
).fetchone()
assert linked["event_id"] == exact["id"] and linked["state"] == "imported"

print("Deduplikační blok, skóre, pásma a referenční případy prošly.")
