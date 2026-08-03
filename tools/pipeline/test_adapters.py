#!/usr/bin/env python3
"""Golden testy tří prvních deterministických adaptérů, bez živé sítě."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from adapters import ical, pardubice_calendar, schema_org  # noqa: E402
from adapters.base import Snapshot  # noqa: E402


CASES = (
    ("kultura-hk-official", ".html", "https://kultura.hradeckralove.cz/", schema_org),
    ("uffo-trutnov", ".ics",
     "https://uffo.cz/data/user-content/calendar/completeCalendar.ics", ical),
    ("pardubice-calendar", ".html",
     "https://pardubice.eu/kalendar-akci?page=6", pardubice_calendar),
)


def check(label, actual, expected) -> None:
    if actual != expected:
        raise AssertionError(
            f"{label}\nOčekáváno:\n{json.dumps(expected, ensure_ascii=False, indent=2)}"
            f"\nSkutečnost:\n{json.dumps(actual, ensure_ascii=False, indent=2)}")
    print(f"OK  {label}")


for source_id, suffix, url, adapter in CASES:
    snapshot = Snapshot.from_path(
        HERE / "fixtures" / f"{source_id}{suffix}", url=url, source_id=source_id)
    expected = json.loads(
        (HERE / "fixtures" / f"{source_id}.expected.json").read_text(encoding="utf-8"))
    first = adapter.extract(snapshot)
    second = adapter.extract(snapshot)
    actual = {
        "items_found": first.items_found,
        "items_valid": first.items_valid,
        "items_rejected": first.items_rejected,
        "items_unparsed": first.items_unparsed,
        "category_values_rejected": first.category_values_rejected,
        "items": [item.to_dict() for item in first.items],
    }
    check(f"{source_id}: golden výstup", actual, expected)
    check(f"{source_id}: opakovaná extrakce", first.to_dict(), second.to_dict())
    check(f"{source_id}: found = accepted + rejected",
          first.items_found, first.items_valid + first.items_rejected)

# HTML karta nedovoluje bezpečně odvodit obec ani venue z jedné adresní věty.
pardubice_result = pardubice_calendar.extract(Snapshot.from_path(
    HERE / "fixtures" / "pardubice-calendar.html",
    url="https://pardubice.eu/kalendar-akci?page=6"))
check("Pardubice: obec se nedomýšlí", pardubice_result.items[0].municipality, None)
check("Pardubice: místo se nedomýšlí", pardubice_result.items[0].venue, None)
check("UFFO: aliasy pokrývají druh i publikum",
      ical.extract(Snapshot.from_path(
          HERE / "fixtures" / "uffo-trutnov.ics",
          url="https://uffo.cz/data/user-content/calendar/completeCalendar.ics"
      )).items[0].categories,
      ["divadlo", "rodiny"])
check("Kultura HK: plán má tři stránky", len(schema_org.fetch_plan({
    "id": "kultura-hk-official", "url": "https://kultura.hradeckralove.cz/"})), 3)
check("UFFO: plán používá iCal feed", ical.fetch_plan({
    "id": "uffo-trutnov", "url": "https://www.uffo.cz/program/"})[0].url,
    "https://uffo.cz/data/user-content/calendar/completeCalendar.ics")
check("Pardubice: kumulativní plán končí šestou stránkou",
      pardubice_calendar.fetch_plan({
          "id": "pardubice-calendar", "url": "https://pardubice.eu/kalendar-akci"})[0].url,
      "https://pardubice.eu/kalendar-akci?page=6")

print("\nVšechny golden testy adaptérů prošly.")
