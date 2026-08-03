"""Adaptér pro iCalendar (RFC 5545) — druhá volba podle ADR 0003.

Ověřený zdroj k 2. 8. 2026: `uffo-trutnov`. Společenské centrum Trutnov
vystavuje na svém programu odkaz „Stáhni si program do kalendáře“, který
míří na `/uffoCompleteCalendar.ashx/?c=apple`. Ten odpovídá 302 na
`webcal://uffo.cz/data/user-content/calendar/completeCalendar.ics`;
schéma `webcal://` je totéž jako `https://` a stahuje se přes ně 70 akcí.

Poznámka k RSS: v `config/source-registry.json` publikuje RSS devět zdrojů
(Chrudim, Trutnov, Jičín, Dvůr Králové, Vysoké Mýto, Divadlo Drak a další),
ale u všech jde o kanál aktuálit, ne akcí — položky nemají termín a mísí
se v nich úřední deska s programem. Čtení RSS proto v adaptéru není:
napsat ho bez zdroje, nad kterým by šlo ověřit výstup, by znamenalo vydat
za funkční kód, který nikdo nezkoušel. Až se najde zdroj s RSS akcí,
přidá se sem druhá větev.

Co adaptér nedělá:

* `RRULE` nerozbaluje na jednotlivé termíny. Uloží ho doslovně do
  `extra.rrule`, nastaví `recurring` a nechá rozhodnutí na kurátorovi —
  stejně jako `tools/fb-events` u opakovaných akcí na Facebooku.
* Nedoplňuje obec. `LOCATION` v UFFO obsahuje jen „UFFO“; odvodit z toho
  Trutnov je úsudek, ne čtení.

Spuštění:

    python3 tools/pipeline/fetch.py --source uffo-trutnov
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .base import TZ, ExtractResult, RawItem, Request, Snapshot, clean_text, normalize_categories

name = "ical"

# Adresa feedu není v registru — registr drží lidskou stránku zdroje.
# Zdroj bez záznamu se zkusí stáhnout přímo ze svého `url`.
FEED_URLS = {
    "uffo-trutnov": "https://uffo.cz/data/user-content/calendar/completeCalendar.ics",
}

# "DTSTART;TZID=Europe/Prague:20261120T201500" → jméno, parametry, hodnota.
_PROPERTY_RE = re.compile(r"^(?P<name>[A-Za-z0-9\-]+)(?P<params>(?:;[^:]*)?):(?P<value>.*)$")
_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_DATETIME_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)$")


def fetch_plan(source: dict) -> list[Request]:
    url = FEED_URLS.get(source.get("id") or "") or source.get("url")
    if not url:
        raise ValueError(f"Zdroj {source.get('id')!r} nemá url ani zapsaný feed.")
    return [Request(url=url, label="calendar", kind="feed", accept="text/calendar, */*;q=0.5")]


def extract(snapshot: Snapshot) -> ExtractResult:
    text = snapshot.text()
    result = ExtractResult()

    if "BEGIN:VCALENDAR" not in text:
        result.notes.append(
            "Odpověď neobsahuje BEGIN:VCALENDAR — zdroj nevrátil iCalendar.")
        return result

    blocks = _vevent_blocks(_unfold(text))
    for block in blocks:
        item = _to_item(block, snapshot)
        if item is None:
            result.items_unparsed += 1
        else:
            result.items.append(item)

    if result.items_unparsed:
        result.notes.append(
            f"{result.items_unparsed} bloků VEVENT nemá SUMMARY ani DTSTART — "
            "potenciálně ztracené akce, prověřit formát feedu.")
    if not blocks:
        result.notes.append("Kalendář neobsahuje žádný VEVENT.")
    return result


# ---------------------------------------------------------------------------

def _unfold(text: str) -> list[str]:
    """Spojí pokračovací řádky (RFC 5545, 3.1). Bez toho se dlouhý SUMMARY zlomí."""
    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line[:1] in (" ", "\t") and lines:
            lines[-1] += raw_line[1:]
        else:
            lines.append(raw_line)
    return lines


def _vevent_blocks(lines: list[str]) -> list[list[tuple[str, dict[str, str], str]]]:
    blocks: list[list[tuple[str, dict[str, str], str]]] = []
    current: list[tuple[str, dict[str, str], str]] | None = None
    for line in lines:
        stripped = line.strip()
        if stripped.upper() == "BEGIN:VEVENT":
            current = []
            continue
        if stripped.upper() == "END:VEVENT":
            if current is not None:
                blocks.append(current)
            current = None
            continue
        if current is None or not stripped:
            continue
        parsed = _parse_property(line)
        if parsed:
            current.append(parsed)
    if current is not None:
        # Neuzavřený blok na konci souboru: feed je useknutý. Blok se
        # zahodit nesmí, jinak by se ztráta nikde neprojevila.
        blocks.append(current)
    return blocks


def _parse_property(line: str) -> tuple[str, dict[str, str], str] | None:
    match = _PROPERTY_RE.match(line)
    if not match:
        return None
    params: dict[str, str] = {}
    for chunk in (match.group("params") or "").split(";"):
        if "=" in chunk:
            key, _, value = chunk.partition("=")
            params[key.strip().upper()] = value.strip().strip('"')
    return match.group("name").upper(), params, _unescape(match.group("value"))


def _unescape(value: str) -> str:
    """Rozbalí escapování textových hodnot podle RFC 5545, 3.3.11."""
    out: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            nxt = value[index + 1]
            out.append({"n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"}.get(nxt, nxt))
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _parse_moment(value: str, params: dict[str, str]) -> tuple[str | None, bool | None]:
    """DTSTART/DTEND → ISO 8601 v pražském čase. Nerozpoznaný tvar vrací None."""
    value = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or _DATE_RE.match(value):
        match = _DATE_RE.match(value)
        if not match:
            return None, None
        try:
            day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None, None
        return datetime.combine(day, time(0, 0), TZ).isoformat(), True

    match = _DATETIME_RE.match(value)
    if not match:
        return None, None
    year, month, day, hour, minute, second, zulu = match.groups()
    tzinfo = TZ
    if zulu == "Z":
        tzinfo = ZoneInfo("UTC")
    elif params.get("TZID"):
        try:
            tzinfo = ZoneInfo(params["TZID"])
        except (ZoneInfoNotFoundError, ValueError):
            # Neznámá zóna: použije se pražská, ale je to vidět v poznámce.
            tzinfo = TZ
    try:
        moment = datetime(int(year), int(month), int(day),
                          int(hour), int(minute), int(second), tzinfo=tzinfo)
    except ValueError:
        return None, None
    return moment.astimezone(TZ).isoformat(), False


def _to_item(block: list[tuple[str, dict[str, str], str]], snapshot: Snapshot) -> RawItem | None:
    props: dict[str, tuple[dict[str, str], str]] = {}
    categories: list[str] = []
    for prop_name, params, value in block:
        if prop_name == "CATEGORIES":
            categories.extend(part.strip() for part in value.split(",") if part.strip())
        elif prop_name not in props:
            props[prop_name] = (params, value)

    title = clean_text(props.get("SUMMARY", ({}, ""))[1])
    start_params, start_raw = props.get("DTSTART", ({}, ""))
    start_at, all_day = _parse_moment(start_raw, start_params) if start_raw else (None, None)
    if not title and not start_at:
        return None

    end_params, end_raw = props.get("DTEND", ({}, ""))
    end_at, _ = _parse_moment(end_raw, end_params) if end_raw else (None, None)

    notes: list[str] = []
    if start_raw and start_at is None:
        notes.append(f"DTSTART „{start_raw}“ se nepodařilo přečíst.")
    tzid = start_params.get("TZID")
    if tzid:
        try:
            ZoneInfo(tzid)
        except (ZoneInfoNotFoundError, ValueError):
            notes.append(f"Neznámé časové pásmo TZID={tzid}; čteno jako Europe/Prague.")

    extra: dict[str, Any] = {"encoding": "ical"}
    recurring = None
    if "RRULE" in props:
        recurring = True
        extra["rrule"] = props["RRULE"][1]
        notes.append("Akce má RRULE; adaptér ji na jednotlivé termíny nerozpadá.")
    for prop_name in ("STATUS", "SEQUENCE", "CLASS"):
        if prop_name in props:
            extra[prop_name.lower()] = props[prop_name][1]

    canonical_categories, categories_unmapped = normalize_categories(categories)
    if categories_unmapped:
        extra["categories_unmapped"] = categories_unmapped
        notes.append(
            "Neznámé zdrojové kategorie nebyly namapovány: "
            + ", ".join(categories_unmapped) + ".")

    url = clean_text(props.get("URL", ({}, ""))[1])
    return RawItem(
        uid=clean_text(props.get("UID", ({}, ""))[1]),
        title=title,
        date_text=start_raw or None,
        start_at=start_at,
        end_at=end_at,
        all_day=all_day,
        venue=clean_text(props.get("LOCATION", ({}, ""))[1]),
        address=None,
        municipality=None,          # LOCATION nese název sálu, ne obec
        description=clean_text(props.get("DESCRIPTION", ({}, ""))[1]),
        url=url or snapshot.url,
        price_text=None,
        categories=canonical_categories,
        organizers=[o] if (o := clean_text(props.get("ORGANIZER", ({}, ""))[1])) else [],
        recurring=recurring,
        extra=extra,
        notes=notes,
    )
