"""Testy inboxu bez živé sítě."""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIPELINE_DIR = HERE.parent / "pipeline"
for directory in (HERE, PIPELINE_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import db  # noqa: E402
import inbox  # noqa: E402

failures: list[str] = []


def check(label, actual, expected):
    if actual != expected:
        failures.append(f"{label}: očekáváno {expected!r}, dostal {actual!r}")


@dataclass
class FetchResult:
    snapshot_path: Path | None = None
    error: str | None = None


def fetcher_for(html: str, directory: Path):
    path = directory / "snapshot.html"
    path.write_text(html, encoding="utf-8")

    def fetcher(connection, url, **kwargs):
        return FetchResult(path)

    return fetcher


DETAIL = """<!doctype html><html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Event","name":"Koncert na náměstí",
"startDate":"2026-08-15T19:00:00+02:00","location":{"@type":"Place","name":"Náměstí"}}
</script></head><body><h1>Koncert na náměstí</h1><p>15. 8. 2026 v 19:00.</p>
<p>Podrobný oficiální popis akce pro návštěvníky. Program nabídne hudbu,
setkání a doprovodný program. Vstup je možný od osmnácti hodin a pořadatel
doporučuje přijít s předstihem. Informace o dopravě a přístupnosti budou
průběžně aktualizovány na této stránce.</p></body></html>"""

LISTING = """<!doctype html><html><body><h1>Kalendář akcí</h1>
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
{"@type":"Event","name":"První","startDate":"2026-08-10"},
{"@type":"Event","name":"Druhá","startDate":"2026-08-11"},
{"@type":"Event","name":"Třetí","startDate":"2026-08-12"}]}</script>
<p>Přehled kulturních, sportovních a komunitních událostí ve městě. U každé
akce najdete samostatný detail, čas, místo a kontakt na pořadatele. Nabídka
se průběžně mění a další termíny jsou dostupné na následujících stránkách.
Tento text je dost dlouhý, aby stránka nebyla považována za prázdnou kostru
vykreslovanou JavaScriptem.</p></body></html>"""

UNKNOWN = """<!doctype html><html><body><h1>O organizaci</h1><p>Jsme místní
spolek a tato obecná stránka popisuje naši činnost, historii, kontakty,
provozní dobu, tým, poslání a možnosti spolupráce. Nejde o detail jedné akce
ani o kalendář. Další obecné informace vyplňují stránku tak, aby klasifikátor
nepovažoval HTML za prázdnou JavaScriptovou kostru. Žádný termín zde není a
nic se proto nemá domýšlet.</p></body></html>"""


with tempfile.TemporaryDirectory() as temp_name:
    temp = Path(temp_name)
    conn = db.connect(":memory:")

    first = inbox.submit(
        conn, "http://Example.cz/akce/koncert/?utm_source=test#program",
        note="první poznámka", submitted_at="2026-08-03T08:00:00+00:00")
    duplicate = inbox.submit(
        conn, "https://example.cz/akce/koncert?fbclid=abc", note="druhá poznámka")
    check("normalizovaná URL se deduplikuje", duplicate.id, first.id)
    check("duplicita je označená", duplicate.duplicate, True)
    check("existuje jediný řádek", conn.execute(
        "SELECT count(*) n FROM inbox").fetchone()["n"], 1)
    check("poznámka se doplnila", conn.execute(
        "SELECT note FROM inbox WHERE id = ?", (first.id,)).fetchone()["note"],
        "první poznámka\ndruhá poznámka")

    detail = inbox.process_one(
        conn, first.id, now="2026-08-03T09:00:00+00:00",
        fetch_url_fn=fetcher_for(DETAIL, temp))
    check("detail vytvoří kandidáta", detail.state, "candidate")
    check("detail je klasifikovaný", detail.resolved_kind, "event-detail")
    candidate = conn.execute(
        "SELECT * FROM candidate WHERE id = ?", (detail.candidate_id,)).fetchone()
    payload = json.loads(candidate["payload"])
    check("ruční discovery metoda", payload["discovery_method"], "manual-submission")
    check("kandidát čeká na ověření", candidate["state"], "new")
    check("název se přečetl", payload["title"], "Koncert na náměstí")

    listing_row = inbox.submit(conn, "https://example.cz/kalendar")
    listing = inbox.process_one(
        conn, listing_row.id, fetch_url_fn=fetcher_for(LISTING, temp))
    check("výpis je návrh zdroje", listing.state, "source-proposal")
    check("výpis nevytváří kandidáta", listing.candidate_id, None)

    unknown_row = inbox.submit(conn, "https://example.cz/o-nas")
    unknown = inbox.process_one(
        conn, unknown_row.id, fetch_url_fn=fetcher_for(UNKNOWN, temp))
    check("nerozpoznaná stránka selže", unknown.state, "failed")
    check("selhání má důvod", bool(unknown.error), True)

    broken_row = inbox.submit(conn, "https://example.cz/docasne-nedostupne")

    def broken_fetcher(connection, url, **kwargs):
        return FetchResult(error="HTTP 503")

    for expected_attempt in (1, 2):
        retry = inbox.process_one(conn, broken_row.id, fetch_url_fn=broken_fetcher)
        check(f"pokus {expected_attempt} zůstává ve frontě", retry.state, "new")
        check(f"čítač pokusu {expected_attempt}", retry.attempts, expected_attempt)
    failed = inbox.process_one(conn, broken_row.id, fetch_url_fn=broken_fetcher)
    check("třetí chyba uzavře záznam", failed.state, "failed")
    check("pokusy končí na třech", failed.attempts, 3)

    # Hotový řádek se při dalším volání znovu nestahuje.
    again = inbox.process_one(conn, first.id, fetch_url_fn=broken_fetcher)
    check("hotový detail je idempotentní", again.attempts, 1)

if failures:
    print(f"NEPROŠLO {len(failures)} kontrol:")
    for failure in failures:
        print(" - " + failure)
    raise SystemExit(1)

print("Všechny kontroly inboxu prošly.")
