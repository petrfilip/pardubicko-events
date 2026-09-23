#!/usr/bin/env python3
"""Dávkový runner registrovaných deterministických zdrojů jako klient API.

Zdroje, číselník obcí i výsledek patří serveru (ADR 0008). Runner si vezme
zdroje z `GET /api/v1/sources`, stáhne je, vytěží a normalizuje kandidáty,
pošle je do `POST /api/v1/candidates` a na konci běhu pošle report do
`POST /api/v1/runs`. Deduplikaci i zdraví zdrojů počítá server.

Lokální SQLite je jen cache: ETagy a Last-Modified pro podmíněné dotazy,
odkazy na snapshoty a zrcadlo zdrojů a obcí, které normalizace potřebuje.
Smí kdykoli zmizet; další běh ji postaví znovu.

Novou veřejnou akci tento nástroj nikdy nevytváří. Server kandidáta buď
založí, nebo ho při jisté shodě připojí jako další zdroj existující akce.

    python3 tools/pipeline/run.py --due
    python3 tools/pipeline/run.py --source pardubice-calendar --dry-run

Konfigurace API je stejná jako u `tools/client/pardubicko_client.py`
(`PARDUBICKO_API_URL`, `PARDUBICKO_API_TOKEN[_FILE]`). Označení běhu se
vygeneruje, pokud není v `PARDUBICKO_RUN_ID`.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "client"))

import db  # noqa: E402
import fetch  # noqa: E402
import normalization  # noqa: E402
from pardubicko_client import ApiClient, ClientError, new_run_id  # noqa: E402

LOCAL_TZ = normalization.TZ
DEFAULT_CACHE = db.REPO_ROOT / "var" / "pipeline-cache.db"
DEFAULT_REPORT_DIR = db.REPO_ROOT / "var" / "runs"
SOURCE_COLUMNS = ("id", "name", "url", "type", "adapter", "municipality_name", "district",
                  "region", "priority", "check_interval_days", "enabled", "notes")


class SourceRunError(RuntimeError):
    """Jeden zdroj selhal; dávka smí pokračovat dalšími zdroji."""


class ApiError(RuntimeError):
    """API odmítlo požadavek; nese stav a tělo odpovědi."""

    def __init__(self, method: str, path: str, status: int, body: Any) -> None:
        reason = body.get("error") if isinstance(body, dict) else None
        fields = body.get("fields") if isinstance(body, dict) else None
        detail = f"{reason} {json.dumps(fields, ensure_ascii=False)}" if fields else reason
        super().__init__(f"{method} {path} → HTTP {status}: {detail or body}")
        self.status = status
        self.body = body


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
    started_at: str | None = None
    finished_at: str | None = None
    fetches: list[dict[str, Any]] = field(default_factory=list)

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

    def to_api(self) -> dict[str, Any]:
        """Výsledek zdroje ve tvaru `POST /api/v1/runs`."""
        return {
            "source_id": self.source_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "items_found": self.items_found,
            "items_valid": self.items_valid,
            "candidates_created": self.candidates_created,
            "candidates_existing": self.candidates_existing,
            "candidates_updated": self.candidates_updated,
            "candidates_quarantined": self.candidates_quarantined,
            "error": self.error,
            "fetches": self.fetches,
        }


@dataclass
class BatchResult:
    report: dict[str, Any]
    outcomes: list[SourceOutcome] = field(default_factory=list)
    server: dict[str, Any] | None = None
    report_path: Path | None = None


class Api:
    """Obálka klienta, která chybu API mění na výjimku."""

    def __init__(self, client: ApiClient) -> None:
        self.client = client

    @property
    def run_id(self) -> str:
        return str(self.client.run_id)

    def get(self, path: str, query: dict[str, Any] | None = None, *,
            allow: tuple[int, ...] = ()) -> Any:
        response = self.client.get(path, query)
        if not response.ok and response.status not in allow:
            raise ApiError("GET", path, response.status, response.body)
        return None if response.status in allow else response.body

    def post(self, path: str, body: dict[str, Any], *, allow: tuple[int, ...] = ()) -> Any:
        response = self.client.post(path, body)
        if not response.ok and response.status not in allow:
            raise ApiError("POST", path, response.status, response.body)
        return response.body


def run_batch(api: Api, cache: sqlite3.Connection, *, source_ids: list[str] | None = None,
              due: bool = False, offline: bool = False, dry_run: bool = False,
              snapshot_dir: Path | None = None, now: datetime | None = None,
              fetch_kwargs: dict[str, Any] | None = None) -> BatchResult:
    """Projde zdroje a pošle kandidáty; report odešle `send_report`.

    Dry-run do API nezapisuje nic, jen čte.
    """
    started = _moment(now)
    sources = sync_catalog(api, cache)
    selected = select_sources(sources, source_ids=source_ids, due=due)
    dry_snapshot_temp = None
    if dry_run and not offline:
        # Dry-run nezanechá snapshoty ani při explicitním --snapshot-dir.
        dry_snapshot_temp = tempfile.TemporaryDirectory(prefix="pardubicko-pipeline-dry-run-")
        snapshot_dir = Path(dry_snapshot_temp.name)

    notes = ["Runner nevytváří nové akce; jistou shodu připojí jako zdroj server."]
    if offline:
        notes.append("Offline běh použil jen uložené snapshoty; stažení se neposílají.")
    if dry_run:
        notes.append("Dry-run jen četl z API a lokální cache vrátil do původního stavu.")

    outcomes: list[SourceOutcome] = []
    for source in selected:
        outcome = SourceOutcome(
            source_id=source["id"], status="running", municipality=source.get("municipality_name"),
            district=source.get("district"), region=source.get("region"),
            started_at=_iso(_moment(now)),
        )
        try:
            adapter = fetch.resolve_adapter(source)
        except ValueError as exc:
            outcome.status, outcome.error = "skipped", str(exc)
        else:
            try:
                _run_source(api, cache, source, adapter, outcome, offline=offline,
                            dry_run=dry_run, snapshot_dir=snapshot_dir,
                            fetch_kwargs=fetch_kwargs or {})
            except Exception as exc:  # noqa: BLE001 — hranice izolace zdroje
                if cache.in_transaction:
                    cache.rollback()
                outcome.status = "failed"
                outcome.error = str(exc) if isinstance(exc, (SourceRunError, ApiError)) \
                    else f"{type(exc).__name__}: {exc}"
        outcome.finished_at = _iso(_moment(now))
        outcomes.append(outcome)

    finished = _moment(now)
    report = _build_report(api.run_id, started, finished, outcomes, selected_count=len(selected),
                           offline=offline, dry_run=dry_run, notes=notes)
    result = BatchResult(report=report, outcomes=outcomes)
    if dry_snapshot_temp is not None:
        dry_snapshot_temp.cleanup()
    return result


def report_payload(result: BatchResult) -> dict[str, Any]:
    """Tělo `POST /api/v1/runs`."""
    return {"run": {
        "started_at": result.report["started_at"],
        "finished_at": result.report["finished_at"],
        "status": result.report["status"],
        "offline": result.report["metrics"]["offline"],
        "report": result.report,
        "sources": [outcome.to_api() for outcome in result.outcomes],
    }}


def send_report(api: Api, payload: dict[str, Any]) -> dict[str, Any]:
    """Pošle report. Už zapsaný běh (`409 run-exists`) je úspěch: první pokus prošel."""
    body = api.post("/runs", payload, allow=(409,))
    if isinstance(body, dict) and body.get("code") == "run-exists":
        return {"run_id": body.get("run_id"), "already_reported": True}
    if isinstance(body, dict) and "error" in body:
        raise ApiError("POST", "/runs", 409, body)
    return body


def sync_catalog(api: Api, cache: sqlite3.Connection) -> list[dict[str, Any]]:
    """Zrcadlí zdroje a obce ze serveru do cache a vrátí zdroje z API.

    Cache potřebuje zdroje kvůli cizímu klíči `source_fetch` a obce kvůli
    normalizaci. Co na serveru už není, v cache zůstane; nevadí to, runner
    bere seznam zdrojů vždy z API.
    """
    sources = api.get("/sources")["sources"]
    taxonomy = api.get("/taxonomy")
    cache.executemany(
        "INSERT INTO municipality (id, name, district, region) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET name = excluded.name, district = excluded.district, "
        "region = excluded.region",
        [(item["id"], item["name"], item["district"], item["region"])
         for item in taxonomy["municipalities"]],
    )
    cache.execute("DELETE FROM municipality_alias")
    cache.executemany(
        "INSERT INTO municipality_alias (alias, municipality_id) VALUES (?, ?)",
        [(item["alias"], item["municipality_id"]) for item in taxonomy["municipality_aliases"]],
    )
    rows = []
    for source in sources:
        source["municipality_name"] = source.get("municipality")
        rows.append(tuple(
            int(source[column]) if column == "enabled" else source.get(column)
            for column in SOURCE_COLUMNS))
    placeholders = ", ".join("?" for _ in SOURCE_COLUMNS)
    updates = ", ".join(f"{column} = excluded.{column}" for column in SOURCE_COLUMNS[1:])
    cache.executemany(
        f"INSERT INTO source ({', '.join(SOURCE_COLUMNS)}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}", rows)
    cache.commit()
    return sources


def select_sources(sources: list[dict[str, Any]], *, source_ids: list[str] | None,
                   due: bool) -> list[dict[str, Any]]:
    """Jen zapnuté zdroje; `due` bere splatnost tak, jak ji spočítal server."""
    known = {source["id"]: source for source in sources}
    requested = list(dict.fromkeys(source_ids or []))
    missing = [source_id for source_id in requested if source_id not in known]
    disabled = [source_id for source_id in requested
                if source_id in known and not known[source_id]["enabled"]]
    if missing:
        raise ValueError("Neznámé source id: " + ", ".join(missing))
    if disabled:
        raise ValueError("Zdroj je vypnutý: " + ", ".join(disabled))

    selected = [known[source_id] for source_id in requested] if requested else [
        source for source in sources if source["enabled"]]
    return [source for source in selected if source["due"]] if due else selected


def _run_source(api: Api, cache: sqlite3.Connection, source: dict[str, Any], adapter,
                outcome: SourceOutcome, *, offline: bool, dry_run: bool,
                snapshot_dir: Path | None, fetch_kwargs: dict[str, Any]) -> None:
    # Stažení se do cache zapíše i při HTTP chybě, aby šlo do reportu.
    # V dry-runu drží fetch i extrakci jedna vratná transakce.
    cache.execute("BEGIN")
    fetched = fetch.fetch_source(cache, source, adapter=adapter, offline=offline,
                                 snapshot_dir=snapshot_dir, autocommit=False, **fetch_kwargs)
    if not fetched:
        cache.rollback()
        raise SourceRunError("Adaptér nevytvořil žádný fetch požadavek.")
    failures = [item for item in fetched if not item.ok]
    if failures:
        if not offline:
            outcome.fetches = _fetch_records(cache, fetched)
        cache.rollback() if dry_run else cache.commit()
        raise SourceRunError("; ".join(item.error or "fetch selhal" for item in failures))

    extracted = fetch.extract_results(cache, fetched, adapter, autocommit=False)
    if not offline:
        outcome.fetches = _fetch_records(cache, fetched)
    outcome.items_found = extracted.items_found
    outcome.items_valid = extracted.items_valid
    outcome.items_unparsed = extracted.items_unparsed
    provenance = normalization.snapshot_provenance(fetched)
    candidates = [normalization.normalize_item(cache, source, item) for item in extracted.items]
    cache.rollback() if dry_run else cache.commit()

    for candidate in candidates:
        body = {
            "id": candidate.candidate_id,
            "source_id": source["id"],
            "discovery_method": "adapter",
            "payload": candidate.payload(source["id"], provenance),
            "state": candidate.state,
        }
        if dry_run:
            _predict(api, body, outcome)
        else:
            _submit(api, body, outcome)

    changed = (outcome.candidates_created + outcome.candidates_updated
               + outcome.matched_existing + outcome.match_review_queued)
    outcome.status = "success" if changed else "no-change"


def _submit(api: Api, body: dict[str, Any], outcome: SourceOutcome) -> None:
    response = api.post("/candidates", {"candidate": body})
    result = response.get("result")
    decision = (response.get("match") or {}).get("decision")
    if result == "merged":
        # Server kandidáta rovnou připojil k publikované akci a uzavřel.
        outcome.matched_existing += 1
    elif result == "created":
        outcome.candidates_created += 1
    else:
        outcome.candidates_existing += 1
        if result == "updated":
            outcome.candidates_updated += 1
    if decision == "review":
        outcome.match_review_queued += 1
    if (response.get("candidate") or {}).get("state") == "quarantined":
        outcome.candidates_quarantined += 1


def _predict(api: Api, body: dict[str, Any], outcome: SourceOutcome) -> None:
    """Dry-run: jen zjistí, jestli kandidát na serveru už je."""
    existing = api.get(f"/candidates/{urllib.parse.quote(body['id'], safe='')}", allow=(404,))
    if existing is None:
        outcome.candidates_created += 1
    else:
        outcome.candidates_existing += 1
        if _canonical_json(existing.get("payload")) != _canonical_json(body["payload"]):
            outcome.candidates_updated += 1
    if body["state"] == "quarantined":
        outcome.candidates_quarantined += 1


def _fetch_records(cache: sqlite3.Connection, fetched) -> list[dict[str, Any]]:
    """Stažení tohoto běhu ve tvaru reportu, i s výsledkem extrakce."""
    records = []
    for item in fetched:
        if item.fetch_id is None:
            continue
        row = cache.execute(
            "SELECT f.url, f.fetched_at, f.http_status, f.etag, f.last_modified, f.content_hash, "
            "f.bytes, f.duration_ms, f.error, e.items_found, e.items_valid, e.items_unparsed, "
            "e.fill_rates FROM source_fetch f LEFT JOIN source_extract e ON e.fetch_id = f.id "
            "WHERE f.id = ?", (item.fetch_id,)).fetchone()
        if row is None:
            continue
        record = {key: row[key] for key in ("url", "fetched_at", "http_status", "etag",
                                            "last_modified", "content_hash", "bytes",
                                            "duration_ms", "error")}
        record["extract"] = None if row["items_found"] is None else {
            "items_found": row["items_found"],
            "items_valid": row["items_valid"],
            "items_unparsed": row["items_unparsed"],
            "fill_rates": json.loads(row["fill_rates"] or "{}"),
        }
        records.append(record)
    return records


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
    errors = [{"source_id": item.source_id, "message": item.error}
              for item in outcomes if item.error and item.status == "failed"]
    if skipped:
        notes = notes + ["Přeskočené zdroje bez podporovaného adaptéru: "
                         + ", ".join(item.source_id for item in skipped) + "."]
    return {
        "schema_version": 2,
        "agent": "pipeline",
        "run_id": run_id,
        "started_at": _iso(started),
        "finished_at": _iso(finished),
        "duration_seconds": max(0.0, (finished - started).total_seconds()),
        "status": status,
        "partial_reason": partial,
        "metrics": aggregate,
        "coverage": coverage,
        "errors": errors,
        "notes": notes,
    }


def write_report(result: BatchResult, root: Path) -> Path:
    """Lokální kopie těla reportu. Když odeslání selže, pošle se znovu přes
    `pardubicko_client.py runs report --file SOUBOR` se stejným run_id."""
    moment = datetime.fromisoformat(result.report["started_at"])
    directory = Path(root) / moment.strftime("%Y-%m")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{result.report['run_id']}.json"
    payload = report_payload(result)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


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
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE,
                        help=f"lokální cache stažení (výchozí {DEFAULT_CACHE})")
    parser.add_argument("--source", action="append", default=[], metavar="ID",
                        help="omezit běh na zapnutý zdroj; lze opakovat")
    parser.add_argument("--due", action="store_true",
                        help="jen zdroje, které server označil jako splatné")
    parser.add_argument("--offline", action="store_true",
                        help="použít poslední snapshoty bez sítě")
    parser.add_argument("--dry-run", action="store_true",
                        help="projít tok, ale do API nic nezapsat")
    parser.add_argument("--snapshot-dir", type=Path, default=None)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR,
                        help="kam uložit kopii reportu pro opakované odeslání")
    args = parser.parse_args()

    try:
        client = ApiClient.from_environment()
    except ClientError as error:
        parser.error(str(error))
    client.run_id = client.run_id or new_run_id("pipeline")
    api = Api(client)
    cache = db.connect(args.cache)
    try:
        result = run_batch(api, cache, source_ids=args.source, due=args.due,
                           offline=args.offline, dry_run=args.dry_run,
                           snapshot_dir=args.snapshot_dir)
    except ValueError as error:
        parser.error(str(error))
    except (ApiError, ClientError) as error:
        print(f"CHYBA: {error}", file=sys.stderr)
        return 2
    finally:
        cache.close()

    if not args.dry_run:
        # Kopie vzniká před odesláním, aby výpadek spojení report neztratil.
        result.report_path = write_report(result, args.report_dir)
        try:
            result.server = send_report(api, report_payload(result))
        except (ApiError, ClientError) as error:
            print(f"CHYBA: report se neodeslal ({error}). Kopie: {result.report_path}",
                  file=sys.stderr)
            return 2
    print(json.dumps({**result.report, "server": result.server,
                      "report_path": str(result.report_path) if result.report_path else None},
                     ensure_ascii=False, indent=2))
    return 1 if result.report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
