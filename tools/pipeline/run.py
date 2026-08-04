#!/usr/bin/env python3
"""Dávkový end-to-end runner registrovaných deterministických zdrojů.

Tok končí kandidátem, karanténou nebo bezpečným připojením dalšího zdroje k
již publikované akci. Novou veřejnou akci tento nástroj nikdy nevytváří.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import fetch  # noqa: E402
import matching  # noqa: E402
import normalization  # noqa: E402

LOCAL_TZ = normalization.TZ
DEFAULT_REPORT_DIR = db.REPO_ROOT / "stats" / "runs"


class SourceRunError(RuntimeError):
    """Jeden zdroj selhal; dávka smí pokračovat dalšími zdroji."""


@dataclass
class SourceOutcome:
    source_id: str
    status: str
    items_found: int = 0
    items_valid: int = 0
    items_unparsed: int = 0
    candidates_created: int = 0
    candidates_existing: int = 0
    candidates_updated: int = 0
    candidates_quarantined: int = 0
    matched_existing: int = 0
    match_review_queued: int = 0
    error: str | None = None
    municipality: str | None = None
    district: str | None = None
    region: str | None = None

    def metrics(self) -> dict[str, int]:
        return {
            "items_found": self.items_found,
            "items_valid": self.items_valid,
            "items_unparsed": self.items_unparsed,
            "candidates_created": self.candidates_created,
            "candidates_existing": self.candidates_existing,
            "candidates_updated": self.candidates_updated,
            "candidates_quarantined": self.candidates_quarantined,
            "matched_existing": self.matched_existing,
            "match_review_queued": self.match_review_queued,
        }


@dataclass
class BatchResult:
    report: dict[str, Any]
    outcomes: list[SourceOutcome] = field(default_factory=list)
    report_path: Path | None = None


def run_batch(connection: sqlite3.Connection, *, source_ids: list[str] | None = None,
              due: bool = False, offline: bool = False, dry_run: bool = False,
              snapshot_dir: Path | None = None, report_root: Path | None = None,
              write_report: bool = True, now: datetime | None = None,
              fetch_kwargs: dict[str, Any] | None = None) -> BatchResult:
    """Spustí izolovanou dávku a vrátí stejný report, který ukládá na disk."""
    started = _moment(now)
    selected = select_sources(connection, source_ids=source_ids, due=due, now=started)
    dry_snapshot_temp = None
    if dry_run and not offline:
        # API i CLI mají stejnou garanci: dry-run nezanechá snapshoty ani při
        # explicitním --snapshot-dir, který by jinak byl trvalým zápisem.
        dry_snapshot_temp = tempfile.TemporaryDirectory(
            prefix="pardubicko-pipeline-dry-run-")
        snapshot_dir = Path(dry_snapshot_temp.name)
    run_id = _unique_run_id(connection, started, report_root or DEFAULT_REPORT_DIR)
    outcomes: list[SourceOutcome] = []
    notes = [
        "Runner nevytváří nové event ani export; automaticky smí pouze připojit "
        "zdroj k již publikované jisté shodě.",
    ]
    if offline:
        notes.append("Offline běh použil pouze poslední uložené snapshoty; síť nebyla otevřena.")
    if dry_run:
        notes.append("Dry-run vrátil všechny databázové změny a nevytvořil run report na disku.")

    if not dry_run:
        connection.execute(
            "INSERT INTO pipeline_run "
            "(id, started_at, status, offline, dry_run) VALUES (?, ?, 'running', ?, 0)",
            (run_id, _iso(started), 1 if offline else 0),
        )
        connection.commit()

    for source in selected:
        try:
            adapter = fetch.resolve_adapter(source)
        except ValueError as exc:
            outcome = SourceOutcome(
                source_id=source["id"], status="skipped", error=str(exc),
                municipality=source.get("municipality_name"),
                district=source.get("district"), region=source.get("region"),
            )
            outcomes.append(outcome)
            if not dry_run:
                _record_source_outcome(connection, run_id, outcome, started, started)
            continue

        outcome = SourceOutcome(
            source_id=source["id"], status="running",
            municipality=source.get("municipality_name"),
            district=source.get("district"), region=source.get("region"),
        )
        if not dry_run:
            _record_source_running(connection, run_id, source["id"], started)
        try:
            outcome = _run_source(
                connection, source, adapter, started=started, offline=offline,
                dry_run=dry_run, snapshot_dir=snapshot_dir,
                fetch_kwargs=fetch_kwargs or {}, outcome=outcome,
            )
        except Exception as exc:  # noqa: BLE001 — hranice izolace zdroje
            if connection.in_transaction:
                connection.rollback()
            outcome.status = "failed"
            outcome.error = f"{type(exc).__name__}: {exc}"
        outcomes.append(outcome)
        if not dry_run:
            _record_source_outcome(connection, run_id, outcome, started, _moment(now))

    finished = _moment(now)
    report = _build_report(
        run_id, started, finished, outcomes, selected_count=len(selected),
        offline=offline, dry_run=dry_run, notes=notes,
    )
    result = BatchResult(report=report, outcomes=outcomes)

    if not dry_run:
        connection.execute(
            "UPDATE pipeline_run SET finished_at = ?, status = ?, error = ? WHERE id = ?",
            (_iso(finished), report["status"], report["partial_reason"], run_id),
        )
        connection.commit()
        if write_report:
            result.report_path = write_run_report(report, report_root or DEFAULT_REPORT_DIR)
            connection.execute(
                "UPDATE pipeline_run SET report_path = ? WHERE id = ?",
                (str(result.report_path), run_id),
            )
            connection.commit()
    if dry_snapshot_temp is not None:
        dry_snapshot_temp.cleanup()
    return result


def select_sources(connection: sqlite3.Connection, *, source_ids: list[str] | None,
                   due: bool, now: datetime) -> list[dict[str, Any]]:
    """Vybere jen enabled zdroje; `--due` porovná poslední pokus s intervalem."""
    requested = list(dict.fromkeys(source_ids or []))
    rows = connection.execute(
        "SELECT s.*, (SELECT f.fetched_at FROM source_fetch f "
        "WHERE f.source_id = s.id ORDER BY datetime(f.fetched_at) DESC, f.id DESC "
        "LIMIT 1) AS last_fetched_at FROM source s ORDER BY s.id"
    ).fetchall()
    known = {row["id"]: dict(row) for row in rows}
    missing = [source_id for source_id in requested if source_id not in known]
    disabled = [source_id for source_id in requested
                if source_id in known and not known[source_id]["enabled"]]
    if missing:
        raise ValueError("Neznámé source id: " + ", ".join(missing))
    if disabled:
        raise ValueError("Zdroj je disabled: " + ", ".join(disabled))

    selected = [known[source_id] for source_id in requested] if requested else [
        dict(row) for row in rows if row["enabled"]]
    if due:
        selected = [source for source in selected if _is_due(source, now)]
    return selected


def _is_due(source: dict[str, Any], now: datetime) -> bool:
    last = source.get("last_fetched_at")
    if not last:
        return True
    try:
        parsed = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return now >= parsed.astimezone(now.tzinfo) + timedelta(
        days=int(source["check_interval_days"]))


def _run_source(connection, source, adapter, *, started, offline, dry_run,
                snapshot_dir, fetch_kwargs, outcome) -> SourceOutcome:
    # Fetch pozorování se u skutečného běhu commitne i při HTTP chybě. Tím
    # zůstává ADR 0004 splněné; kandidáti přesto vznikají v samostatné atomické
    # transakci a chyba je nemůže ponechat napůl.
    connection.execute("BEGIN")
    fetched = fetch.fetch_source(
        connection, source, adapter=adapter, offline=offline,
        snapshot_dir=snapshot_dir, autocommit=False, **fetch_kwargs)
    failures = [item for item in fetched if not item.ok]
    if failures:
        if dry_run:
            connection.rollback()
        else:
            connection.commit()
        raise SourceRunError("; ".join(item.error or "fetch selhal" for item in failures))
    if not fetched:
        connection.rollback()
        raise SourceRunError("Adaptér nevytvořil žádný fetch požadavek.")
    if dry_run:
        # Dry-run drží fetch i kandidátní změny v jedné vratné transakci.
        pass
    else:
        connection.commit()
        connection.execute("BEGIN")

    extracted = fetch.extract_results(
        connection, fetched, adapter, autocommit=False)
    outcome.items_found = extracted.items_found
    outcome.items_valid = extracted.items_valid
    outcome.items_unparsed = extracted.items_unparsed
    provenance = normalization.snapshot_provenance(fetched)

    for item in extracted.items:
        candidate = normalization.normalize_item(connection, source, item)
        payload = candidate.payload(source["id"], provenance)
        existing = connection.execute(
            "SELECT state, payload FROM candidate WHERE id = ?", (candidate.candidate_id,)
        ).fetchone()
        rendered_payload = _canonical_json(payload)
        if existing:
            outcome.candidates_existing += 1
            target_state = (existing["state"] if existing["state"] in {"imported", "rejected"}
                            else candidate.state)
            if existing["payload"] != rendered_payload or existing["state"] != target_state:
                outcome.candidates_updated += 1
        else:
            outcome.candidates_created += 1
        notes = ", ".join(candidate.quarantine_reasons) or None
        connection.execute(
            "INSERT INTO candidate ("
            "id, source_id, discovery_method, payload, state, created_at, notes"
            ") VALUES (?, ?, 'adapter', ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "source_id = excluded.source_id, payload = excluded.payload, "
            "state = CASE WHEN candidate.state IN ('imported', 'rejected') "
            "THEN candidate.state ELSE excluded.state END, notes = excluded.notes",
            (candidate.candidate_id, source["id"], rendered_payload,
             candidate.state, _iso(started), notes),
        )
        current = connection.execute(
            "SELECT state FROM candidate WHERE id = ?", (candidate.candidate_id,)
        ).fetchone()["state"]
        if current == "quarantined":
            outcome.candidates_quarantined += 1
            continue
        if current != "new":
            continue

        normalized = candidate.normalized
        match = matching.apply_best_match(
            connection, candidate.candidate_id, normalized,
            source_id=source["id"], source_url=normalized["canonical_url"],
            now=_iso(started),
        )
        if match and match.decision == "auto-merge":
            outcome.matched_existing += 1
        elif match and match.decision == "review":
            connection.execute(
                "UPDATE candidate SET state = 'needs-verification' WHERE id = ?",
                (candidate.candidate_id,),
            )
            outcome.match_review_queued += 1
            outcome.candidates_quarantined += 1

    if dry_run:
        connection.rollback()
    else:
        connection.commit()
    changed = (outcome.candidates_created + outcome.candidates_updated
               + outcome.matched_existing + outcome.match_review_queued)
    outcome.status = "success" if changed else "no-change"
    return outcome


def _record_source_running(connection, run_id: str, source_id: str,
                           started: datetime) -> None:
    connection.execute(
        "INSERT INTO pipeline_source_run (run_id, source_id, started_at, status) "
        "VALUES (?, ?, ?, 'running')",
        (run_id, source_id, _iso(started)),
    )
    connection.commit()


def _record_source_outcome(connection, run_id: str, outcome: SourceOutcome,
                           started: datetime, finished: datetime) -> None:
    connection.execute(
        "INSERT INTO pipeline_source_run ("
        "run_id, source_id, started_at, finished_at, status, items_found, items_valid, "
        "candidates_created, candidates_existing, candidates_updated, "
        "candidates_quarantined, error"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(run_id, source_id) DO UPDATE SET "
        "finished_at=excluded.finished_at, status=excluded.status, "
        "items_found=excluded.items_found, items_valid=excluded.items_valid, "
        "candidates_created=excluded.candidates_created, "
        "candidates_existing=excluded.candidates_existing, "
        "candidates_updated=excluded.candidates_updated, "
        "candidates_quarantined=excluded.candidates_quarantined, error=excluded.error",
        (run_id, outcome.source_id, _iso(started), _iso(finished), outcome.status,
         outcome.items_found, outcome.items_valid, outcome.candidates_created,
         outcome.candidates_existing, outcome.candidates_updated,
         outcome.candidates_quarantined, outcome.error),
    )
    connection.commit()


def _build_report(run_id, started, finished, outcomes, *, selected_count,
                  offline, dry_run, notes) -> dict[str, Any]:
    failed = [item for item in outcomes if item.status == "failed"]
    skipped = [item for item in outcomes if item.status == "skipped"]
    completed = [item for item in outcomes if item.status in {"success", "no-change"}]
    aggregate = {
        "sources_selected": selected_count,
        "sources_checked": len(completed) + len(failed),
        "sources_succeeded": len(completed),
        "sources_failed": len(failed),
        "sources_skipped": len(skipped),
        "offline": offline,
        "dry_run": dry_run,
    }
    for key in SourceOutcome("_", "skipped").metrics():
        aggregate[key] = sum(item.metrics()[key] for item in outcomes)

    if failed and completed:
        status, partial = "partial", f"Selhalo zdrojů: {len(failed)}."
    elif failed:
        status, partial = "failed", None
    elif (aggregate["candidates_created"] + aggregate["candidates_updated"]
          + aggregate["matched_existing"] + aggregate["match_review_queued"]) == 0:
        status, partial = "no-change", None
    else:
        status, partial = "success", None

    checked = [item for item in outcomes if item.status != "skipped"]
    coverage = {
        "regions": sorted({item.region for item in checked if item.region}),
        "districts": sorted({item.district for item in checked if item.district}),
        "municipalities": sorted({item.municipality for item in checked if item.municipality}),
        "sources": sorted(item.source_id for item in checked),
    }
    errors = [
        {"source_id": item.source_id, "message": item.error}
        for item in outcomes if item.error and item.status == "failed"
    ]
    if skipped:
        notes = notes + [
            "Přeskočené zdroje bez podporovaného adaptéru: "
            + ", ".join(item.source_id for item in skipped) + "."
        ]
    return {
        "schema_version": 1,
        "agent": "pipeline",
        "run_id": run_id,
        "started_at": _iso(started),
        "finished_at": _iso(finished),
        "duration_seconds": max(0.0, (finished - started).total_seconds()),
        "status": status,
        "partial_reason": partial,
        "commit_sha": None,
        "metrics": aggregate,
        "coverage": coverage,
        "errors": errors,
        "notes": notes,
    }


def write_run_report(report: dict[str, Any], root: Path) -> Path:
    moment = datetime.fromisoformat(report["started_at"])
    directory = Path(root) / moment.strftime("%Y-%m")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{report['run_id']}.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path


def _unique_run_id(connection, moment: datetime, report_root: Path) -> str:
    prefixes = (moment.strftime("%Y-%m-%d-%H%M"),
                moment.strftime("%Y-%m-%d-%H%M%S"),
                moment.strftime("%Y-%m-%d-%H%M%S%f"))
    month = moment.strftime("%Y-%m")
    for prefix in prefixes:
        value = f"{prefix}-pipeline"
        in_db = connection.execute(
            "SELECT 1 FROM pipeline_run WHERE id = ?", (value,)
        ).fetchone()
        if not in_db and not (Path(report_root) / month / f"{value}.json").exists():
            return value
    raise RuntimeError("Nelze vytvořit jednoznačné run_id.")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _moment(value: datetime | None) -> datetime:
    moment = value or datetime.now(LOCAL_TZ)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=LOCAL_TZ)
    return moment


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--source", action="append", default=[], metavar="ID",
                        help="omezit běh na enabled source id; lze opakovat")
    parser.add_argument("--due", action="store_true",
                        help="spustit jen zdroje po check_interval_days")
    parser.add_argument("--offline", action="store_true",
                        help="použít poslední snapshoty bez sítě")
    parser.add_argument("--dry-run", action="store_true",
                        help="zpracovat tok, ale vrátit DB i reportové změny")
    parser.add_argument("--snapshot-dir", type=Path, default=None)
    parser.add_argument("--report-root", type=Path, default=None)
    args = parser.parse_args()

    connection = db.connect(args.database, create=False)
    try:
        result = run_batch(
            connection, source_ids=args.source, due=args.due,
            offline=args.offline, dry_run=args.dry_run,
            snapshot_dir=args.snapshot_dir, report_root=args.report_root)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({
        **result.report,
        "report_path": str(result.report_path) if result.report_path else None,
    }, ensure_ascii=False, indent=2))
    return 1 if result.report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
