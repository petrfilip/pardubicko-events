#!/usr/bin/env python3
"""Test společné fetch/snapshot vrstvy s lokálním fake HTTP klientem."""

from __future__ import annotations

import io
import sqlite3
import sys
import tempfile
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import db  # noqa: E402
import fetch  # noqa: E402


def check(label, actual, expected) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: očekáváno {expected!r}, skutečnost {actual!r}")
    print(f"OK  {label}")


class FakeResponse:
    def __init__(self, body: bytes, status=200, headers=None):
        self._body = body
        self.status = status
        self.headers = headers or {}

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def add_source(connection, source_id="test-source", url="https://example.test/events"):
    connection.execute(
        "INSERT INTO source (id, name, url, type, priority, check_interval_days, enabled) "
        "VALUES (?, ?, ?, 'test', 'high', 1, 1)",
        (source_id, source_id, url),
    )
    connection.commit()


connection = db.connect(":memory:")
add_source(connection)
url = "https://example.test/events"
moment = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)

robots_http = FakeOpener(FakeResponse(
    b"User-agent: *\nDisallow: /private\nAllow: /\n"))
robots = fetch.RobotsPolicy(
    opener=robots_http, rate_limiter=fetch.HostRateLimiter(interval=0))
check("robots.txt zakáže privátní cestu",
      robots.allowed("https://robots.test/private/event"), False)
check("robots.txt povolí veřejnou cestu",
      robots.allowed("https://robots.test/events"), True)
check("robots.txt se pro host načte jen jednou", len(robots_http.requests), 1)

sleeps = []
rate_limiter = fetch.HostRateLimiter(
    interval=1.0, clock=lambda: 0.0, sleeper=sleeps.append)
rate_limiter.wait("https://same.test/a")
rate_limiter.wait("https://same.test/b")
rate_limiter.wait("https://other.test/a")
check("stejný host má sekvenční pauzu", sleeps, [1.0])

with tempfile.TemporaryDirectory() as directory:
    snapshots = Path(directory)
    limiter = fetch.HostRateLimiter(interval=0)
    first_http = FakeOpener(FakeResponse(
        b"stejny obsah", headers={
            "ETag": '"abc"', "Last-Modified": "Mon, 03 Aug 2026 09:00:00 GMT"}))
    first = fetch.fetch_url(
        connection, url, source_id="test-source", snapshot_dir=snapshots,
        opener=first_http, robots_policy=lambda _: True,
        rate_limiter=limiter, now=moment)
    check("úspěch vrátí snapshot", first.ok, True)
    check("snapshot existuje", first.snapshot_path.is_file(), True)
    check("tělo se bezeztrátově načte", first.snapshot.body, b"stejny obsah")
    check("úspěšný pokus je zapsán", connection.execute(
        "SELECT count(*) FROM source_fetch").fetchone()[0], 1)

    second_http = FakeOpener(FakeResponse(
        b"stejny obsah", headers={
            "ETag": '"abc"', "Last-Modified": "Mon, 03 Aug 2026 09:00:00 GMT"}))
    second = fetch.fetch_url(
        connection, url, source_id="test-source", snapshot_dir=snapshots,
        opener=second_http, robots_policy=lambda _: True,
        rate_limiter=limiter, now=moment)
    check("stejný obsah má stejnou cestu", second.snapshot_path, first.snapshot_path)
    check("stejný obsah nevytvoří druhý soubor",
          len(list(snapshots.rglob("*.gz"))), 1)
    request_headers = {key.lower(): value for key, value in second_http.requests[0].header_items()}
    check("If-None-Match se posílá", request_headers.get("if-none-match"), '"abc"')
    check("If-Modified-Since se posílá", request_headers.get("if-modified-since"),
          "Mon, 03 Aug 2026 09:00:00 GMT")

    not_modified_error = urllib.error.HTTPError(
        url, 304, "Not Modified", {"ETag": '"abc"'}, io.BytesIO())
    third = fetch.fetch_url(
        connection, url, source_id="test-source", snapshot_dir=snapshots,
        opener=FakeOpener(not_modified_error), robots_policy=lambda _: True,
        rate_limiter=limiter, now=moment)
    check("304 použije uložený snapshot", third.snapshot.body, b"stejny obsah")
    check("304 je rozlišeno", third.not_modified, True)

    offline = fetch.load_latest_snapshot(
        connection, "test-source", url, snapshot_dir=snapshots)
    check("offline načtení nepotřebuje opener", offline.snapshot.body, b"stejny obsah")

    failed = fetch.fetch_url(
        connection, url, source_id="test-source", snapshot_dir=snapshots,
        opener=FakeOpener(urllib.error.URLError("lokální test chyby")),
        robots_policy=lambda _: True, rate_limiter=limiter, now=moment)
    check("běžná síťová chyba vrátí neúspěch", failed.ok, False)
    check("neúspěšný pokus je zapsán", connection.execute(
        "SELECT count(*) FROM source_fetch WHERE error IS NOT NULL").fetchone()[0], 1)

    blocked = fetch.fetch_url(
        connection, "https://example.test/private", source_id="test-source",
        snapshot_dir=snapshots, opener=FakeOpener(),
        robots_policy=lambda _: False, rate_limiter=limiter, now=moment)
    check("robots zákaz neotevře síť", blocked.ok, False)
    check("robots zákaz je evidován", "robots.txt" in blocked.error, True)

    inbox = fetch.fetch_url(
        connection, "https://inbox.test/tip", source_id=None,
        snapshot_dir=snapshots, opener=FakeOpener(FakeResponse(b"inbox")),
        robots_policy=lambda _: True, rate_limiter=limiter, now=moment)
    check("inbox dostane snapshot_path", inbox.snapshot_path.is_file(), True)
    check("inbox bez source_id neporuší FK", inbox.fetch_id, None)

    add_source(connection, "pardubice-calendar", "https://pardubice.eu/kalendar-akci")
    pardubice_url = "https://pardubice.eu/kalendar-akci?page=6"
    fixture_body = (HERE / "fixtures" / "pardubice-calendar.html").read_bytes()
    fetch.fetch_url(
        connection, pardubice_url, source_id="pardubice-calendar",
        snapshot_dir=snapshots, opener=FakeOpener(FakeResponse(fixture_body)),
        robots_policy=lambda _: True, rate_limiter=limiter, now=moment)
    offline_fetches = fetch.fetch_source(
        connection, "pardubice-calendar", offline=True, snapshot_dir=snapshots)
    offline_result = fetch.extract_results(
        connection, offline_fetches,
        fetch.resolve_adapter({"id": "pardubice-calendar"}))
    check("adaptér běží nad snapshotem z offline plánu", offline_result.items_valid, 1)

    add_source(connection, "retention-source")
    retained = []
    for body in (b"posledni uspesna extrakce", b"stary neuspesny obsah", b"nejnovejsi obsah"):
        retained.append(fetch.fetch_url(
            connection, "https://retention.test/events",
            source_id="retention-source", snapshot_dir=snapshots,
            opener=FakeOpener(FakeResponse(body)), robots_policy=lambda _: True,
            rate_limiter=limiter, now=moment))
    connection.execute(
        "INSERT INTO source_extract "
        "(fetch_id, items_found, items_valid, items_unparsed, fill_rates) "
        "VALUES (?, 1, 1, 0, '{}')", (retained[0].fetch_id,))
    connection.commit()
    removed = fetch.prune_snapshots(
        connection, keep_per_source=1, snapshot_dir=snapshots)
    check("retence odstraní starý nereferenční snapshot",
          retained[1].snapshot_path in removed, True)
    check("retence drží poslední úspěšnou extrakci",
          retained[0].snapshot_path.exists(), True)
    check("retence drží nejnovější snapshot",
          retained[2].snapshot_path.exists(), True)

print("\nVšechny testy fetch/snapshot vrstvy prošly.")
