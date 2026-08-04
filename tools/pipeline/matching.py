#!/usr/bin/env python3
"""Deterministická deduplikace s blokem a třemi rozhodovacími pásmy."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

AUTO_THRESHOLD = 0.92
REVIEW_THRESHOLD = 0.75


def normalize_text(value: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", (value or "").casefold())
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


def jaro_winkler(left: str | None, right: str | None) -> float:
    a, b = normalize_text(left), normalize_text(right)
    if a == b:
        return 1.0 if a else 0.0
    if not a or not b:
        return 0.0
    distance = max(len(a), len(b)) // 2 - 1
    a_match = [False] * len(a)
    b_match = [False] * len(b)
    matches = 0
    for i, char in enumerate(a):
        for j in range(max(0, i - distance), min(i + distance + 1, len(b))):
            if b_match[j] or char != b[j]:
                continue
            a_match[i] = True
            b_match[j] = True
            matches += 1
            break
    if matches == 0:
        return 0.0
    a_chars = [a[i] for i in range(len(a)) if a_match[i]]
    b_chars = [b[j] for j in range(len(b)) if b_match[j]]
    transpositions = sum(x != y for x, y in zip(a_chars, b_chars)) / 2
    jaro = (matches / len(a) + matches / len(b)
            + (matches - transpositions) / matches) / 3
    prefix = 0
    for x, y in zip(a, b):
        if x != y or prefix == 4:
            break
        prefix += 1
    return min(1.0, jaro + prefix * 0.1 * (1.0 - jaro))


def _minutes(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.hour * 60 + parsed.minute


def time_similarity(left: str | None, right: str | None) -> float:
    a, b = _minutes(left), _minutes(right)
    if a is None or b is None:
        return 0.0
    difference = abs(a - b)
    return max(0.0, 1.0 - difference / 180.0)


@dataclass(frozen=True)
class MatchScore:
    score: float
    decision: str
    title: float
    venue: float | None
    time: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score, "decision": self.decision,
            "title": self.title, "venue": self.venue, "time": self.time,
        }


def score(left: dict, right: dict) -> MatchScore:
    title = jaro_winkler(left.get("title"), right.get("title"))
    venue_available = bool(left.get("venue") and right.get("venue"))
    venue = jaro_winkler(left.get("venue"), right.get("venue")) if venue_available else None
    time = time_similarity(left.get("start_at"), right.get("start_at"))
    components = [(title, 0.70), (time, 0.15)]
    if venue is not None:
        components.append((venue, 0.15))
    weighted = sum(value * weight for value, weight in components)
    total_weight = sum(weight for _, weight in components)
    value = round(weighted / total_weight, 4)
    decision = "auto-merge" if value >= AUTO_THRESHOLD else (
        "review" if value >= REVIEW_THRESHOLD else "separate")
    return MatchScore(value, decision, round(title, 4),
                      round(venue, 4) if venue is not None else None,
                      round(time, 4))


def same_block(left: dict, right: dict) -> bool:
    try:
        left_date = datetime.fromisoformat(left["start_at"]).date()
        right_date = datetime.fromisoformat(right["start_at"]).date()
    except (KeyError, TypeError, ValueError):
        return False
    return left_date == right_date and left.get("municipality_id") is not None \
        and left.get("municipality_id") == right.get("municipality_id")


def find_matches(connection: sqlite3.Connection, candidate: dict) -> list[tuple[dict, MatchScore]]:
    """Vrátí pouze události ze stejného tvrdého bloku, seřazené skórem."""
    if candidate.get("municipality_id") is None or not candidate.get("start_at"):
        return []
    day = datetime.fromisoformat(candidate["start_at"]).date().isoformat()
    rows = connection.execute(
        "SELECT id, title, start_at, venue, municipality_id FROM event "
        "WHERE date(start_at) = ? AND municipality_id = ? AND status = 'published'",
        (day, candidate["municipality_id"]),
    ).fetchall()
    ranked = [(dict(row), score(candidate, dict(row))) for row in rows]
    return sorted(ranked, key=lambda item: (-item[1].score, item[0]["id"]))


def apply_best_match(connection: sqlite3.Connection, candidate_id: str,
                     candidate: dict, *, source_id: str | None, source_url: str,
                     now: str | None = None) -> MatchScore | None:
    """Sloučí jistou shodu nebo zapíše nejistou do fronty.

    Samostatná položka se zde nepublikuje; publish je až následná transakční
    fáze. Tím matching nikdy nevytvoří neověřenou veřejnou akci.
    """
    matches = find_matches(connection, candidate)
    if not matches:
        return None
    event, result = matches[0]
    timestamp = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    if result.decision == "auto-merge":
        connection.execute(
            "INSERT INTO event_source (event_id, source_id, url, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(event_id, url) DO UPDATE SET "
            "last_seen_at = excluded.last_seen_at, "
            "source_id = COALESCE(event_source.source_id, excluded.source_id)",
            (event["id"], source_id, source_url, timestamp, timestamp),
        )
        connection.execute(
            "UPDATE candidate SET state = 'imported', event_id = ?, reviewed_at = ? WHERE id = ?",
            (event["id"], timestamp, candidate_id),
        )
    elif result.decision == "review":
        connection.execute(
            "INSERT INTO match_review "
            "(candidate_id, event_id, score, breakdown, state, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?) ON CONFLICT(candidate_id, event_id) "
            "DO UPDATE SET score = excluded.score, breakdown = excluded.breakdown",
            (candidate_id, event["id"], result.score,
             json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True), timestamp),
        )
    return result
