#!/usr/bin/env python3
"""Společná fetch/snapshot vrstva pro adaptéry i URL inbox.

Síť je soustředěná v tomto modulu. Extrakce vždy dostává uložený
`Snapshot`, takže ji lze zopakovat s `--offline` bez živého webu.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
from adapters import ical, pardubice_calendar, schema_org  # noqa: E402
from adapters.base import ExtractResult, Snapshot, record_extract  # noqa: E402

USER_AGENT = (
    "PardubickoEventsBot/0.1 "
    "(+https://github.com/petrfilip/pardubicko-events)"
)
DEFAULT_SNAPSHOT_DIR = db.REPO_ROOT / "var" / "snapshots"
DEFAULT_TIMEOUT = 30.0
MIN_HOST_INTERVAL = 1.0

ADAPTERS: dict[str, ModuleType] = {
    "schema_org": schema_org,
    "ical": ical,
    "pardubice_calendar": pardubice_calendar,
}
DEFAULT_ADAPTER_BY_SOURCE = {
    "kultura-hk-official": "schema_org",
    "uffo-trutnov": "ical",
    "pardubice-calendar": "pardubice_calendar",
}


@dataclass
class FetchResult:
    url: str
    fetch_id: int | None
    snapshot: Snapshot | None
    snapshot_path: Path | None
    not_modified: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.snapshot is not None and self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "fetch_id": self.fetch_id,
            "snapshot_path": str(self.snapshot_path) if self.snapshot_path else None,
            "content_hash": self.snapshot.content_hash if self.snapshot else None,
            "http_status": self.snapshot.status if self.snapshot else None,
            "not_modified": self.not_modified,
            "error": self.error,
        }


class HostRateLimiter:
    """Sekvenční pauza mezi dotazy na stejný host."""

    def __init__(self, interval: float = MIN_HOST_INTERVAL, *,
                 clock: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self.interval = interval
        self.clock = clock
        self.sleeper = sleeper
        self._last_request: dict[str, float] = {}

    def wait(self, url: str) -> None:
        host = (urlsplit(url).hostname or "").lower()
        previous = self._last_request.get(host)
        current = self.clock()
        if previous is not None:
            remaining = self.interval - (current - previous)
            if remaining > 0:
                self.sleeper(remaining)
        self._last_request[host] = self.clock()


GLOBAL_RATE_LIMITER = HostRateLimiter()


class RobotsPolicy:
    """Načte a kešuje robots.txt přes stejný opener a rate limiter."""

    def __init__(self, *, opener=None, rate_limiter: HostRateLimiter | None = None,
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        self.opener = opener or urllib.request.build_opener()
        self.rate_limiter = rate_limiter or GLOBAL_RATE_LIMITER
        self.timeout = timeout
        self._parsers: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme.lower()}://{parts.netloc.lower()}"
        if origin not in self._parsers:
            robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
            parser = urllib.robotparser.RobotFileParser(robots_url)
            request = urllib.request.Request(
                robots_url, headers={"User-Agent": USER_AGENT, "Accept": "text/plain,*/*;q=0.1"})
            try:
                self.rate_limiter.wait(robots_url)
                with self.opener.open(request, timeout=self.timeout) as response:
                    body = response.read().decode("utf-8", "replace")
                parser.parse(body.splitlines())
                self._parsers[origin] = parser
            except urllib.error.HTTPError as exc:
                # 401/403 znamená zákaz; chybějící robots.txt podle RFC povoluje.
                if exc.code in (401, 403):
                    parser.parse(["User-agent: *", "Disallow: /"])
                    self._parsers[origin] = parser
                else:
                    self._parsers[origin] = None
            except (OSError, urllib.error.URLError):
                # Nedostupný robots.txt nesmí předstírat explicitní zákaz.
                self._parsers[origin] = None
        parser = self._parsers[origin]
        return True if parser is None else parser.can_fetch(USER_AGENT, url)


def fetch_url(connection: sqlite3.Connection, url: str, *, source_id: str | None = None,
              snapshot_dir: str | Path | None = None, opener=None,
              robots_policy=None, rate_limiter: HostRateLimiter | None = None,
              timeout: float = DEFAULT_TIMEOUT, now=None,
              autocommit: bool = True) -> FetchResult:
    """Stáhne URL, uloží obsahový snapshot a případně zapíše `source_fetch`.

    `source_id=None` je varianta pro inbox: snapshot vznikne stejně, ale
    `source_fetch` se nezapisuje, protože tabulka má FK jen na registrované
    zdroje. Inbox eviduje svůj pokus ve vlastním záznamu.
    """
    target_dir = Path(snapshot_dir) if snapshot_dir else DEFAULT_SNAPSHOT_DIR
    http = opener or urllib.request.build_opener()
    limiter = rate_limiter or GLOBAL_RATE_LIMITER
    policy = robots_policy or RobotsPolicy(
        opener=http, rate_limiter=limiter, timeout=timeout)
    started = time.monotonic()

    if not _robots_allowed(policy, url):
        error = f"robots.txt zakazuje stažení {url}"
        fetch_id = _record_attempt(
            connection, source_id, url=url, fetched_at=_timestamp(now),
            duration_ms=_elapsed_ms(started), error=error)
        if autocommit:
            connection.commit()
        return FetchResult(url, fetch_id, None, None, error=error)

    previous = _latest_fetch(connection, source_id, url) if source_id else None
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,"
               "application/xml,text/calendar;q=0.9,*/*;q=0.5"}
    if previous:
        if previous["etag"]:
            headers["If-None-Match"] = previous["etag"]
        if previous["last_modified"]:
            headers["If-Modified-Since"] = previous["last_modified"]
    request = urllib.request.Request(url, headers=headers)

    try:
        limiter.wait(url)
        with http.open(request, timeout=timeout) as response:
            body = response.read()
            status = int(getattr(response, "status", None) or response.getcode() or 200)
            response_headers = dict(response.headers.items())
        return _store_response(
            connection, url, body, status=status, response_headers=response_headers,
            source_id=source_id, snapshot_dir=target_dir,
            fetched_at=_timestamp(now), duration_ms=_elapsed_ms(started),
            autocommit=autocommit)
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and previous:
            return _reuse_not_modified(
                connection, url, previous, dict(exc.headers.items()), source_id,
                target_dir, _timestamp(now), _elapsed_ms(started),
                autocommit=autocommit)
        error = f"HTTP {exc.code}: {exc.reason}"
        fetch_id = _record_attempt(
            connection, source_id, url=url, fetched_at=_timestamp(now),
            http_status=exc.code, etag=exc.headers.get("ETag"),
            last_modified=exc.headers.get("Last-Modified"),
            duration_ms=_elapsed_ms(started), error=error)
        if autocommit:
            connection.commit()
        return FetchResult(url, fetch_id, None, None, error=error)
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        error = f"{type(exc).__name__}: {exc}"
        fetch_id = _record_attempt(
            connection, source_id, url=url, fetched_at=_timestamp(now),
            duration_ms=_elapsed_ms(started), error=error)
        if autocommit:
            connection.commit()
        return FetchResult(url, fetch_id, None, None, error=error)


def fetch_source(connection: sqlite3.Connection, source: dict | str, *, adapter=None,
                 offline: bool = False, snapshot_dir: str | Path | None = None,
                 **fetch_kwargs) -> list[FetchResult]:
    """Provede fetch plán adaptéru pro registrovaný zdroj.

    `source` může být slovník z registru nebo jeho id už naimportované v DB.
    V offline režimu neproběhne žádný síťový ani databázový zápis.
    """
    source_dict = _source_dict(connection, source)
    adapter_module = resolve_adapter(source_dict, adapter)
    results: list[FetchResult] = []
    for planned in adapter_module.fetch_plan(source_dict):
        if offline:
            results.append(load_latest_snapshot(
                connection, source_dict["id"], planned.url,
                snapshot_dir=snapshot_dir, request_label=planned.label))
            continue
        result = fetch_url(
            connection, planned.url, source_id=source_dict["id"],
            snapshot_dir=snapshot_dir, **fetch_kwargs)
        if result.snapshot:
            result.snapshot.request_label = planned.label
        results.append(result)
    return results


def load_latest_snapshot(connection: sqlite3.Connection, source_id: str, url: str, *,
                         snapshot_dir: str | Path | None = None,
                         request_label: str | None = None) -> FetchResult:
    row = _latest_fetch(connection, source_id, url)
    if row is None or not row["content_hash"]:
        error = f"Pro {source_id} a {url} není uložený snapshot."
        return FetchResult(url, None, None, None, error=error)
    target_dir = Path(snapshot_dir) if snapshot_dir else DEFAULT_SNAPSHOT_DIR
    path = snapshot_path(row["content_hash"], target_dir)
    if not path.exists():
        error = f"Snapshot {path} chybí."
        return FetchResult(url, None, None, path, error=error)
    snapshot = Snapshot.from_path(
        path, url=url, source_id=source_id, fetched_at=row["fetched_at"],
        status=row["http_status"], content_hash=row["content_hash"],
        request_label=request_label,
        headers={"ETag": row["etag"] or "", "Last-Modified": row["last_modified"] or ""})
    return FetchResult(url, int(row["id"]), snapshot, path)


def resolve_adapter(source: dict, adapter=None) -> ModuleType:
    if isinstance(adapter, ModuleType):
        return adapter
    adapter_name = adapter or source.get("adapter") or DEFAULT_ADAPTER_BY_SOURCE.get(source.get("id"))
    if adapter_name not in ADAPTERS:
        raise ValueError(
            f"Zdroj {source.get('id')!r} nemá podporovaný adaptér; "
            f"známé: {', '.join(sorted(ADAPTERS))}.")
    return ADAPTERS[adapter_name]


def extract_results(connection: sqlite3.Connection, fetches: list[FetchResult],
                    adapter, *, autocommit: bool = True) -> ExtractResult:
    """Extrahuje všechny snapshoty, zapíše per-fetch metriky a sloučí výstup."""
    merged = ExtractResult()
    for fetched in fetches:
        if not fetched.snapshot:
            continue
        result = adapter.extract(fetched.snapshot)
        if fetched.fetch_id is not None:
            record_extract(connection, fetched.fetch_id, result,
                           autocommit=autocommit)
        merged.merge(result)
    return merged


def snapshot_path(content_hash: str, snapshot_dir: str | Path | None = None) -> Path:
    root = Path(snapshot_dir) if snapshot_dir else DEFAULT_SNAPSHOT_DIR
    return root / content_hash[:2] / f"{content_hash}.gz"


def prune_snapshots(connection: sqlite3.Connection, *, keep_per_source: int = 5,
                    snapshot_dir: str | Path | None = None) -> list[Path]:
    """Odstraní staré zdrojové snapshoty podle dokumentované retence.

    Pro každý zdroj zůstane posledních `keep_per_source` různých obsahů a
    navíc poslední obsah, nad kterým existuje úspěšně zapsaná extrakce.
    Mažou se jen hashe evidované v `source_fetch`; samostatné inbox snapshoty
    bez `source_id` tato údržba nezná a nedotkne se jich.
    """
    if keep_per_source < 1:
        raise ValueError("keep_per_source musí být alespoň 1")
    rows = connection.execute(
        "SELECT source_id, content_hash FROM source_fetch "
        "WHERE content_hash IS NOT NULL ORDER BY id DESC").fetchall()
    protected: set[str] = set()
    seen_by_source: dict[str, set[str]] = {}
    all_hashes: set[str] = set()
    for row in rows:
        digest = row["content_hash"]
        all_hashes.add(digest)
        seen = seen_by_source.setdefault(row["source_id"], set())
        if digest in seen:
            continue
        if len(seen) < keep_per_source:
            protected.add(digest)
        seen.add(digest)

    # Poslední skutečně dokončená extrakce může být starší než poslední fetch
    # (např. nový snapshot rozbil adaptér). Právě ten je referencí pro diff.
    successful = connection.execute(
        "SELECT f.source_id, f.content_hash FROM source_fetch f "
        "JOIN source_extract e ON e.fetch_id = f.id "
        "WHERE f.content_hash IS NOT NULL ORDER BY f.id DESC").fetchall()
    protected_sources: set[str] = set()
    for row in successful:
        if row["source_id"] not in protected_sources:
            protected.add(row["content_hash"])
            protected_sources.add(row["source_id"])

    removed: list[Path] = []
    for digest in sorted(all_hashes - protected):
        path = snapshot_path(digest, snapshot_dir)
        if path.exists():
            path.unlink()
            removed.append(path)
            try:
                path.parent.rmdir()
            except OSError:
                pass
    return removed


def _store_response(connection, url: str, body: bytes, *, status: int,
                    response_headers: dict[str, str], source_id: str | None,
                    snapshot_dir: Path, fetched_at: str, duration_ms: int,
                    autocommit: bool = True) -> FetchResult:
    digest = hashlib.sha256(body).hexdigest()
    path = snapshot_path(digest, snapshot_dir)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(body, mtime=0))
    etag = _header(response_headers, "ETag")
    last_modified = _header(response_headers, "Last-Modified")
    fetch_id = _record_attempt(
        connection, source_id, url=url, fetched_at=fetched_at, http_status=status,
        etag=etag, last_modified=last_modified, content_hash=digest,
        bytes_=len(body), duration_ms=duration_ms)
    if autocommit:
        connection.commit()
    snapshot = Snapshot(
        url=url, body=body, source_id=source_id, fetched_at=fetched_at,
        status=status, headers=response_headers, content_hash=digest)
    return FetchResult(url, fetch_id, snapshot, path)


def _reuse_not_modified(connection, url: str, previous, response_headers: dict[str, str],
                        source_id: str | None, snapshot_dir: Path,
                        fetched_at: str, duration_ms: int,
                        autocommit: bool = True) -> FetchResult:
    digest = previous["content_hash"]
    path = snapshot_path(digest, snapshot_dir) if digest else None
    if not path or not path.exists():
        error = "Server vrátil 304, ale předchozí snapshot chybí."
        fetch_id = _record_attempt(
            connection, source_id, url=url, fetched_at=fetched_at, http_status=304,
            duration_ms=duration_ms, error=error)
        if autocommit:
            connection.commit()
        return FetchResult(url, fetch_id, None, path, not_modified=True, error=error)
    body = gzip.decompress(path.read_bytes())
    etag = _header(response_headers, "ETag") or previous["etag"]
    last_modified = (_header(response_headers, "Last-Modified")
                     or previous["last_modified"])
    fetch_id = _record_attempt(
        connection, source_id, url=url, fetched_at=fetched_at, http_status=304,
        etag=etag, last_modified=last_modified, content_hash=digest,
        bytes_=len(body), duration_ms=duration_ms)
    if autocommit:
        connection.commit()
    snapshot = Snapshot(
        url=url, body=body, source_id=source_id, fetched_at=fetched_at,
        status=304, headers=response_headers, content_hash=digest)
    return FetchResult(url, fetch_id, snapshot, path, not_modified=True)


def _record_attempt(connection, source_id: str | None, *, url: str, fetched_at: str,
                    http_status=None, etag=None, last_modified=None,
                    content_hash=None, bytes_=None, duration_ms=None,
                    error=None) -> int | None:
    if source_id is None:
        return None
    cursor = connection.execute(
        "INSERT INTO source_fetch (source_id, url, fetched_at, http_status, etag, "
        "last_modified, content_hash, bytes, duration_ms, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (source_id, url, fetched_at, http_status, etag, last_modified,
         content_hash, bytes_, duration_ms, error),
    )
    return int(cursor.lastrowid)


def _latest_fetch(connection, source_id: str, url: str):
    return connection.execute(
        "SELECT * FROM source_fetch WHERE source_id = ? AND url = ? "
        "AND content_hash IS NOT NULL ORDER BY id DESC LIMIT 1",
        (source_id, url),
    ).fetchone()


def _source_dict(connection, source: dict | str) -> dict:
    if isinstance(source, dict):
        return dict(source)
    row = connection.execute("SELECT * FROM source WHERE id = ?", (source,)).fetchone()
    if row is None:
        raise ValueError(
            f"Zdroj {source!r} není v databázi; spusť nejprve pipeline.py import.")
    return dict(row)


def _robots_allowed(policy, url: str) -> bool:
    if callable(policy):
        return bool(policy(url))
    return bool(policy.allowed(url))


def _header(headers: dict[str, str], name: str) -> str | None:
    lowered = name.lower()
    return next((value for key, value in headers.items() if key.lower() == lowered), None)


def _timestamp(value=None) -> str:
    if callable(value):
        value = value()
    moment = value or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.isoformat(timespec="seconds")


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--source", required=True, help="id zdroje z registru")
    parser.add_argument("--offline", action="store_true",
                        help="použít poslední snapshot bez sítě")
    parser.add_argument("--snapshot-dir", type=Path, default=None)
    parser.add_argument("--keep-snapshots", type=int, default=5,
                        help="počet posledních různých obsahů na zdroj (výchozí 5)")
    args = parser.parse_args()

    connection = db.connect(args.database, create=False)
    source = _source_dict(connection, args.source)
    adapter = resolve_adapter(source)
    fetched = fetch_source(
        connection, source, adapter=adapter, offline=args.offline,
        snapshot_dir=args.snapshot_dir)
    result = extract_results(connection, fetched, adapter)
    removed = [] if args.offline else prune_snapshots(
        connection, keep_per_source=args.keep_snapshots,
        snapshot_dir=args.snapshot_dir)
    print(json.dumps({
        "source_id": args.source,
        "adapter": adapter.name,
        "fetches": [item.to_dict() for item in fetched],
        "metrics": {
            "found": result.items_found,
            "accepted": result.items_valid,
            "rejected": result.items_rejected,
            "unparsed": result.items_unparsed,
            "category_values_rejected": result.category_values_rejected,
        },
        "items": [item.to_dict() for item in result.items],
        "notes": result.notes,
        "snapshots_pruned": len(removed),
    }, ensure_ascii=False, indent=2))
    return 0 if fetched and all(item.ok for item in fetched) else 1


if __name__ == "__main__":
    raise SystemExit(main())
