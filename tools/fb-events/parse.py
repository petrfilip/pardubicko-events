"""Čisté parsovací funkce pro veřejné výpisy událostí z Facebooku.

Modul záměrně neobsahuje žádnou práci se sítí ani s prohlížečem, aby se dal
testovat bez Playwrightu (`python test_parse.py`).

Formáty, které Facebook v české lokalizaci používá:

    Čt, 13. 8. v 20:30 CEST                     – letošní akce, rok chybí
    So, 7. 9. 2024                              – starší akce, rok uveden
    Sobota 29. srpna 2026 v 14:00 CEST          – detail akce, měsíc slovem
    30. 4. v 18:00 až 1. 5. v 5:00 CEST         – rozsah přes půlnoc
    Pá, 7. 8. – 8. 8.                           – vícedenní bez času

Rok u nadcházejících akcí Facebook vynechává. Domýšlet ho nesmíme náhodně,
proto platí deterministické pravidlo: chybí-li rok, vybere se nejbližší rok,
ve kterém datum ještě nespadá do minulosti (viz `resolve_year`).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Prague")

MONTH_NAMES = {
    "ledna": 1, "leden": 1,
    "února": 2, "unora": 2, "únor": 2,
    "března": 3, "brezna": 3, "březen": 3,
    "dubna": 4, "duben": 4,
    "května": 5, "kvetna": 5, "květen": 5,
    "června": 6, "cervna": 6, "červen": 6,
    "července": 7, "cervence": 7, "červenec": 7,
    "srpna": 8, "srpen": 8,
    "září": 9, "zari": 9,
    "října": 10, "rijna": 10, "říjen": 10,
    "listopadu": 11, "listopad": 11,
    "prosince": 12, "prosinec": 12,
}

WEEKDAY_PREFIX = re.compile(
    r"^\s*(po|út|ut|st|čt|ct|pá|pa|so|ne"
    r"|pondělí|úterý|středa|čtvrtek|pátek|sobota|neděle"
    r"|pondeli|utery|streda|ctvrtek|patek|nedele)\s*,?\s*",
    re.IGNORECASE,
)

TZ_SUFFIX = re.compile(r"\b(CEST|CET|GMT[+-]\d+|UTC[+-]\d+)\b", re.IGNORECASE)
RANGE_SPLIT = re.compile(r"\s+(?:až|do)\s+|\s+[–—-]\s+")

NUMERIC_DATE = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.(?:\s*(\d{4}))?")
NAMED_DATE = re.compile(r"(\d{1,2})\.\s*([a-záčďéěíňóřšťúůýž]+)(?:\s+(\d{4}))?", re.IGNORECASE)
TIME = re.compile(r"(?:\bv\s*)?(\d{1,2}):(\d{2})")

ORGANIZER_PREFIX = re.compile(r"^\s*(Událost vytvořena|Událost pořádá)\s*", re.IGNORECASE)
# U právě běžících akcí Facebook místo data napíše "Právě probíhá". Datum se pak
# musí vzít z detailu; odhadovat ho ze slova „probíhá“ nelze.
ONGOING_MARKER = re.compile(r"^\s*(právě probíhá|probíhá(\s+právě)?)\s*$", re.IGNORECASE)
# "RockIn a TRAKTOR spolu s 4 dalšími" je souhrn, ne jméno pořadatele.
COHOST_SUMMARY = re.compile(r"\s+spolu s\s+\d+\s+dalšími\s*$", re.IGNORECASE)


class ParseError(ValueError):
    """Řetězec se nepodařilo bezpečně naparsovat."""


def resolve_year(day: int, month: int, today: date, prefer: str = "future") -> int:
    """Doplní chybějící rok.

    `prefer="future"` (výpis nadcházejících akcí): vybere nejbližší rok, ve kterém
    datum ještě neleží víc než 2 dny v minulosti. Tolerance 2 dnů pokrývá akce,
    které právě probíhají nebo skončily včera.

    `prefer="past"` (výpis uplynulých akcí): vybere nejbližší rok, ve kterém
    datum neleží v budoucnosti.
    """
    for offset in (0, 1, -1) if prefer == "future" else (0, -1, 1):
        try:
            candidate = date(today.year + offset, month, day)
        except ValueError:
            continue  # 29. 2. v nepřestupném roce
        if prefer == "future" and candidate >= today - timedelta(days=2):
            return candidate.year
        if prefer == "past" and candidate <= today:
            return candidate.year
    return today.year


def _extract_date(part: str, today: date, prefer: str):
    """Vrátí (den, měsíc, rok|None) nebo None, pokud část datum neobsahuje."""
    m = NUMERIC_DATE.search(part)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else None
    else:
        m = NAMED_DATE.search(part)
        if not m:
            return None
        month = MONTH_NAMES.get(m.group(2).lower())
        if month is None:
            return None
        day = int(m.group(1))
        year = int(m.group(3)) if m.group(3) else None
    if not 1 <= day <= 31 or not 1 <= month <= 12:
        raise ParseError(f"nesmyslné datum {day}.{month}. v {part!r}")
    if year is None:
        year = resolve_year(day, month, today, prefer)
    return day, month, year


def _extract_time(part: str):
    m = TIME.search(part)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        raise ParseError(f"nesmyslný čas v {part!r}")
    return hour, minute


def parse_datetime_line(line: str, today: date | None = None, prefer: str = "future") -> dict:
    """Naparsuje řádek s datem a časem.

    Vrací {"start_at", "end_at", "all_day", "date_text"}. `start_at` a `end_at`
    jsou ISO 8601 s offsetem, `end_at` je None, pokud konec není uveden.
    Nic se nedomýšlí: chybí-li čas, je `all_day` True a čas se nedoplňuje.
    """
    if today is None:
        today = datetime.now(TZ).date()
    raw = line.strip()
    cleaned = TZ_SUFFIX.sub("", raw)
    cleaned = WEEKDAY_PREFIX.sub("", cleaned).strip()

    parts = [p.strip() for p in RANGE_SPLIT.split(cleaned) if p.strip()]
    if not parts:
        raise ParseError(f"prázdný řádek: {raw!r}")

    start_date = _extract_date(parts[0], today, prefer)
    if start_date is None:
        raise ParseError(f"nenalezeno datum v {raw!r}")
    day, month, year = start_date
    start_time = _extract_time(parts[0])
    all_day = start_time is None

    start = datetime(year, month, day, *(start_time or (0, 0)), tzinfo=TZ)

    end = None
    if len(parts) > 1:
        tail = parts[1]
        end_date = _extract_date(tail, today, prefer)
        end_time = _extract_time(tail)
        if end_date is None and end_time is None:
            end = None
        else:
            if end_date is None:
                # "30. 4. v 18:00 až 1. 5. v 5:00" má datum, "… až 22:00" nemá
                ed_day, ed_month, ed_year = day, month, year
            else:
                ed_day, ed_month, ed_year = end_date
            if end_time is None:
                end = datetime(ed_year, ed_month, ed_day, 0, 0, tzinfo=TZ)
                all_day = True
            else:
                end = datetime(ed_year, ed_month, ed_day, *end_time, tzinfo=TZ)
            if end < start:
                # rozsah přes půlnoc bez uvedeného data konce
                end += timedelta(days=1)

    return {
        "start_at": start.isoformat(),
        "end_at": end.isoformat() if end else None,
        "all_day": all_day,
        "date_text": raw,
    }


def clean_organizer(line: str) -> str | None:
    """"Událost vytvořena Divadlo 29" -> "Divadlo 29"."""
    if not ORGANIZER_PREFIX.search(line):
        return None
    name = ORGANIZER_PREFIX.sub("", line).strip()
    return name or None


def is_cohost_summary(name: str) -> bool:
    """Pozná souhrnnou formulaci "X a Y spolu s N dalšími"."""
    return bool(COHOST_SUMMARY.search(name))


def strip_cohost_summary(name: str) -> str:
    """Odřízne "spolu s N dalšími"; zbytek nechá beze změny, nerozděluje jména."""
    return COHOST_SUMMARY.sub("", name).strip()


def clean_municipality(line: str) -> str | None:
    """" · Chrudim" -> "Chrudim". Facebook sem dává vlastní geoznačku, která
    nemusí odpovídat skutečné obci konání — ověřuje ji až Curator."""
    text = line.strip().lstrip("·").strip()
    return text or None


def parse_listing_block(lines: list[str], today: date | None = None,
                        prefer: str = "future") -> dict:
    """Naparsuje blok textu jedné položky výpisu událostí.

    Očekávané pořadí (některé řádky mohou chybět):
        Pá, 14. 8. v 17:00 CEST
        Název akce
        Místo konání
         · Obec
        Událost vytvořena Pořadatel
    """
    rows = [ln.strip() for ln in lines if ln and ln.strip()]
    if len(rows) < 2:
        raise ParseError(f"blok má málo řádků: {lines!r}")

    if ONGOING_MARKER.match(rows[0]):
        # Akce právě běží a Facebook u ní datum nezobrazuje. Blok se nezahazuje —
        # jde o platnou akci, jen se termín musí doplnit z detailu.
        when = {"start_at": None, "end_at": None, "all_day": None, "date_text": rows[0],
                "ongoing": True}
    else:
        when = {**parse_datetime_line(rows[0], today=today, prefer=prefer), "ongoing": False}

    title = rows[1]
    venue = None
    municipality = None
    organizers = []

    for row in rows[2:]:
        organizer = clean_organizer(row)
        if organizer:
            organizers.append(organizer)
        elif row.startswith("·"):
            municipality = clean_municipality(row)
        elif venue is None:
            venue = row

    return {
        **when,
        "title": title,
        "venue": venue,
        "municipality": municipality,
        "organizers": organizers,
    }
