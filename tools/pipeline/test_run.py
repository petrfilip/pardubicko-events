#!/usr/bin/env python3
"""Offline integrační test runneru jako klienta API.

Server zastupuje `FakeApi`: registr zdrojů a obce bere z databáze
postavené `import_repo`, kandidáty a reporty drží v paměti. Kontrakt
skutečného API ověřují `web/tests/test_api.php` a `web/tests/test_runs.php`.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
import tempfile
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

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


class FakeApi:
    """Rozhraní `run.Api` nad pamětí; chyby API hází stejně jako `run.Api`."""

    def __init__(self, server, run_id: str = "pipeline-test") -> None:
        self.server = server
        self.run_id = run_id
        self.candidates: dict[str, dict[str, Any]] = {}
        self.runs: list[dict[str, Any]] = []
        self.writes = 0
        self.due: set[str] | None = None
        self.fail_candidate: str | None = None

    def get(self, path: str, query=None, *, allow=()):
        if path == "/sources":
            rows = self.server.execute("SELECT * FROM source ORDER BY id").fetchall()
            return {"sources": [{
                **{key: row[key] for key in ("id", "name", "url", "type", "adapter", "district",
                                             "region", "priority", "check_interval_days", "notes")},
                "municipality": row["municipality_name"],
                "enabled": bool(row["enabled"]),
                "due": self.due is None or row["id"] in self.due,
            } for row in rows]}
        if path == "/taxonomy":
            return {
                "municipalities": [dict(row) for row in self.server.execute(
                    "SELECT id, name, district, region FROM municipality")],
                "municipality_aliases": [dict(row) for row in self.server.execute(
                    "SELECT alias, municipality_id FROM municipality_alias")],
            }
        if path.startswith("/candidates/"):
            candidate = self.candidates.get(urllib.parse.unquote(path.split("/", 2)[2]))
            if candidate is None:
                if 404 in allow:
                    return None
                raise run.ApiError("GET", path, 404, {"error": "Kandidát neexistuje."})
            return candidate
        raise AssertionError(f"Neočekávané GET {path}")

    def post(self, path: str, body: dict[str, Any], *, allow=()):
        self.writes += 1
        if path == "/candidates":
            candidate = body["candidate"]
            if candidate["source_id"] == self.fail_candidate:
                raise run.ApiError("POST", path, 422, {"error": "Data neprošla kontrolou."})
            existing = self.candidates.get(candidate["id"])
            if existing is None:
                result = "created"
            elif existing["payload"] != candidate["payload"]:
                result = "updated"
            else:
                result = "unchanged"
            self.candidates[candidate["id"]] = candidate
            return {"result": result, "candidate": candidate}
        if path == "/runs":
            if any(item["run_id"] == self.run_id for item in self.runs):
                if 409 in allow:
                    return {"error": "Běh už je zapsaný.", "code": "run-exists", "run_id": self.run_id}
                raise run.ApiError("POST", path, 409, {"code": "run-exists"})
            self.runs.append({"run_id": self.run_id, **body["run"]})
            return {"run_id": self.run_id, "sources": len(body["run"]["sources"]), "health": []}
        raise AssertionError(f"Neočekávané POST {path}")


def seed_fixture(cache, source_id: str, fixture: str, snapshots: Path,
                 fetched_at: datetime) -> None:
    source = dict(cache.execute("SELECT * FROM source WHERE id = ?", (source_id,)).fetchone())
    adapter = fetch.resolve_adapter(source)
    body = (HERE / "fixtures" / fixture).read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    path = fetch.snapshot_path(digest, snapshots)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(body, mtime=0))
    for request in adapter.fetch_plan(source):
        cache.execute(
            "INSERT INTO source_fetch "
            "(source_id, url, fetched_at, http_status, content_hash, bytes, duration_ms) "
            "VALUES (?, ?, ?, 200, ?, ?, 0)",
            (source_id, request.url, fetched_at.isoformat(timespec="seconds"), digest, len(body)),
        )
    cache.commit()


def fresh_cache(api: FakeApi):
    cache = db.connect(Path(":memory:"))
    run.sync_catalog(api, cache)
    return cache


moment = datetime(2026, 8, 3, 12, 0, tzinfo=normalization.TZ)
server = db.connect(Path(":memory:"))
import_repo.import_all(server)

# Jednotkové hrany normalizace: doložený geografický alias, český termín,
# URL tracking a rozporná cena. Raw text musí zůstat nedotčený.
source = dict(server.execute("SELECT * FROM source WHERE id = 'ufc-janderov'").fetchone())
normalized = normalization.normalize_item(server, source, RawItem(
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

# Zrcadlo katalogu: cache dostane zdroje a obce ze serveru, alias funguje i v ní.
api = FakeApi(server)
cache = fresh_cache(api)
check("cache zrcadlí zdroje", cache.execute("SELECT count(*) FROM source").fetchone()[0],
      server.execute("SELECT count(*) FROM source").fetchone()[0])
cached_source = dict(cache.execute("SELECT * FROM source WHERE id = 'ufc-janderov'").fetchone())
check("normalizace nad cache najde alias", normalization.normalize_item(
    cache, cached_source, RawItem(uid="a", title="A", date_text="3. 8. 2026",
                                  municipality="Janderov")).normalized["municipality"], "Chrudim")

with tempfile.TemporaryDirectory() as name:
    snapshots = Path(name) / "snapshots"
    seed_fixture(cache, "pardubice-calendar", "pardubice-calendar.html", snapshots, moment)

    first = run.run_batch(api, cache, source_ids=["pardubice-calendar"], offline=True,
                          snapshot_dir=snapshots, now=moment)
    check("offline fixture doběhne", first.report["status"], "success")
    check("fixture projde extractem", first.report["metrics"]["items_found"], 3)
    check("čitelná i neúplná položka se pošle", first.report["metrics"]["candidates_created"], 2)
    check("neúplný termín skončí v karanténě", first.report["metrics"]["candidates_quarantined"], 1)
    check("kandidáti jsou na serveru", len(api.candidates), 2)
    sent = next(iter(api.candidates.values()))
    check("kandidát nese zdroj a způsob nálezu",
          (sent["source_id"], sent["discovery_method"]), ("pardubice-calendar", "adapter"))
    check("payload obsahuje raw položku", bool(sent["payload"]["raw"]["title"]), True)
    check("payload obsahuje hash snapshotu",
          len(sent["payload"]["provenance"]["snapshots"][0]["content_hash"]), 64)

    payload = run.report_payload(first)
    check("report pro API má výsledek zdroje",
          payload["run"]["sources"][0]["source_id"], "pardubice-calendar")
    check("offline běh neposílá stará stažení", payload["run"]["sources"][0]["fetches"], [])
    check("run_batch sám nic nereportuje", api.runs, [])
    check("report se odešle", run.send_report(api, payload)["run_id"], "pipeline-test")
    check("opakované odeslání je úspěch", run.send_report(api, payload),
          {"run_id": "pipeline-test", "already_reported": True})
    report_path = run.write_report(first, Path(name) / "runs")
    check("lokální kopie je tělo pro API",
          json.loads(report_path.read_text(encoding="utf-8")), json.loads(json.dumps(payload)))

    second = run.run_batch(api, cache, source_ids=["pardubice-calendar"], offline=True,
                           snapshot_dir=snapshots, now=moment + timedelta(minutes=1))
    check("opakovaný běh nic nevytvoří", second.report["status"], "no-change")
    check("opakovaný běh rozpozná stejné kandidáty", second.report["metrics"]["candidates_existing"], 2)
    check("počet kandidátů je idempotentní", len(api.candidates), 2)

    # Splatnost rozhoduje server.
    api.due = {"uffo-trutnov"}
    sources = run.sync_catalog(api, cache)
    check("--due bere splatnost ze serveru",
          [item["id"] for item in run.select_sources(sources, source_ids=None, due=True)], ["uffo-trutnov"])
    check("vybraný nesplatný zdroj se s --due vynechá",
          run.select_sources(sources, source_ids=["pardubice-calendar"], due=True), [])
    api.due = None

    seed_fixture(cache, "uffo-trutnov", "uffo-trutnov.ics", snapshots, moment)
    writes = api.writes
    fetches_before = cache.execute("SELECT count(*) FROM source_extract").fetchone()[0]
    dry = run.run_batch(api, cache, source_ids=["uffo-trutnov"], offline=True, dry_run=True,
                        snapshot_dir=snapshots, now=moment + timedelta(minutes=2))
    check("dry-run spočítá budoucí kandidáty", dry.report["metrics"]["candidates_created"], 2)
    check("dry-run do API nic nezapíše", api.writes, writes)
    check("dry-run vrátí cache", cache.execute("SELECT count(*) FROM source_extract").fetchone()[0],
          fetches_before)
    dry_again = run.run_batch(api, cache, source_ids=["pardubice-calendar"], offline=True, dry_run=True,
                              snapshot_dir=snapshots, now=moment + timedelta(minutes=2))
    check("dry-run pozná existující kandidáty", (dry_again.report["metrics"]["candidates_existing"],
          dry_again.report["metrics"]["candidates_created"]), (2, 0))

    seed_fixture(cache, "kultura-hk-official", "kultura-hk-official.html", snapshots, moment)
    remaining = run.run_batch(api, cache, source_ids=["uffo-trutnov", "kultura-hk-official"],
                              offline=True, snapshot_dir=snapshots, now=moment + timedelta(minutes=3))
    check("zbývající dvě golden fixtures doběhnou", remaining.report["status"], "success")
    counts: dict[str, int] = {}
    for candidate in api.candidates.values():
        counts[candidate["source_id"]] = counts.get(candidate["source_id"], 0) + 1
    check("všechny tři adaptéry mají kandidáty", counts,
          {"kultura-hk-official": 2, "pardubice-calendar": 2, "uffo-trutnov": 2})

# Izolace chyby: chybějící snapshot a odmítnutí API shodí jen svůj zdroj.
api = FakeApi(server, run_id="pipeline-isolated")
cache = fresh_cache(api)
with tempfile.TemporaryDirectory() as name:
    snapshots = Path(name) / "snapshots"
    seed_fixture(cache, "pardubice-calendar", "pardubice-calendar.html", snapshots, moment)
    seed_fixture(cache, "kultura-hk-official", "kultura-hk-official.html", snapshots, moment)
    api.fail_candidate = "kultura-hk-official"
    partial = run.run_batch(api, cache, source_ids=["pardubice-calendar", "uffo-trutnov",
                                                    "kultura-hk-official"],
                            offline=True, snapshot_dir=snapshots, now=moment)
    check("chyby dvou zdrojů dají partial", partial.report["status"], "partial")
    check("jeden zdroj uspěl", partial.report["metrics"]["sources_succeeded"], 1)
    check("dva zdroje selhaly", partial.report["metrics"]["sources_failed"], 2)
    check("úspěšné kandidáty chyba nesmaže",
          sum(1 for item in api.candidates.values() if item["source_id"] == "pardubice-calendar"), 2)
    errors = {item["source_id"]: item["message"] for item in partial.report["errors"]}
    check("chyba API je u zdroje čitelná", "HTTP 422" in errors["kultura-hk-official"], True)
    check("stav chyby jde do reportu pro API", {
        item["source_id"]: item["status"] for item in run.report_payload(partial)["run"]["sources"]
    }, {"pardubice-calendar": "success", "uffo-trutnov": "failed", "kultura-hk-official": "failed"})

print("\nRunner jako klient API: offline tok, idempotence, dry-run a izolace chyb prošly.")
