"""Perzistentní inbox ručně vložených odkazů (ADR 0005).

Modul odděluje vložení URL od jejího zpracování. ``submit`` nesahá na síť;
``process_pending`` používá společnou fetch/snapshot vrstvu a nad uloženým
snapshotem spustí konzervativní klasifikátor.
"""

from __future__ import annotations

import hashlib
import json
import gzip
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
PIPELINE_DIR = HERE.parent / "pipeline"
for directory in (HERE, PIPELINE_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from classify import Classification, classify  # noqa: E402
from urlnorm import normalize_url  # noqa: E402

MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Submission:
    id: int
    state: str
    duplicate: bool
    url_norm: str


@dataclass(frozen=True)
class ProcessingResult:
    id: int
    state: str
    resolved_kind: str | None
    candidate_id: str | None
    attempts: int
    error: str | None


def _timestamp(value=None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat(timespec="seconds")


def submit(connection, url: str, *, note: str | None = None,
           submitted_via: str = "cli", submitted_at=None) -> Submission:
    """Vloží odkaz idempotentně; duplicita pouze doplní novou poznámku."""
    url_norm = normalize_url(url)
    existing = connection.execute(
        "SELECT id, state, note FROM inbox WHERE url_norm = ?", (url_norm,)
    ).fetchone()
    if existing is not None:
        merged_note = _merge_note(existing["note"], note)
        if merged_note != existing["note"]:
            connection.execute(
                "UPDATE inbox SET note = ? WHERE id = ?", (merged_note, existing["id"])
            )
            connection.commit()
        return Submission(int(existing["id"]), existing["state"], True, url_norm)

    cursor = connection.execute(
        "INSERT INTO inbox (url, url_norm, submitted_at, submitted_via, note) "
        "VALUES (?, ?, ?, ?, ?)",
        (url, url_norm, _timestamp(submitted_at), submitted_via, note),
    )
    connection.commit()
    return Submission(int(cursor.lastrowid), "new", False, url_norm)


def _merge_note(current: str | None, incoming: str | None) -> str | None:
    incoming = incoming.strip() if incoming else None
    if not incoming or incoming == current:
        return current
    if not current:
        return incoming
    if incoming in current.splitlines():
        return current
    return current.rstrip() + "\n" + incoming


def process_pending(connection, *, limit: int | None = None, now=None,
                    fetch_url_fn: Callable | None = None,
                    snapshot_dir: Path | None = None) -> list[ProcessingResult]:
    """Zpracuje nové položky v pořadí vložení.

    ``fetch_url_fn`` je injekční bod pro testy. Produkce používá
    ``tools/pipeline/fetch.py``; jeho výsledek musí nést ``snapshot_path`` a
    ``error``.
    """
    if fetch_url_fn is None:
        from fetch import fetch_url as fetch_url_fn  # noqa: PLC0415

    sql = "SELECT * FROM inbox WHERE state = 'new' AND attempts < ? ORDER BY id"
    params: list[object] = [MAX_ATTEMPTS]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(max(0, int(limit)))

    rows = connection.execute(sql, params).fetchall()
    return [
        process_one(connection, row["id"], now=now, fetch_url_fn=fetch_url_fn,
                    snapshot_dir=snapshot_dir)
        for row in rows
    ]


def process_one(connection, inbox_id: int, *, now=None,
                fetch_url_fn: Callable | None = None,
                snapshot_dir: Path | None = None) -> ProcessingResult:
    if fetch_url_fn is None:
        from fetch import fetch_url as fetch_url_fn  # noqa: PLC0415

    row = connection.execute("SELECT * FROM inbox WHERE id = ?", (inbox_id,)).fetchone()
    if row is None:
        raise ValueError(f"Inbox záznam {inbox_id} neexistuje.")
    if row["state"] != "new":
        return _result(row)

    attempted_at = _timestamp(now)
    attempts = int(row["attempts"]) + 1
    connection.execute(
        "UPDATE inbox SET attempts = ?, last_attempt_at = ?, error = NULL WHERE id = ?",
        (attempts, attempted_at, inbox_id),
    )
    connection.commit()

    try:
        fetched = fetch_url_fn(
            connection, row["url_norm"], source_id=None, snapshot_dir=snapshot_dir,
            now=now)
    except Exception as exc:  # fetch chyba musí zůstat viditelná ve frontě
        return _fetch_failed(connection, inbox_id, attempts, f"stažení selhalo: {exc}")

    fetch_error = getattr(fetched, "error", None)
    snapshot_path = getattr(fetched, "snapshot_path", None)
    if fetch_error or not snapshot_path:
        reason = str(fetch_error or "stažení nevrátilo uložený snapshot")
        return _fetch_failed(connection, inbox_id, attempts, reason)

    try:
        snapshot = getattr(fetched, "snapshot", None)
        body = getattr(snapshot, "body", None)
        if isinstance(body, bytes):
            html = body.decode("utf-8", "replace")
        elif Path(snapshot_path).suffix == ".gz":
            html = gzip.decompress(Path(snapshot_path).read_bytes()).decode(
                "utf-8", "replace")
        else:
            html = Path(snapshot_path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _fetch_failed(connection, inbox_id, attempts,
                             f"snapshot nelze přečíst: {exc}")

    connection.execute("UPDATE inbox SET state = 'fetched' WHERE id = ?", (inbox_id,))
    classification = classify(html, row["url_norm"])
    if classification.kind == "event-detail":
        candidate_id = _upsert_candidate(
            connection, row, classification, created_at=attempted_at)
        state, error = "candidate", None
    elif classification.kind == "listing":
        candidate_id = None
        state, error = "source-proposal", None
    else:
        candidate_id = None
        state = "failed"
        error = classification.reason_text

    connection.execute(
        "UPDATE inbox SET state = ?, resolved_kind = ?, candidate_id = ?, error = ? "
        "WHERE id = ?",
        (state, classification.kind, candidate_id, error, inbox_id),
    )
    connection.commit()
    return ProcessingResult(inbox_id, state, classification.kind, candidate_id,
                            attempts, error)


def _fetch_failed(connection, inbox_id: int, attempts: int, reason: str) -> ProcessingResult:
    # Přechodná chyba se smí zopakovat. Třetí neúspěch frontu uzavře.
    state = "failed" if attempts >= MAX_ATTEMPTS else "new"
    connection.execute(
        "UPDATE inbox SET state = ?, error = ? WHERE id = ?",
        (state, reason, inbox_id),
    )
    connection.commit()
    return ProcessingResult(inbox_id, state, None, None, attempts, reason)


def _upsert_candidate(connection, inbox_row, result: Classification, *,
                      created_at: str) -> str:
    candidate_id = "manual-" + hashlib.sha256(
        inbox_row["url_norm"].encode("utf-8")
    ).hexdigest()[:20]
    signals = result.signals
    payload = {
        "id": candidate_id,
        "title": signals.title,
        "date_text": signals.date_text,
        "municipality": None,
        "district": None,
        "region": None,
        "venue": signals.venue,
        "categories": None,
        "discovered_at": created_at,
        "discovery_method": "manual-submission",
        "source_url": inbox_row["url_norm"],
        "source_type": "manual-url",
        "candidate_kind": "single-event",
        "status": "new",
        "notes": "Klasifikace inboxu: " + result.reason_text,
        "inbox_signals": signals.as_dict(),
    }
    connection.execute(
        "INSERT INTO candidate ("
        "  id, inbox_id, discovery_method, payload, state, created_at, notes"
        ") VALUES (?, ?, 'manual-submission', ?, 'new', ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET inbox_id = excluded.inbox_id",
        (candidate_id, inbox_row["id"],
         json.dumps(payload, ensure_ascii=False, sort_keys=True), created_at,
         payload["notes"]),
    )
    return candidate_id


def _result(row) -> ProcessingResult:
    return ProcessingResult(
        int(row["id"]), row["state"], row["resolved_kind"],
        row["candidate_id"], int(row["attempts"]), row["error"])
