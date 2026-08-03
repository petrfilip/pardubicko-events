#!/usr/bin/env python3
"""Číselník obcí Pardubického a Královéhradeckého kraje z otevřených dat ČSÚ.

Nástroj stáhne veřejný soubor „Struktura území ČR – otevřená data“, vybere
z něj obce obou sledovaných krajů a zapíše `config/municipalities.json`.
Seznam obcí se nikdy nepíše ručně a nikdy se nedoplňuje z paměti: ručně
sestavený číselník je nedetekovatelně nesprávný a měření pokrytí by na něm
stálo naslepo.

Spuštění:

    python3 tools/pipeline/municipalities.py                  # stáhne a přepíše číselník
    python3 tools/pipeline/municipalities.py --dry-run         # jen souhrn, nic nezapíše
    python3 tools/pipeline/municipalities.py --csv soubor.csv  # z dříve staženého souboru
    python3 tools/pipeline/municipalities.py --csv-url URL     # jiná verze zdrojového souboru

Pravidla provozu, která jsou v kódu záměrně natvrdo:

* představuje se vlastním user-agentem, nepodvrhává prohlížeč,
* před stažením se ptá na `robots.txt` daného hostitele,
* adresu CSV si najde na rozcestníku ČSÚ, aby změna verze souboru
  nevyžadovala zásah do kódu,
* neznámý typ obce nebo neznámý okres je chyba, ne tichá domněnka,
* podezřele malý výsledek nezapíše.
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.request
import urllib.robotparser
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
USER_AGENT = "PardubickoEventsBot/0.1 (+https://github.com/petrfilip/pardubicko-events)"
TZ = ZoneInfo("Europe/Prague")

# Rozcestník ČSÚ „Základní územní číselníky na území ČR a klasifikace CZ-NUTS“.
# Odkaz na CSV na něm nese číslo verze, proto se hledá až v HTML stránky.
LANDING_URL = (
    "https://csu.gov.cz/i_zakladni_uzemni_ciselniky_na_uzemi_cr_a_klasifikace_cz_nuts"
)
CSV_LINK_RE = re.compile(r'href="([^"]*struktura_uzemi_cr\.csv[^"]*)"')

SOURCE_NAME = "Struktura území ČR – otevřená data"
SOURCE_PUBLISHER = "Český statistický úřad"
SOURCE_CODELIST = (
    "ČSÚ číselník 43 (CISOB); kód obce je shodný s kódem obce v RÚIAN"
)

OUTPUT_PATH = REPO_ROOT / "config" / "municipalities.json"
REGIONS_PATH = REPO_ROOT / "config" / "regions.json"
DISTRICTS_PATH = REPO_ROOT / "config" / "districts.json"

# Hodnoty sloupce obec_typ ve zdroji -> hodnoty v číselníku. Neuvedený typ
# nástroj nedomýšlí, ale zastaví se s chybou.
TYPES = {
    "Obec": "obec",
    "Městys": "mestys",
    "Město": "mesto",
    "Statutární město": "statutarni-mesto",
    "Hlavní město": "hlavni-mesto",
    "Vojenský újezd": "vojensky-ujezd",
}

# Pojistka proti zkrácenému nebo prázdnému stažení. Oba kraje mají dohromady
# přibližně 900 obcí; výrazně nižší číslo znamená vadný vstup, ne úbytek obcí.
MIN_EXPECTED = 800


class SourceError(RuntimeError):
    """Vstupní data nejsou použitelná. Nikdy se nenahrazují odhadem."""


def log(message: str = "") -> None:
    print(message, file=sys.stderr)


# --------------------------------------------------------------------------
# Síť
# --------------------------------------------------------------------------


_ROBOTS: dict[str, urllib.robotparser.RobotFileParser] = {}


def robots_allows(url: str) -> bool:
    """Zeptá se na robots.txt hostitele. Nedostupný robots.txt bere jako povolení."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    parser = _ROBOTS.get(origin)
    if parser is None:
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(origin + "/robots.txt")
        try:
            request = urllib.request.Request(
                origin + "/robots.txt", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=30) as response:
                parser.parse(response.read().decode("utf-8", "replace").splitlines())
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                parser.disallow_all = True
            else:
                parser.allow_all = True
        except OSError as error:
            log(f"robots.txt na {origin} se nepodařilo načíst ({error}); beru jako povolený.")
            parser.allow_all = True
        _ROBOTS[origin] = parser
    return parser.can_fetch(USER_AGENT, url)


def fetch(url: str) -> bytes:
    if not robots_allows(url):
        raise SourceError(
            f"robots.txt zakazuje stažení {url}. Zdroj se neobchází; "
            "najdi jiné veřejné umístění téhož číselníku."
        )
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    log(f"GET {url}")
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def discover_csv_url(landing_url: str = LANDING_URL) -> str:
    """Najde na rozcestníku ČSÚ odkaz na CSV se strukturou území."""
    page = fetch(landing_url).decode("utf-8", "replace")
    match = CSV_LINK_RE.search(page)
    if not match:
        raise SourceError(
            f"Na {landing_url} není odkaz na struktura_uzemi_cr.csv. "
            "Stránka se pravděpodobně změnila; adresu lze zadat přes --csv-url."
        )
    return urljoin(landing_url, html.unescape(match.group(1)))


# --------------------------------------------------------------------------
# Zpracování číselníku
# --------------------------------------------------------------------------


def load_geography() -> tuple[dict[str, str], dict[str, list[str]]]:
    """Z config/regions.json vrátí mapu název kraje -> id a jeho okresy."""
    regions = json.loads(REGIONS_PATH.read_text(encoding="utf-8"))["regions"]
    known_districts = {
        item["name"] for item in
        json.loads(DISTRICTS_PATH.read_text(encoding="utf-8"))["districts"]
    }

    region_ids = {region["name"]: region["id"] for region in regions}
    expected = {region["name"]: sorted(region["districts"]) for region in regions}

    listed = {name for names in expected.values() for name in names}
    if listed != known_districts:
        raise SourceError(
            "config/regions.json a config/districts.json se neshodují v okresech: "
            f"{sorted(listed ^ known_districts)}"
        )
    return region_ids, expected


def parse_csv(payload: bytes) -> list[dict[str, str]]:
    text = payload.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text, newline="")))
    if not rows:
        raise SourceError("Stažený CSV soubor neobsahuje žádný řádek.")
    required = {"platnost_datum", "obec_text", "obec_kod", "obec_typ",
                "okres_text", "kraj_text"}
    missing = required - set(rows[0])
    if missing:
        raise SourceError(f"Ve zdrojovém CSV chybí sloupce: {sorted(missing)}")
    return rows


def _fold(text: str) -> str:
    """Řadicí klíč bez diakritiky. Blíž české abecedě než pořadí kódů znaků."""
    return unicodedata.normalize("NFKD", text.casefold()).encode("ascii", "ignore").decode()


def build_municipalities(rows: list[dict[str, str]]) -> tuple[list[dict], str]:
    region_ids, expected_districts = load_geography()
    wanted = set(region_ids)

    selected = [row for row in rows if row["kraj_text"] in wanted]
    if not selected:
        raise SourceError(
            f"Ve zdroji není žádná obec krajů {sorted(wanted)}. "
            "Zkontroluj, zda soubor obsahuje sloupec kraj_text s názvy krajů."
        )

    validity = {row["platnost_datum"] for row in selected}
    if len(validity) != 1:
        raise SourceError(
            f"Zdroj míchá více dat platnosti: {sorted(validity)}. "
            "Číselník musí být k jednomu datu."
        )
    valid_from = validity.pop()

    municipalities: list[dict] = []
    seen: set[int] = set()
    found_districts: dict[str, set[str]] = {name: set() for name in wanted}

    for row in selected:
        obec_typ = row["obec_typ"]
        if obec_typ not in TYPES:
            raise SourceError(
                f"Neznámý typ obce {obec_typ!r} u {row['obec_text']!r}. "
                "Doplň mapování do TYPES; typ se nedohaduje."
            )
        if not row["obec_kod"].isdigit():
            raise SourceError(
                f"Kód obce {row['obec_kod']!r} u {row['obec_text']!r} není číslo.")

        code = int(row["obec_kod"])
        if code in seen:
            raise SourceError(f"Kód obce {code} je ve zdroji dvakrát.")
        seen.add(code)

        found_districts[row["kraj_text"]].add(row["okres_text"])
        municipalities.append({
            "code": code,
            "name": row["obec_text"],
            "type": TYPES[obec_typ],
            "district": row["okres_text"],
            "region": region_ids[row["kraj_text"]],
        })

    for region_name, districts in found_districts.items():
        if sorted(districts) != expected_districts[region_name]:
            raise SourceError(
                f"Okresy kraje {region_name} ve zdroji ({sorted(districts)}) "
                f"neodpovídají config/regions.json ({expected_districts[region_name]}). "
                "Buď se změnilo územní členění, nebo je konfigurace zastaralá."
            )

    if len(municipalities) < MIN_EXPECTED:
        raise SourceError(
            f"Zdroj vydal jen {len(municipalities)} obcí, očekáváno nejméně "
            f"{MIN_EXPECTED}. Stažení bylo pravděpodobně neúplné; nic se nezapisuje."
        )

    municipalities.sort(key=lambda item: (
        item["region"], _fold(item["district"]), _fold(item["name"]), item["code"]))
    return municipalities, valid_from


def build_payload(municipalities: list[dict], valid_from: str, csv_url: str,
                  landing_url: str, now: datetime) -> dict:
    counts = {"total": len(municipalities)}
    for item in municipalities:
        counts[item["region"]] = counts.get(item["region"], 0) + 1

    return {
        "schema_version": 1,
        "updated_at": now.isoformat(timespec="seconds"),
        "generator": "tools/pipeline/municipalities.py",
        "source": {
            "name": SOURCE_NAME,
            "publisher": SOURCE_PUBLISHER,
            "codelist": SOURCE_CODELIST,
            "landing_page": landing_url,
            "url": csv_url,
            "downloaded_at": now.date().isoformat(),
            "valid_from": valid_from,
        },
        "counts": counts,
        "municipalities": municipalities,
    }


# --------------------------------------------------------------------------
# Zápis ve stylu ostatních konfiguračních souborů
# --------------------------------------------------------------------------


def render(payload: dict) -> str:
    """Obálka po řádcích, jednotlivé obce kompaktně — styl config/source-registry.json."""
    lines = ["{"]
    head = [(key, value) for key, value in payload.items() if key != "municipalities"]

    for key, value in head:
        block = json.dumps(value, ensure_ascii=False, indent=2)
        block = "\n".join(
            line if index == 0 else "  " + line
            for index, line in enumerate(block.splitlines())
        )
        lines.append(f'  "{key}": {block},')

    lines.append('  "municipalities": [')
    rows = [
        "    " + json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        for item in payload["municipalities"]
    ]
    lines.append(",\n".join(rows))
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", type=Path,
                        help="už stažený CSV soubor; vyžaduje --csv-url s adresou, "
                             "odkud pochází. Nástroj pak nesahá na síť.")
    parser.add_argument("--csv-url", help="přímá adresa CSV místo hledání na rozcestníku")
    parser.add_argument("--landing-url", default=LANDING_URL,
                        help="rozcestník ČSÚ, na kterém se hledá odkaz na CSV")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH,
                        help="cílový soubor číselníku")
    parser.add_argument("--dry-run", action="store_true", help="nic nezapisovat, jen vypsat")
    args = parser.parse_args()

    # Bez adresy by výsledek nesl jen cestu v cizím počítači, tedy žádný doklad
    # o původu. Číselník bez doložitelného zdroje je k ničemu.
    if args.csv and not args.csv_url:
        parser.error("--csv vyžaduje --csv-url s veřejnou adresou, odkud soubor pochází")

    try:
        if args.csv:
            csv_url = args.csv_url
            payload_bytes = args.csv.read_bytes()
            log(f"Čtu {args.csv}")
        else:
            csv_url = args.csv_url or discover_csv_url(args.landing_url)
            payload_bytes = fetch(csv_url)

        rows = parse_csv(payload_bytes)
        municipalities, valid_from = build_municipalities(rows)
    except SourceError as error:
        log(f"CHYBA: {error}")
        return 1
    except OSError as error:
        log(f"CHYBA: zdroj se nepodařilo načíst: {error}")
        return 1

    payload = build_payload(
        municipalities, valid_from, csv_url, args.landing_url, datetime.now(TZ))
    text = render(payload)

    log("")
    log(f"Obcí celkem: {payload['counts']['total']}   platnost od: {valid_from}")
    for key, value in sorted(payload["counts"].items()):
        if key != "total":
            log(f"  {key}: {value}")

    if args.dry_run:
        log("--dry-run: nic se nezapisuje.")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    log(f"Zapsáno: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
