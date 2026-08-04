#!/usr/bin/env python3
"""Offline integrační test dávkového pipeline runneru WP3."""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import db  # noqa: E402
import fetch  # noqa: E402
import import_repo  # noqa: E402
import normalization  # noqa: E402
import run  # noqa: E402
from adapters.base import RawItem  # noqa: E402


def check(label, actual, expected) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: očekáváno {expected!r}, skutečnost {actual!r}")
    print(f"OK  {label}")


def seed_fixture(connection, source_id: str, fixture: str, snapshots: Path,
                 fetched_at: datetime) -> None:
    source = dict(connection.execute(
        "SELECT * FROM source WHERE id = ?", (source_id,)).fetchone())
    adapter = fetch.resolve_adapter(source)
    requests = adapter.fetch_plan(source)
    body = (HERE / "fixtures" / fixture).read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    path = fetch.snapshot_path(digest, snapshots)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(body, mtime=0))
    for request in requests:
        connection.execute(
            "INSERT INTO source_fetch "
            "(source_id, url, fetched_at, http_status, content_hash, bytes, duration_ms) "
            "VALUES (?, ?, ?, 200, ?, ?, 0)",
            (source_id, request.url, fetched_at.isoformat(timespec="seconds"),
             digest, len(body)),
        )
    connection.commit()


moment = datetime(2026, 8, 3, 12, 0, tzinfo=normalization.TZ)
connection = db.connect(Path(":memory:"))
import_repo.import_all(connection)

# Jednotkové hrany normalizace: doložený geografický alias, český termín,
# URL tracking a rozporná cena. Raw text musí zůstat nedotčený.
source = dict(connection.execute(
    "SELECT * FROM source WHERE id = 'ufc-janderov'").fetchone())
normalized = normalization.normalize_item(connection, source, RawItem(
    uid="edge-1", title="  Testovací   akce ",
    date_text="3. 8. 2026 od 18:30", municipality="Janderov",
    url="/akce/test/?utm_source=x&b=2&a=1#program",
    price_text="zdarma i 100 Kč",
))
check("český termín se normalizuje deterministicky",
      normalized.normalized["start_at"], "2026-08-03T18:30:00+02:00")
check("alias Janderov míří na Chrudim", normalized.normalized["municipality"], "Chrudim")
check("alias zachová vstup", normalized.normalized["municipality_input"], "Janderov")
check("tracking a fragment se odstraní",
      normalized.normalized["canonical_url"],
      "https://www.ufc-janderov.cz/akce/test?a=1&b=2")
check("rozporná cena jde do karantény", "conflicting-price" in normalized.quarantine_reasons, True)
check("raw cena zůstane doslovná", normalized.raw["price_text"], "zdarma i 100 Kč")
range_price, _ = normalization.normalize_price("100–200 Kč")
decimal_price, _ = normalization.normalize_price("100,50 Kč")
check("cenové rozpětí se nevydává za jednu částku", range_price["amount"], None)
check("desetinná cena se nevydává za částku 50", decimal_price["amount"], None)
check("rozpětí i desetinná cena zůstávají placené",
      (range_price["type"], decimal_price["type"]), ("paid", "paid"))

with tempfile.TemporaryDirectory() as name:
    root = Path(name)
    snapshots = root / "snapshots"
    reports = root / "reports"
    seed_fixture(connection, "pardubice-calendar", "pardubice-calendar.html",
                 snapshots, moment)
    events_before = connection.execute("SELECT count(*) FROM event").fetchone()[0]

    first = run.run_batch(
        connection, source_ids=["pardubice-calendar"], offline=True,
        snapshot_dir=snapshots, report_root=reports, now=moment)
    check("offline fixture doběhne", first.report["status"], "success")
    check("fixture projde extractem", first.report["metrics"]["items_found"], 3)
    check("čitelná i neúplná raw položka se uloží", first.report["metrics"]["candidates_created"], 2)
    check("neúplný termín skončí v karanténě",
          first.report["metrics"]["candidates_quarantined"], 1)
    check("report vznikne bez přepsání historie", first.report_path.is_file(), True)
    check("runner nevytvoří veřejnou událost",
          connection.execute("SELECT count(*) FROM event").fetchone()[0], events_before)
    payload = json.loads(connection.execute(
        "SELECT payload FROM candidate WHERE source_id = 'pardubice-calendar' "
        "ORDER BY state = 'quarantined' LIMIT 1").fetchone()[0])
    check("payload obsahuje raw položku", bool(payload["raw"]["title"]), True)
    check("payload obsahuje hash snapshotu",
          len(payload["provenance"]["snapshots"][0]["content_hash"]), 64)

    candidate_count = connection.execute(
        "SELECT count(*) FROM candidate WHERE source_id = 'pardubice-calendar'"
    ).fetchone()[0]
    second = run.run_batch(
        connection, source_ids=["pardubice-calendar"], offline=True,
        snapshot_dir=snapshots, report_root=reports,
        now=moment + timedelta(minutes=1))
    check("opakovaný běh nic nevytvoří", second.report["status"], "no-change")
    check("opakovaný běh rozpozná stejné kandidáty",
          second.report["metrics"]["candidates_existing"], 2)
    check("počet kandidátů je idempotentní", connection.execute(
        "SELECT count(*) FROM candidate WHERE source_id = 'pardubice-calendar'"
    ).fetchone()[0], candidate_count)

    not_due = run.select_sources(
        connection, source_ids=["pardubice-calendar"], due=True,
        now=moment + timedelta(hours=23, minutes=59))
    due = run.select_sources(
        connection, source_ids=["pardubice-calendar"], due=True,
        now=moment + timedelta(days=1))
    check("zdroj před intervalem není due", len(not_due), 0)
    check("zdroj na hranici intervalu je due", len(due), 1)

    seed_fixture(connection, "uffo-trutnov", "uffo-trutnov.ics",
                 snapshots, moment)
    before_dry = connection.execute("SELECT count(*) FROM candidate").fetchone()[0]
    runs_before_dry = connection.execute("SELECT count(*) FROM pipeline_run").fetchone()[0]
    dry = run.run_batch(
        connection, source_ids=["uffo-trutnov"], offline=True, dry_run=True,
        snapshot_dir=snapshots, report_root=reports,
        now=moment + timedelta(minutes=2))
    check("dry-run spočítá budoucí kandidáty", dry.report["metrics"]["candidates_created"], 2)
    check("dry-run neuloží kandidáty", connection.execute(
        "SELECT count(*) FROM candidate").fetchone()[0], before_dry)
    check("dry-run neuloží DB běh", connection.execute(
        "SELECT count(*) FROM pipeline_run").fetchone()[0], runs_before_dry)
    check("dry-run nevytvoří report", dry.report_path, None)

    seed_fixture(connection, "kultura-hk-official", "kultura-hk-official.html",
                 snapshots, moment)
    remaining = run.run_batch(
        connection, source_ids=["uffo-trutnov", "kultura-hk-official"],
        offline=True, snapshot_dir=snapshots, report_root=reports,
        now=moment + timedelta(minutes=3))
    check("zbývající dvě golden fixtures doběhnou", remaining.report["status"], "success")
    check("všechny tři adaptéry mají kandidáty", {
        row["source_id"]: row["n"] for row in connection.execute(
            "SELECT source_id, count(*) AS n FROM candidate "
            "WHERE source_id IN ('pardubice-calendar', 'uffo-trutnov', "
            "'kultura-hk-official') GROUP BY source_id")
    }, {"kultura-hk-official": 2, "pardubice-calendar": 2, "uffo-trutnov": 2})

# Izolace chyby: jeden offline snapshot existuje, druhý chybí. Úspěšný zdroj
# musí zůstat commitnutý a report musí přesně označit částečný běh.
isolated = db.connect(Path(":memory:"))
import_repo.import_all(isolated)
with tempfile.TemporaryDirectory() as name:
    root = Path(name)
    seed_fixture(isolated, "pardubice-calendar", "pardubice-calendar.html",
                 root / "snapshots", moment)
    partial = run.run_batch(
        isolated, source_ids=["pardubice-calendar", "uffo-trutnov"],
        offline=True, snapshot_dir=root / "snapshots", report_root=root / "reports",
        now=moment)
    check("chyba druhého zdroje dá partial", partial.report["status"], "partial")
    check("jeden zdroj uspěl", partial.report["metrics"]["sources_succeeded"], 1)
    check("jeden zdroj selhal", partial.report["metrics"]["sources_failed"], 1)
    check("úspěšné kandidáty chyba nesmaže", isolated.execute(
        "SELECT count(*) FROM candidate WHERE source_id = 'pardubice-calendar'"
    ).fetchone()[0], 2)
    check("per-source stav chyby je auditovatelný", isolated.execute(
        "SELECT status FROM pipeline_source_run WHERE source_id = 'uffo-trutnov'"
    ).fetchone()[0], "failed")

print("\nOffline end-to-end runner, idempotence, dry-run a izolace chyb prošly.")
