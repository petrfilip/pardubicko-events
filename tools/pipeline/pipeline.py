#!/usr/bin/env python3
"""Rozhraní k provozní databázi fáze 2.

Spuštění:

    python3 tools/pipeline/pipeline.py import      # repozitář -> databáze
    python3 tools/pipeline/pipeline.py export      # databáze -> repozitář
    python3 tools/pipeline/pipeline.py roundtrip   # ověření bezeztrátovosti
    python3 tools/pipeline/pipeline.py stats       # obsah databáze
    python3 tools/pipeline/pipeline.py candidates  # provozní backlog Curatora

`roundtrip` do repozitáře nic nezapisuje. Exportuje do dočasného adresáře
a porovná ho bajt po bajtu se skutečnými soubory.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import export_repo  # noqa: E402
import fetch  # noqa: E402
import import_repo  # noqa: E402
import jsonfmt  # noqa: E402
from adapters import pardubice_calendar  # noqa: E402
from adapters.base import Snapshot  # noqa: E402

REPO_ROOT = db.REPO_ROOT
EXPORTED_FILES = ("data/manifest.json",)
OPEN_CANDIDATE_STATES = ("new", "needs-verification", "quarantined", "verified")
CANDIDATE_STATES = OPEN_CANDIDATE_STATES + ("imported", "rejected")


class CandidateError(Exception):
    """Neplatná nebo rozporná kurátorská operace."""


def _exported_paths(root: Path) -> list[str]:
    paths = list(EXPORTED_FILES)
    paths += sorted(
        path.relative_to(root).as_posix()
        for path in (root / "data" / "weeks").glob("*.json")
    )
    return paths


def cmd_import(args) -> int:
    connection = db.connect(args.database)
    stats = import_repo.import_all(connection, args.root)
    for key, value in stats.items():
        print(f"{key}: {value}")
    print(f"\nDatabáze: {args.database or db.DEFAULT_DB_PATH}")
    return 0


FROZEN_MESSAGE = (
    "CHYBA: data v gitu jsou od 23. 9. 2026 zmrazená. Zdrojem pravdy je databáze "
    "na https://pardubicko.tix.cz (ADR 0008); zápis jde jen přes /api/v1. "
    "Kurátorské běhy stojí, dokud etapa 2 v docs/phase-3-plan.md nepřinese klienta API.")


def _frozen() -> int:
    print(FROZEN_MESSAGE, file=sys.stderr)
    return 2


def cmd_export(args) -> int:
    connection = db.connect(args.database, create=False)
    stats = export_repo.export_all(connection, args.root)
    for key, value in stats.items():
        print(f"{key}: {value}")
    return 0


def cmd_roundtrip(args) -> int:
    """Import z repozitáře, export do dočasného adresáře, porovnání."""
    root = Path(args.root) if args.root else REPO_ROOT

    connection = db.connect(":memory:")
    import_repo.import_all(connection, root)

    with tempfile.TemporaryDirectory() as name:
        target = Path(name)
        export_repo.export_all(connection, target)

        expected = _exported_paths(root)
        produced = _exported_paths(target)

        missing = sorted(set(expected) - set(produced))
        extra = sorted(set(produced) - set(expected))
        differing = [
            relative for relative in expected
            if relative in produced
            and not filecmp.cmp(root / relative, target / relative, shallow=False)
        ]

        for relative in missing:
            print(f"CHYBÍ    {relative}")
        for relative in extra:
            print(f"NAVÍC    {relative}")
        for relative in differing:
            print(f"LIŠÍ SE  {relative}")
            if args.diff:
                _print_diff(root / relative, target / relative)

        if missing or extra or differing:
            print(f"\nKolotoč nesedí: {len(missing)} chybí, {len(extra)} navíc, "
                  f"{len(differing)} se liší.")
            return 1

        print(f"Kolotoč sedí. Ověřeno souborů: {len(expected)}.")
        return 0


def _print_diff(original: Path, produced: Path) -> None:
    import difflib

    diff = difflib.unified_diff(
        original.read_text(encoding="utf-8").splitlines(keepends=True),
        produced.read_text(encoding="utf-8").splitlines(keepends=True),
        fromfile=f"repozitář/{original.name}", tofile=f"export/{produced.name}",
        n=2,
    )
    shown = 0
    for line in diff:
        print("    " + line.rstrip("\n"))
        shown += 1
        if shown > 60:
            print("    … zkráceno")
            break


def cmd_stats(args) -> int:
    connection = db.connect(args.database, create=False)
    tables = (
        "week", "event", "event_week", "event_category", "event_source",
        "source", "facebook_page", "candidate", "municipality", "category",
        "inbox", "source_fetch", "source_health", "pipeline_run",
        "pipeline_source_run",
    )
    for table in tables:
        count = connection.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
        print(f"{table:<16} {count}")
    return 0


def _candidate_filter(*, states: list[str] | tuple[str, ...],
                      source_id: str | None = None,
                      week_id: str | None = None) -> tuple[list[str], list[object]]:
    if not states:
        return ["0 = 1"], []
    placeholders = ", ".join("?" for _ in states)
    where = ["source_file IS NULL", f"state IN ({placeholders})"]
    params: list[object] = list(states)
    if source_id:
        where.append("source_id = ?")
        params.append(source_id)
    if week_id:
        monday, sunday = week_bounds(week_id)
        where += [
            "date(json_extract(payload, '$.normalized.start_at')) <= date(?)",
            "date(COALESCE(json_extract(payload, '$.normalized.end_at'), "
            "json_extract(payload, '$.normalized.start_at'))) >= date(?)",
        ]
        params += [sunday.isoformat(), monday.isoformat()]
    return where, params


def operational_candidates(connection, *, states: list[str] | tuple[str, ...],
                           source_id: str | None = None,
                           week_id: str | None = None,
                           limit: int = 100) -> list[dict]:
    """Vrátí provozní kandidáty adaptérů, nikoli zrcadla `research/`."""
    where, params = _candidate_filter(
        states=states, source_id=source_id, week_id=week_id)
    params.append(limit)
    rows = connection.execute(
        "SELECT id, source_id, inbox_id, discovery_method, payload, state, "
        "event_id, created_at, reviewed_at, notes FROM candidate WHERE "
        + " AND ".join(where)
        + " ORDER BY COALESCE(json_extract(payload, '$.normalized.start_at'), "
        "'9999-12-31'), created_at, id LIMIT ?",
        params,
    ).fetchall()

    result = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item["payload"])
        except json.JSONDecodeError as error:
            item["payload_error"] = str(error)
        result.append(item)
    return result


def count_operational_candidates(connection, *,
                                 states: list[str] | tuple[str, ...],
                                 source_id: str | None = None,
                                 week_id: str | None = None) -> int:
    """Spočítá celý filtrovaný backlog nezávisle na stránkovacím limitu."""
    where, params = _candidate_filter(
        states=states, source_id=source_id, week_id=week_id)
    return int(connection.execute(
        "SELECT count(*) AS n FROM candidate WHERE " + " AND ".join(where),
        params,
    ).fetchone()["n"])


def resolve_operational_candidate(connection, candidate_id: str, *,
                                  event_id: str | None = None,
                                  reject: bool = False,
                                  note: str | None = None) -> dict:
    """Uzavře provozního kandidáta po kurátorském rozhodnutí."""
    if bool(event_id) == bool(reject):
        raise CandidateError("Zvol právě jedno z event_id nebo reject.")
    if reject and not (note or "").strip():
        raise CandidateError("Zamítnutí vyžaduje konkrétní poznámku.")

    row = connection.execute(
        "SELECT * FROM candidate WHERE id = ?", (candidate_id,)
    ).fetchone()
    if row is None:
        raise CandidateError(f"Kandidát {candidate_id!r} neexistuje.")
    if row["source_file"] is not None:
        raise CandidateError(
            "Kandidát pochází z research/; uprav jeho zdrojový JSON, ne SQLite.")

    target_state = "rejected" if reject else "imported"
    target_event = None if reject else event_id
    if row["state"] in {"imported", "rejected"}:
        if row["state"] == target_state and row["event_id"] == target_event:
            return dict(row)
        raise CandidateError(
            f"Kandidát už je uzavřen ve stavu {row['state']!r}; "
            "rozporné rozhodnutí nebylo zapsáno.")

    if event_id and connection.execute(
        "SELECT 1 FROM event WHERE id = ?", (event_id,)
    ).fetchone() is None:
        raise CandidateError(
            f"Produkční akce {event_id!r} v databázi neexistuje; nejprve spusť import.")

    reviewed_at = datetime.now(ZoneInfo("Europe/Prague")).isoformat(timespec="seconds")
    clean_note = (note or "").strip() or None
    connection.execute(
        "UPDATE candidate SET state = ?, event_id = ?, reviewed_at = ?, "
        "notes = CASE WHEN ? IS NULL THEN notes "
        "  WHEN notes IS NULL OR notes = '' THEN ? ELSE notes || ' ' || ? END "
        "WHERE id = ?",
        (target_state, target_event, reviewed_at, clean_note,
         clean_note, clean_note, candidate_id),
    )
    if event_id:
        connection.execute(
            "UPDATE match_review SET state = CASE WHEN event_id = ? "
            "THEN 'merged' ELSE 'separate' END, decided_at = ?, note = ? "
            "WHERE candidate_id = ? AND state = 'pending'",
            (event_id, reviewed_at, clean_note, candidate_id),
        )
    else:
        connection.execute(
            "UPDATE match_review SET state = 'separate', decided_at = ?, note = ? "
            "WHERE candidate_id = ? AND state = 'pending'",
            (reviewed_at, clean_note, candidate_id),
        )
    connection.commit()
    return dict(connection.execute(
        "SELECT * FROM candidate WHERE id = ?", (candidate_id,)
    ).fetchone())


def cmd_candidates(args) -> int:
    connection = db.connect(args.database, create=False)
    states = args.state or list(OPEN_CANDIDATE_STATES)
    items = operational_candidates(
        connection, states=states, source_id=args.source,
        week_id=args.week, limit=args.limit)
    total = count_operational_candidates(
        connection, states=states, source_id=args.source, week_id=args.week)
    print(json.dumps({"count": total, "shown": len(items), "total": total,
                      "candidates": items},
                     ensure_ascii=False, indent=2))
    return 0


def week_bounds(value: str) -> tuple[date, date]:
    try:
        year_text, week_text = value.split("-W", 1)
        monday = date.fromisocalendar(int(year_text), int(week_text), 1)
    except (ValueError, TypeError):
        raise CandidateError(
            f"Neplatný ISO týden {value!r}; očekává se například 2026-W33.")
    return monday, monday + timedelta(days=6)


def _research_backlog(root: Path) -> dict[str, dict]:
    latest: dict[str, tuple[str, str, dict]] = {}
    for path in sorted((root / "research").glob("candidates*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        stamp = str(data.get("generated_at") or "")
        for item in data.get("candidates") or []:
            candidate_id = item.get("id")
            if not candidate_id:
                continue
            rank = (stamp, path.name)
            previous = latest.get(candidate_id)
            if previous is None or rank >= previous[:2]:
                latest[candidate_id] = (stamp, path.name, item)
    return {candidate_id: entry[2] for candidate_id, entry in latest.items()}


def _research_week_match(item: dict, week_id: str) -> bool | None:
    """Vrátí shodu týdne; None znamená, že kandidát nemá čitelný termín."""
    monday, sunday = week_bounds(week_id)
    start_value = item.get("start_at")
    if isinstance(start_value, str):
        try:
            start = datetime.fromisoformat(start_value).date()
            end_value = item.get("end_at")
            end = datetime.fromisoformat(end_value).date() if end_value else start
            return start <= sunday and end >= monday
        except ValueError:
            pass
    raw = str(item.get("date_text") or "")
    dates: list[date] = []
    for day, month, year in re.findall(
            r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", raw):
        try:
            dates.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    for year, month, day in re.findall(
            r"(\d{4})-(\d{2})-(\d{2})", raw):
        try:
            dates.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    if dates:
        return any(monday <= value <= sunday for value in dates)
    return None


def cmd_backlog_summary(args) -> int:
    root = Path(args.root) if args.root else REPO_ROOT
    connection = db.connect(args.database, create=False)
    operational_rows = operational_candidates(
        connection, states=OPEN_CANDIDATE_STATES,
        week_id=args.week, limit=1_000_000)
    operational_ids = {item["id"] for item in operational_rows}
    operational_by_state: dict[str, int] = {}
    for item in operational_rows:
        operational_by_state[item["state"]] = operational_by_state.get(item["state"], 0) + 1

    research = _research_backlog(root)
    research_open_all = {
        candidate_id: item for candidate_id, item in research.items()
        if item.get("status", "new") in OPEN_CANDIDATE_STATES
    }
    research_unknown = 0
    if args.week:
        research_open = {}
        for candidate_id, item in research_open_all.items():
            match = _research_week_match(item, args.week)
            if match is True:
                research_open[candidate_id] = item
            elif match is None:
                research_unknown += 1
    else:
        research_open = research_open_all
    research_by_state: dict[str, int] = {}
    for item in research_open.values():
        state = item.get("status", "new")
        research_by_state[state] = research_by_state.get(state, 0) + 1

    overlap = operational_ids & set(research_open)
    output = {
        "week": args.week,
        "operational": {
            "total": len(operational_ids),
            "by_state": dict(sorted(operational_by_state.items())),
        },
        "research": {
            "total": len(research_open),
            "open_all_weeks": len(research_open_all),
            "unclassified_date": research_unknown,
            "by_state": dict(sorted(research_by_state.items())),
        },
        "overlap": len(overlap),
        "total_unique": len(operational_ids | set(research_open)),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _candidate_row(connection, candidate_id: str):
    row = connection.execute(
        "SELECT * FROM candidate WHERE id = ?", (candidate_id,)).fetchone()
    if row is None:
        raise CandidateError(f"Kandidát {candidate_id!r} neexistuje.")
    if row["source_file"] is not None:
        raise CandidateError(
            "Detailový příkaz pracuje jen s provozními kandidáty adaptérů.")
    try:
        payload = json.loads(row["payload"])
    except json.JSONDecodeError as error:
        raise CandidateError(f"Kandidát má neplatný payload: {error}.")
    return row, payload


def cmd_expand_candidate(args) -> int:
    connection = db.connect(args.database, create=False)
    try:
        row, payload = _candidate_row(connection, args.candidate_id)
        if row["source_id"] != "pardubice-calendar":
            raise CandidateError(
                "Detailové rozbalení je zatím deterministicky podporováno "
                "jen pro zdroj pardubice-calendar.")
        normalized = payload.get("normalized") or {}
        raw = payload.get("raw") or {}
        url = normalized.get("canonical_url") or raw.get("url")
        if not url:
            raise CandidateError("Kandidát nemá konkrétní detailní URL.")
        url = pardubice_calendar.canonical_detail_url(url)

        snapshot_path = None
        if args.snapshot:
            snapshot_path = Path(args.snapshot)
            snapshot = Snapshot.from_path(
                snapshot_path, url=url, source_id=row["source_id"])
        elif args.offline:
            fetched = fetch.load_latest_snapshot(
                connection, row["source_id"], url,
                snapshot_dir=args.snapshot_dir, request_label="candidate-detail")
            if not fetched.ok:
                raise CandidateError(fetched.error or "Detailní snapshot není dostupný.")
            snapshot, snapshot_path = fetched.snapshot, fetched.snapshot_path
        else:
            fetched = fetch.fetch_url(
                connection, url, source_id=row["source_id"],
                snapshot_dir=args.snapshot_dir)
            if not fetched.ok:
                raise CandidateError(fetched.error or "Detail se nepodařilo stáhnout.")
            snapshot, snapshot_path = fetched.snapshot, fetched.snapshot_path
        assert snapshot is not None
        result = pardubice_calendar.extract_detail(snapshot)
        items = result.items
        if args.week:
            monday, sunday = week_bounds(args.week)
            items = [item for item in items
                     if monday <= datetime.fromisoformat(item.start_at).date() <= sunday]
        proposal = {
            "candidate_id": args.candidate_id,
            "source_id": row["source_id"],
            "verified_source_url": url,
            "source_snapshot": str(snapshot_path) if snapshot_path else None,
            "content_hash": snapshot.content_hash,
            "week": args.week,
            "requires_review": True,
            "terms_found": len(result.items),
            "terms_shown": len(items),
            "items_unparsed": result.items_unparsed,
            "notes": result.notes,
            "terms": [item.to_dict() for item in items],
        }
        print(json.dumps(proposal, ensure_ascii=False, indent=2))
        return 0
    except CandidateError as error:
        print(f"CHYBA: {error}", file=sys.stderr)
        return 2


def _without_week(event: dict) -> dict:
    return {key: value for key, value in event.items() if key != "week"}


def _prepare_publication(root: Path, proposal: dict) -> tuple[dict, dict[str, dict], list[str]]:
    candidate_id = proposal.get("candidate_id")
    events = proposal.get("events")
    if not candidate_id or not isinstance(events, list) or not events:
        raise CandidateError("Návrh musí obsahovat candidate_id a neprázdné events.")

    manifest_path = root / "data" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    weeks = {item["id"]: item for item in manifest.get("weeks") or []}
    week_data = {
        week_id: json.loads((root / item["file"]).read_text(encoding="utf-8"))
        for week_id, item in weeks.items()
    }
    existing: dict[str, dict] = {}
    placements: set[tuple[str, str]] = set()
    for week_id, data in week_data.items():
        for event in data.get("events") or []:
            placements.add((event["id"], week_id))
            previous = existing.get(event["id"])
            if previous is not None and _without_week(previous) != _without_week(event):
                raise CandidateError(f"Existující akce {event['id']!r} má rozporné kopie.")
            existing[event["id"]] = event

    proposal_identity: dict[str, dict] = {}
    changed_weeks: list[str] = []
    required = {
        "id", "week", "title", "description", "start_at", "end_at",
        "all_day", "venue", "municipality", "categories", "price",
        "source", "cancelled",
    }
    for event in events:
        if not isinstance(event, dict):
            raise CandidateError("Každá položka events musí být objekt.")
        missing = sorted(required - set(event))
        if missing:
            raise CandidateError(
                f"Akce {event.get('id', '?')!r} postrádá pole: {', '.join(missing)}.")
        week_id, event_id = event["week"], event["id"]
        if week_id not in weeks:
            raise CandidateError(f"Týden {week_id!r} není v manifestu.")
        monday, sunday = week_bounds(week_id)
        try:
            start = datetime.fromisoformat(event["start_at"])
        except (TypeError, ValueError):
            raise CandidateError(f"Akce {event_id!r} má neplatný start_at.")
        try:
            end = datetime.fromisoformat(event["end_at"]) if event.get("end_at") else start
        except (TypeError, ValueError):
            raise CandidateError(f"Akce {event_id!r} má neplatný end_at.")
        if start.date() > sunday or end.date() < monday:
            raise CandidateError(
                f"Akce {event_id!r} nepřekrývá svůj týden {week_id}.")
        if not event.get("categories"):
            raise CandidateError(f"Akce {event_id!r} nemá kategorie.")
        if not (event.get("source") or {}).get("url"):
            raise CandidateError(f"Akce {event_id!r} nemá konkrétní source.url.")
        identity = proposal_identity.get(event_id)
        if identity is not None and _without_week(identity) != _without_week(event):
            raise CandidateError(f"Návrh obsahuje rozporné kopie akce {event_id!r}.")
        proposal_identity[event_id] = event
        if event_id in existing and _without_week(existing[event_id]) != _without_week(event):
            raise CandidateError(
                f"Akce {event_id!r} už existuje s jinými údaji; bezpečný publish ji nepřepíše.")
        if (event_id, week_id) not in placements:
            week_data[week_id].setdefault("events", []).append(event)
            placements.add((event_id, week_id))
            if week_id not in changed_weeks:
                changed_weeks.append(week_id)

    return manifest, week_data, changed_weeks


def cmd_publish_candidate(args) -> int:
    root = Path(args.root) if args.root else REPO_ROOT
    connection = db.connect(args.database, create=False)
    try:
        proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
        row, _payload = _candidate_row(connection, args.candidate_id)
        if proposal.get("candidate_id") != args.candidate_id:
            raise CandidateError("candidate_id v návrhu neodpovídá příkazu.")
        if row["state"] not in OPEN_CANDIDATE_STATES:
            raise CandidateError(f"Kandidát už je uzavřen ve stavu {row['state']!r}.")
        manifest, weeks, changed_weeks = _prepare_publication(root, proposal)
        event_ids = list(dict.fromkeys(event["id"] for event in proposal["events"]))
        primary_event_id = proposal.get("primary_event_id") or event_ids[0]
        if primary_event_id not in event_ids:
            raise CandidateError("primary_event_id není mezi publikovanými events.")
        receipt = {
            "candidate_id": args.candidate_id,
            "primary_event_id": primary_event_id,
            "event_ids": event_ids,
            "changed_weeks": changed_weeks,
            "mode": "apply" if args.apply else "preview",
        }
        if not args.apply:
            print(json.dumps(receipt, ensure_ascii=False, indent=2))
            return 0
        if not (args.note or "").strip():
            raise CandidateError("Publikace vyžaduje auditní --note.")

        stamp = datetime.now(ZoneInfo("Europe/Prague")).isoformat(timespec="seconds")
        manifest_path = root / "data" / "manifest.json"
        paths = ([manifest_path] if changed_weeks else []) + [
            root / next(item["file"] for item in manifest["weeks"] if item["id"] == week_id)
            for week_id in changed_weeks
        ]
        backups = {path: path.read_text(encoding="utf-8") for path in paths}
        try:
            for week_id in changed_weeks:
                weeks[week_id]["generated_at"] = stamp
                week_path = root / next(
                    item["file"] for item in manifest["weeks"] if item["id"] == week_id)
                jsonfmt.dump_file(week_path, weeks[week_id],
                                  inline_keys=jsonfmt.WEEK_INLINE_KEYS)
            if changed_weeks:
                manifest["generated_at"] = stamp
                jsonfmt.dump_file(manifest_path, manifest)
            import_repo.import_all(db.connect(":memory:"), root)
            import_repo.import_all(connection, root)
            resolve_operational_candidate(
                connection, args.candidate_id, event_id=primary_event_id,
                note=args.note)
        except Exception:
            connection.rollback()
            for path, content in backups.items():
                path.write_text(content, encoding="utf-8")
            import_repo.import_all(connection, root)
            raise
        receipt["generated_at"] = stamp
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    except (CandidateError, OSError, json.JSONDecodeError,
            import_repo.ImportError_) as error:
        print(f"CHYBA: {error}", file=sys.stderr)
        return 2


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("hodnota musí být kladné celé číslo")
    return parsed


def iso_week(value: str) -> str:
    try:
        week_bounds(value)
    except CandidateError as error:
        raise argparse.ArgumentTypeError(str(error))
    return value


def cmd_resolve_candidate(args) -> int:
    connection = db.connect(args.database, create=False)
    try:
        candidate = resolve_operational_candidate(
            connection, args.candidate_id, event_id=args.event,
            reject=args.reject, note=args.note)
    except CandidateError as error:
        print(f"CHYBA: {error}", file=sys.stderr)
        return 2
    print(json.dumps(candidate, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, default=None,
                        help=f"Cesta k databázi (výchozí {db.DEFAULT_DB_PATH}).")
    parser.add_argument("--root", type=Path, default=None, help="Kořen repozitáře.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("import", help="Načíst repozitář do databáze.")
    sub.add_parser("export", help="Zapsat databázi do repozitáře.")
    roundtrip = sub.add_parser("roundtrip", help="Ověřit bezeztrátovost migrace.")
    roundtrip.add_argument("--diff", action="store_true",
                           help="Vypsat rozdíl u lišících se souborů.")
    sub.add_parser("stats", help="Vypsat obsah databáze.")
    candidates = sub.add_parser(
        "candidates", help="Vypsat provozní kandidáty pro Curatora jako JSON.")
    candidates.add_argument("--state", action="append", choices=CANDIDATE_STATES,
                            help="Stav k zahrnutí; lze opakovat.")
    candidates.add_argument("--source", help="Omezit výpis na ID zdroje.")
    candidates.add_argument("--week", type=iso_week,
                            help="Omezit výpis na překryv s ISO týdnem.")
    candidates.add_argument("--limit", type=positive_int, default=100,
                            help="Nejvyšší počet řádků (výchozí 100).")
    resolve = sub.add_parser(
        "resolve-candidate", help="Uzavřít provozního kandidáta po ověření.")
    resolve.add_argument("candidate_id")
    decision = resolve.add_mutually_exclusive_group(required=True)
    decision.add_argument("--event", help="ID odpovídající produkční akce.")
    decision.add_argument("--reject", action="store_true",
                          help="Kandidáta doloženě zamítnout.")
    resolve.add_argument("--note", help="Auditní poznámka; pro zamítnutí povinná.")
    backlog = sub.add_parser(
        "backlog-summary", help="Přesný souhrn provozního a research backlogu.")
    backlog.add_argument("--week", type=iso_week,
                         help="Omezit provozní backlog na ISO týden.")
    expand = sub.add_parser(
        "expand-candidate", help="Načíst detail a navrhnout úplné termíny kandidáta.")
    expand.add_argument("candidate_id")
    expand.add_argument("--week", type=iso_week,
                        help="Ve výstupu ponechat jen zadaný ISO týden.")
    expand.add_argument("--offline", action="store_true",
                        help="Použít poslední uložený detailní snapshot.")
    expand.add_argument("--snapshot", type=Path,
                        help="Použít konkrétní lokální HTML fixture/snapshot.")
    expand.add_argument("--snapshot-dir", type=Path,
                        help="Jiný kořen obsahových snapshotů.")
    publish = sub.add_parser(
        "publish-candidate",
        help="Ověřit kurátorský návrh a publikovat jej s rollbackem při chybě.")
    publish.add_argument("candidate_id")
    publish.add_argument("--proposal", type=Path, required=True,
                         help="JSON s candidate_id, events a volitelným primary_event_id.")
    publish.add_argument("--apply", action="store_true",
                         help="Skutečně zapsat; bez přepínače jen bezpečný preview.")
    publish.add_argument("--note", help="Auditní důvod; při --apply povinný.")

    args = parser.parse_args()
    if args.command in {"export", "resolve-candidate"} or (
            args.command == "publish-candidate" and args.apply):
        return _frozen()
    return {
        "import": cmd_import, "export": cmd_export,
        "roundtrip": cmd_roundtrip, "stats": cmd_stats,
        "candidates": cmd_candidates, "resolve-candidate": cmd_resolve_candidate,
        "backlog-summary": cmd_backlog_summary,
        "expand-candidate": cmd_expand_candidate,
        "publish-candidate": cmd_publish_candidate,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
