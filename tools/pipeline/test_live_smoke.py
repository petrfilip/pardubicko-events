#!/usr/bin/env python3
"""Deterministické testy živého smoke orchestru; HTTP klient je pouze fake."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import db  # noqa: E402
import fetch  # noqa: E402
import live_smoke  # noqa: E402
from adapters import ical  # noqa: E402


def check(label, actual, expected) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: očekáváno {expected!r}, dostal jsem {actual!r}")
    print(f"OK  {label}")


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status
        self.headers = {"Content-Type": "text/calendar; charset=utf-8"}

    def read(self) -> bytes:
        return self.body

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeOpener:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls = 0

    def open(self, _request, timeout=None):
        self.calls += 1
        return FakeResponse(self.body)


class NoWait:
    def wait(self, _url: str) -> None:
        return None


# Golden preflight je plně offline a hlídá regresi kódu.
for source_id, adapter_name in (
    ("kultura-hk-official", "schema_org"),
    ("uffo-trutnov", "ical"),
    ("pardubice-calendar", "pardubice_calendar"),
):
    check(f"{source_id}: fixture prochází",
          live_smoke.check_fixture(source_id, fetch.ADAPTERS[adapter_name])["matches"],
          True)

check("fixture chyba = regrese kódu",
      live_smoke.classify(
          fixture_matches=False, fetch_ok=False, health_state="healthy",
          structure_issues=[]),
      ("code-regression", "code-regression"))
check("fixture prošla + health suspect = změna zdroje",
      live_smoke.classify(
          fixture_matches=True, fetch_ok=True, health_state="suspect",
          structure_issues=[{"kind": "items-empty"}]),
      ("source-change", "suspect"))
check("prázdný vstup s nízkým baseline nevyvolá falešný poplach",
      live_smoke.classify(
          fixture_matches=True, fetch_ok=True, health_state="healthy",
          structure_issues=[{"kind": "items-empty"}]),
      ("healthy", "healthy"))


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    snapshots = root / "snapshots"
    reports = root / "reports"
    connection = db.connect(":memory:")
    source = {
        "id": "uffo-trutnov",
        "name": "UFFO Trutnov",
        "url": "https://www.uffo.cz/program/",
        "type": "cultural-organization",
        "adapter": "ical",
        "municipality_name": "Trutnov",
        "district": "Trutnov",
        "region": "kralovehradecky-kraj",
        "priority": "high",
        "check_interval_days": 2,
        "enabled": 1,
        "notes": None,
    }
    connection.execute(
        "INSERT INTO source (id, name, url, type, adapter, municipality_name, "
        "district, region, priority, check_interval_days, enabled, notes) "
        "VALUES (:id, :name, :url, :type, :adapter, :municipality_name, "
        ":district, :region, :priority, :check_interval_days, :enabled, :notes)",
        source,
    )
    connection.commit()

    fixture_body = (HERE / "fixtures" / "uffo-trutnov.ics").read_bytes()
    feed_url = ical.fetch_plan(source)[0].url
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)

    # Když preflight doloží regresi kódu, živý fetch se ani nepokusí otevřít.
    never_opened = FakeOpener(fixture_body)
    original_fixture_check = live_smoke.check_fixture
    live_smoke.check_fixture = lambda _source_id, _adapter: {
        "input": "fixture", "expected": "expected", "matches": False,
        "error": "simulovaná regrese", "expected_projection": {},
        "actual_projection": None,
    }
    try:
        regression = live_smoke.run_source(
            connection, source, snapshot_dir=snapshots, now=now,
            fetch_kwargs={"opener": never_opened})
    finally:
        live_smoke.check_fixture = original_fixture_check
    check("regrese kódu nezatíží živý zdroj", never_opened.calls, 0)
    check("regrese kódu je oddělená klasifikace",
          regression["classification"], "code-regression")

    # Tři doložené funkční běhy vytvoří health baseline 3 položk.
    for days_ago in (6, 4, 2):
        fetched = fetch.fetch_url(
            connection, feed_url, source_id=source["id"], snapshot_dir=snapshots,
            opener=FakeOpener(fixture_body), robots_policy=lambda _url: True,
            rate_limiter=NoWait(), now=now - timedelta(days=days_ago),
        )
        fetch.extract_results(connection, [fetched], ical)

    # Živý endpoint vrátí syntakticky kalendář, ale bez jediné akce.
    # Nástroj nesahá na síť: fake opener zároveň dokládá jeden request.
    opener = FakeOpener(b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n")
    report = live_smoke.run_source(
        connection, source, snapshot_dir=snapshots, now=now,
        fetch_kwargs={
            "opener": opener,
            "robots_policy": lambda _url: True,
            "rate_limiter": NoWait(),
        },
    )
    check("smoke použil jediný fake request", opener.calls, 1)
    check("health doložil suspect", report["health"]["state"], "suspect")
    check("klasifikace odlišila změnu zdroje", report["classification"],
          "source-change")
    check("adaptér nebyl automaticky změněn", report["adapter_modified"], False)
    check("LLM nebylo zavoláno", report["llm_invoked"], False)
    check("oprava je povolena až s doloženým diffem",
          report["llm_repair"]["allowed"], True)
    check("report obsahuje poslední funkční výstup",
          report["repair_evidence"]["last_successful_output"] is not None, True)
    check("report obsahuje vstupní diff",
          report["repair_evidence"]["input_diffs"][0]["diff"]["lines_total"] > 0,
          True)
    check("report obsahuje výstupní diff",
          report["repair_evidence"]["output_diff"]["lines_total"] > 0, True)

    first_path = live_smoke.write_report(report, reports)
    second_path = live_smoke.write_report(report, reports)
    check("report se zapsal", json.loads(first_path.read_text())["source_id"],
          "uffo-trutnov")
    check("report nikdy nepřepisuje historii", first_path != second_path, True)

print("\nVšechny testy živého smoke orchestru prošly bez sítě.")
