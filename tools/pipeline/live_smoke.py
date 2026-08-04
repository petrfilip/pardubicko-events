#!/usr/bin/env python3
"""Oddělený živý smoke test deterministických adaptérů (P2-4).

Tento příkaz je záměrně mimo deterministický test runner. Nejdřív bez
sítě ověří golden fixture; teprve když adaptér na uloženém vstupu funguje,
stáhne živý zdroj. Tím rozliší regresi kódu od změny vstupu.

Příkaz pouze zapisuje provozní report a snapshoty. Nikdy neupravuje adaptér
a nevolá LLM. Report pouze explicitně sdělí, zda existuje dost doložený
nezdravý stav, nad kterým lze samostatnou opravnou smyčku spustit.

    python3 tools/pipeline/live_smoke.py --source uffo-trutnov
    python3 tools/pipeline/live_smoke.py  # všechny zdroje s golden fixture
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import fetch  # noqa: E402
import health  # noqa: E402
from adapters.base import ExtractResult, Snapshot  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_REPORT_DIR = db.REPO_ROOT / "var" / "live-smoke" / "reports"
MAX_DIFF_LINES = 2000
REPAIRABLE_STATES = frozenset({
    health.STATE_SUSPECT,
    health.STATE_DEGRADED,
    health.STATE_SCHEMA_DRIFT,
})


@dataclass(frozen=True)
class FixtureCase:
    source_id: str
    input_name: str
    expected_name: str
    url: str


FIXTURE_CASES = {
    case.source_id: case for case in (
        FixtureCase(
            "kultura-hk-official", "kultura-hk-official.html",
            "kultura-hk-official.expected.json",
            "https://kultura.hradeckralove.cz/",
        ),
        FixtureCase(
            "uffo-trutnov", "uffo-trutnov.ics", "uffo-trutnov.expected.json",
            "https://uffo.cz/data/user-content/calendar/completeCalendar.ics",
        ),
        FixtureCase(
            "pardubice-calendar", "pardubice-calendar.html",
            "pardubice-calendar.expected.json",
            "https://pardubice.eu/kalendar-akci?page=6",
        ),
    )
}


def _timestamp(value=None) -> str:
    moment = value or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.isoformat(timespec="seconds")


def _golden_projection(result: ExtractResult) -> dict[str, Any]:
    return {
        "items_found": result.items_found,
        "items_valid": result.items_valid,
        "items_rejected": result.items_rejected,
        "items_unparsed": result.items_unparsed,
        "category_values_rejected": result.category_values_rejected,
        "items": [item.to_dict() for item in result.items],
    }


def check_fixture(source_id: str, adapter: ModuleType) -> dict[str, Any]:
    """Ověří aktuální kód adaptéru na verzovaném vstupu."""
    case = FIXTURE_CASES[source_id]
    fixture_dir = HERE / "fixtures"
    expected = json.loads(
        (fixture_dir / case.expected_name).read_text(encoding="utf-8"))
    snapshot = Snapshot.from_path(
        fixture_dir / case.input_name, source_id=source_id, url=case.url)
    try:
        actual = _golden_projection(adapter.extract(snapshot))
        error = None
    except Exception as exc:  # noqa: BLE001 - report musí zachytit i pád adaptéru
        actual = None
        error = f"{type(exc).__name__}: {exc}"
    matches = error is None and actual == expected
    return {
        "input": str((fixture_dir / case.input_name).relative_to(db.REPO_ROOT)),
        "expected": str((fixture_dir / case.expected_name).relative_to(db.REPO_ROOT)),
        "matches": matches,
        "error": error,
        "expected_projection": expected if not matches else None,
        "actual_projection": actual if not matches else None,
    }


def shape_profile(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Kompaktní, obsahově nezávislý profil tvaru extrakce."""
    field_names = sorted({key for item in items for key in item})
    types: dict[str, list[str]] = {}
    fill_rates: dict[str, float] = {}
    for name in field_names:
        values = [item.get(name) for item in items]
        types[name] = sorted({type(value).__name__ for value in values if value is not None})
        fill_rates[name] = (
            round(sum(value not in (None, "", [], {}) for value in values) / len(values), 4)
            if values else 0.0
        )
    return {
        "items": len(items),
        "fields": field_names,
        "types": types,
        "fill_rates": fill_rates,
    }


def compare_structure(expected_projection: dict[str, Any],
                      current: ExtractResult) -> dict[str, Any]:
    """Porovná kontrakt položek a vyplnění klíčových polí s fixture."""
    expected = shape_profile(expected_projection.get("items", []))
    actual_items = [item.to_dict() for item in current.items]
    actual = shape_profile(actual_items)
    issues: list[dict[str, Any]] = []

    missing = sorted(set(expected["fields"]) - set(actual["fields"]))
    if missing and actual_items:
        issues.append({"kind": "missing-fields", "fields": missing})

    for name, expected_types in expected["types"].items():
        incompatible = sorted(set(actual["types"].get(name, [])) - set(expected_types))
        if expected_types and incompatible:
            issues.append({
                "kind": "incompatible-types", "field": name,
                "expected": expected_types, "actual": actual["types"].get(name, []),
            })

    if expected_projection.get("items_found", 0) > 0 and current.items_found == 0:
        issues.append({"kind": "items-empty", "fixture_items_found":
                       expected_projection["items_found"]})
    elif current.items_found:
        for name in ("title", "start_at", "url"):
            baseline = expected["fill_rates"].get(name, 0.0)
            observed = actual["fill_rates"].get(name, 0.0)
            if baseline > 0 and observed < health.SCHEMA_DRIFT_FILL_RATIO * baseline:
                issues.append({
                    "kind": "fill-rate-drop", "field": name,
                    "fixture": baseline, "current": observed,
                    "ratio_threshold": health.SCHEMA_DRIFT_FILL_RATIO,
                })

    return {"fixture": expected, "current": actual, "issues": issues}


def classify(*, fixture_matches: bool, fetch_ok: bool, health_state: str,
             structure_issues: list[dict[str, Any]], extraction_error: str | None = None
             ) -> tuple[str, str]:
    """Vrátí `(classification, smoke_state)` bez skrytého heuristického stavu."""
    if not fixture_matches:
        return "code-regression", "code-regression"
    if extraction_error:
        return "source-change", health.STATE_SCHEMA_DRIFT
    if not fetch_ok:
        if health_state == health.STATE_BROKEN:
            return "source-access-failure", health_state
        return "transient-fetch-failure", health_state
    if health_state in REPAIRABLE_STATES:
        return "source-change", health_state
    if health_state == health.STATE_BROKEN:
        return "source-access-failure", health_state
    if health_state == health.STATE_STALE:
        return "source-content-stale", health_state
    # Fixture popisuje kontrakt adaptéru, ne obvyklý objem konkrétního
    # zdroje. Samotná prázdná živá odpověď proto nesmí obejít pravidlo
    # nízkého baseline z ADR 0004. Strukturální odchylky zůstanou v reportu,
    # ale nezdravý stav vzniká až z provozní historie (nebo z doloženého
    # pádu extrakce výše).
    return "healthy", health.STATE_HEALTHY


def _previous_snapshot_refs(connection, source: dict, adapter: ModuleType,
                            snapshot_dir: Path) -> list[dict[str, Any]]:
    refs = []
    for request in adapter.fetch_plan(source):
        row = connection.execute(
            "SELECT f.id, f.url, f.fetched_at, f.http_status, f.content_hash "
            "FROM source_fetch f JOIN source_extract e ON e.fetch_id = f.id "
            "WHERE f.source_id = ? AND f.url = ? AND f.error IS NULL "
            "  AND f.http_status BETWEEN 200 AND 399 AND e.items_valid > 0 "
            "  AND f.content_hash IS NOT NULL ORDER BY f.id DESC LIMIT 1",
            (source["id"], request.url),
        ).fetchone()
        if not row:
            continue
        path = fetch.snapshot_path(row["content_hash"], snapshot_dir)
        if path.exists():
            refs.append({**dict(row), "path": path, "label": request.label})
    return refs


def _extract_refs(refs: list[dict[str, Any]], adapter: ModuleType,
                  source_id: str) -> ExtractResult:
    result = ExtractResult()
    for ref in refs:
        snapshot = Snapshot.from_path(
            ref["path"], url=ref["url"], source_id=source_id,
            fetched_at=ref["fetched_at"], status=ref["http_status"],
            content_hash=ref["content_hash"], request_label=ref["label"],
        )
        result.merge(adapter.extract(snapshot))
    return result


def unified_diff(previous: str, current: str, *, previous_label: str,
                 current_label: str, max_lines: int = MAX_DIFF_LINES) -> dict[str, Any]:
    lines = list(difflib.unified_diff(
        previous.splitlines(), current.splitlines(),
        fromfile=previous_label, tofile=current_label, lineterm="",
    ))
    truncated = len(lines) > max_lines
    shown = lines[:max_lines]
    return {
        "text": "\n".join(shown),
        "lines_total": len(lines),
        "lines_included": len(shown),
        "truncated": truncated,
    }


def _repair_evidence(previous_refs: list[dict[str, Any]],
                     current_fetches: list[fetch.FetchResult], adapter: ModuleType,
                     source_id: str, current_result: ExtractResult) -> dict[str, Any]:
    previous_by_url = {ref["url"]: ref for ref in previous_refs}
    input_diffs = []
    for current in current_fetches:
        previous = previous_by_url.get(current.url)
        if not previous or not current.snapshot:
            continue
        old_snapshot = Snapshot.from_path(previous["path"], url=previous["url"])
        input_diffs.append({
            "url": current.url,
            "previous_fetch_id": previous["id"],
            "previous_hash": previous["content_hash"],
            "current_fetch_id": current.fetch_id,
            "current_hash": current.snapshot.content_hash,
            "diff": unified_diff(
                old_snapshot.text(), current.snapshot.text(),
                previous_label=f"previous:{previous['content_hash']}",
                current_label=f"current:{current.snapshot.content_hash}",
            ),
        })

    previous_result = _extract_refs(previous_refs, adapter, source_id)
    previous_output = previous_result.to_dict() if previous_refs else None
    current_output = current_result.to_dict()
    output_diff = None
    if previous_output is not None:
        output_diff = unified_diff(
            json.dumps(previous_output, ensure_ascii=False, indent=2, sort_keys=True),
            json.dumps(current_output, ensure_ascii=False, indent=2, sort_keys=True),
            previous_label="last-successful-output.json",
            current_label="current-output.json",
        )
    return {
        "last_successful_snapshots": [
            {
                "fetch_id": ref["id"], "url": ref["url"],
                "fetched_at": ref["fetched_at"], "content_hash": ref["content_hash"],
                "snapshot_path": str(ref["path"]),
            }
            for ref in previous_refs
        ],
        "input_diffs": input_diffs,
        "last_successful_output": previous_output,
        "current_output": current_output,
        "output_diff": output_diff,
    }


def run_source(connection, source: dict, *, snapshot_dir: Path,
               now=None, fetch_kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Provede jeden smoke. `fetch_kwargs` slouží i testům s fake HTTP klientem."""
    started_at = _timestamp(now)
    adapter = fetch.resolve_adapter(source)
    fixture = check_fixture(source["id"], adapter)
    base = {
        "schema_version": 1,
        "tool": "adapter-live-smoke",
        "source_id": source["id"],
        "adapter": adapter.name,
        "started_at": started_at,
        "fixture": fixture,
        "adapter_modified": False,
        "llm_invoked": False,
    }
    if not fixture["matches"]:
        classification, smoke_state = classify(
            fixture_matches=False, fetch_ok=False, health_state=health.STATE_HEALTHY,
            structure_issues=[])
        return {
            **base, "finished_at": _timestamp(now), "classification": classification,
            "smoke_state": smoke_state, "healthy": False, "fetches": [],
            "structure": None, "health": None, "repair_evidence": None,
            "llm_repair": {
                "allowed": False,
                "reason": "Golden fixture neprošla; jde o regresi kódu, živý zdroj nebyl zatížen.",
            },
        }

    expected = json.loads(
        (HERE / "fixtures" / FIXTURE_CASES[source["id"]].expected_name)
        .read_text(encoding="utf-8"))
    previous_refs = _previous_snapshot_refs(connection, source, adapter, snapshot_dir)
    fetched = fetch.fetch_source(
        connection, source, adapter=adapter, snapshot_dir=snapshot_dir,
        **(fetch_kwargs or {}))
    current_result = ExtractResult()
    extraction_error = None
    try:
        current_result = fetch.extract_results(connection, fetched, adapter)
    except Exception as exc:  # noqa: BLE001 - chyba patří do reportu a diffu
        extraction_error = f"{type(exc).__name__}: {exc}"

    health_result = health.evaluate(connection, source["id"], now=now)
    structure = compare_structure(expected, current_result)
    fetch_ok = bool(fetched) and all(item.ok for item in fetched)
    classification, smoke_state = classify(
        fixture_matches=True, fetch_ok=fetch_ok, health_state=health_result.state,
        structure_issues=structure["issues"], extraction_error=extraction_error)
    unhealthy = classification != "healthy"

    evidence = None
    if classification == "source-change":
        evidence = _repair_evidence(
            previous_refs, fetched, adapter, source["id"], current_result)
    evidence_complete = bool(
        evidence and evidence["last_successful_snapshots"]
        and any(item.get("diff", {}).get("lines_total", 0) > 0
                for item in evidence["input_diffs"])
    )
    llm_allowed = smoke_state in REPAIRABLE_STATES and evidence_complete
    if llm_allowed:
        llm_reason = (
            f"Doložen stav {smoke_state}; fixture prošla a report obsahuje "
            "předchozí funkční snapshot i diff. Samotný smoke LLM nevolá.")
    elif smoke_state in REPAIRABLE_STATES:
        llm_reason = (
            f"Stav {smoke_state} je nezdravý, ale chybí předchozí funkční "
            "snapshot s diffem; LLM oprava není povolena.")
    else:
        llm_reason = "Stav není opravovatelná změna struktury zdroje."

    return {
        **base,
        "finished_at": _timestamp(now),
        "classification": classification,
        "smoke_state": smoke_state,
        "healthy": not unhealthy,
        "fetches": [item.to_dict() for item in fetched],
        "extraction_error": extraction_error,
        "metrics": {
            "items_found": current_result.items_found,
            "items_valid": current_result.items_valid,
            "items_rejected": current_result.items_rejected,
            "items_unparsed": current_result.items_unparsed,
            "fill_rates": current_result.fill_rates(),
        },
        "structure": structure,
        "health": health_result.to_dict(),
        "repair_evidence": evidence,
        "llm_repair": {"allowed": llm_allowed, "reason": llm_reason},
    }


def write_report(report: dict[str, Any], report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = report["started_at"].replace(":", "").replace("+", "-")
    base = report_dir / f"{stamp}-{report['source_id']}.json"
    target = base
    suffix = 2
    while target.exists():
        target = base.with_name(f"{base.stem}-{suffix}{base.suffix}")
        suffix += 1
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--source", action="append", choices=sorted(FIXTURE_CASES),
                        help="ID zdroje; lze opakovat (výchozí: všechny fixtures)")
    parser.add_argument("--snapshot-dir", type=Path, default=fetch.DEFAULT_SNAPSHOT_DIR)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args()

    connection = db.connect(args.database, create=False)
    source_ids = args.source or sorted(FIXTURE_CASES)
    failed = False
    for source_id in source_ids:
        row = connection.execute(
            "SELECT * FROM source WHERE id = ? AND enabled = 1", (source_id,)).fetchone()
        if row is None:
            parser.error(
                f"Zdroj {source_id!r} není v databázi nebo není enabled; "
                "spusť nejprve pipeline.py import.")
        report = run_source(
            connection, dict(row), snapshot_dir=args.snapshot_dir)
        path = write_report(report, args.report_dir)
        print(json.dumps({
            "source_id": source_id,
            "classification": report["classification"],
            "smoke_state": report["smoke_state"],
            "llm_repair_allowed": report["llm_repair"]["allowed"],
            "report": str(path),
        }, ensure_ascii=False))
        failed = failed or not report["healthy"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
