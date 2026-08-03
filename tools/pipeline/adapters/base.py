"""Kontrakt adaptéru podle oddílu 3.3 architektury fáze 2.

Modul obsahuje jen datové typy a pomocné funkce. Nesahá na síť ani na
databázi, aby se dal importovat z testů bez jakékoli přípravy prostředí.

Rozhraní, které musí splnit každý adaptér:

    fetch_plan(source) -> list[Request]
    extract(snapshot)  -> ExtractResult(items, items_unparsed, notes)

Pravidla, která platí pro všechny adaptéry bez výjimky (ADR 0003):

* Adaptér **nevyhodnocuje a nedomýšlí.** Co ve zdroji není, zůstává `None`.
  Nikdy se nedopočítává z jiné položky, z loňska ani z podobné akce.
* Co adaptér rozpozná jako blok akce, ale nedokáže z něj nic přečíst,
  se počítá do `items_unparsed`. To je metrika tiché ztráty; její potřebu
  doložil facebookový kanál (`facebook_blocks_unparsed` v docs/monitoring.md).
* Doslovný text zdroje se zachovává v `date_text` a v `extra`, aby měla
  normalizační vrstva z čeho vycházet, když strojový údaj chybí.

Jediná úprava textu, kterou si adaptéry dovolují, je rozbalení HTML entit
(`html.unescape`). Opakuje se nejvýše třikrát, protože české redakční
systémy běžně ukládají popisy dvojitě escapované (`&amp;iacute;`). Není to
odhad obsahu, jen dekódování zápisu; když entity zůstanou i po třetím kole,
zapíše se poznámka a text se ponechá tak, jak je.
"""

from __future__ import annotations

import gzip
import html
import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Prague")

# Klíčová pole pro `source_extract.fill_rates`. `schema-drift` se podle
# oddílu 5 architektury pozná právě na těchto třech.
KEY_FIELDS = ("title", "start_at", "url")
FILL_RATE_FIELDS = KEY_FIELDS + ("end_at", "venue", "municipality", "description")

_ENTITY_RE = re.compile(r"&(?:#\d+|#[xX][0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});")
_WS_RE = re.compile(r"\s+")
_CHARSET_RE = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_\-]+)""", re.I)


class ExtractError(Exception):
    """Adaptér nedokázal snapshot zpracovat jako celek."""


# ---------------------------------------------------------------------------
# Požadavek a snapshot
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Request:
    """Jeden HTTP dotaz z plánu adaptéru.

    `label` popisuje roli dotazu (stránka výpisu, den kalendáře, feed) a
    dostane se až do snapshotu. Bez něj by u vícedotazových plánů nešlo
    poznat, ke které části zdroje snapshot patří.
    """

    url: str
    label: str | None = None
    kind: str = "listing"          # listing|detail|feed
    accept: str | None = None


@dataclass
class Snapshot:
    """Uložené tělo odpovědi plus to, co je potřeba k jeho přečtení.

    Adaptér dostává vždy tenhle objekt, nikdy živou odpověď. Díky tomu jde
    extrakci pouštět opakovaně nad archivem bez zatěžování cizího webu.
    """

    url: str
    body: bytes
    source_id: str | None = None
    fetched_at: str | None = None
    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    content_hash: str | None = None
    request_label: str | None = None

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return None

    def charset(self) -> str:
        """Kódování podle hlavičky, jinak podle `<meta charset>`, jinak UTF-8."""
        ctype = self.header("Content-Type") or ""
        match = re.search(r"charset\s*=\s*\"?([a-zA-Z0-9_\-]+)", ctype)
        if match:
            return match.group(1)
        match_bytes = _CHARSET_RE.search(self.body[:4096])
        if match_bytes:
            return match_bytes.group(1).decode("ascii", "replace")
        return "utf-8"

    def text(self) -> str:
        """Tělo jako text. Neznámé bajty se nahrazují, nikdy nespadne."""
        try:
            return self.body.decode(self.charset(), "replace")
        except LookupError:
            return self.body.decode("utf-8", "replace")

    @classmethod
    def from_path(cls, path: str | Path, **kwargs: Any) -> "Snapshot":
        """Načte snapshot ze souboru; `.gz` rozbalí. Používá se pro fixtures."""
        target = Path(path)
        raw = target.read_bytes()
        if target.suffix == ".gz":
            raw = gzip.decompress(raw)
        kwargs.setdefault("url", target.resolve().as_uri())
        return cls(body=raw, **kwargs)


# ---------------------------------------------------------------------------
# Výstup extrakce
# ---------------------------------------------------------------------------

ITEM_FIELDS = (
    "uid", "title", "date_text", "start_at", "end_at", "all_day",
    "venue", "address", "municipality", "description", "url",
    "price_text", "categories", "organizers", "recurring", "extra", "notes",
)


@dataclass
class RawItem:
    """Syrová položka ze zdroje. Všechna nepřečtená pole zůstávají `None`.

    Prázdný seznam u `categories`, `organizers` a `notes` znamená „zdroj nic
    neuvedl“, což je u výčtů totéž jako `None` u skalárních polí.
    """

    uid: str | None = None
    title: str | None = None
    date_text: str | None = None       # doslovný zápis termínu ze zdroje
    start_at: str | None = None        # ISO 8601 s offsetem, jinak None
    end_at: str | None = None
    all_day: bool | None = None
    venue: str | None = None
    address: str | None = None
    municipality: str | None = None
    description: str | None = None
    url: str | None = None
    price_text: str | None = None
    categories: list[str] = field(default_factory=list)
    organizers: list[str] = field(default_factory=list)
    recurring: bool | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Minimum, aby šlo o akci: název a strojově čitelný začátek."""
        return bool(self.title) and bool(self.start_at)

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in ITEM_FIELDS}


@dataclass
class ExtractResult:
    items: list[RawItem] = field(default_factory=list)
    items_unparsed: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def items_found(self) -> int:
        """Kolik bloků adaptér ve zdroji rozpoznal, včetně nečitelných."""
        return len(self.items) + self.items_unparsed

    @property
    def items_valid(self) -> int:
        return sum(1 for item in self.items if item.is_valid)

    @property
    def items_rejected(self) -> int:
        """Rozpoznané bloky, které nesplňují minimum pro přijetí.

        Zahrnuje jak úplně nečitelné bloky (`items_unparsed`), tak položky,
        ze kterých adaptér něco přečetl, ale chybí jim název nebo začátek.
        Díky tomu vždy platí `found = valid + rejected`.
        """
        return self.items_found - self.items_valid

    @property
    def category_values_rejected(self) -> int:
        """Počet zdrojových kategorií, které řízený slovník nezná."""
        return sum(
            len(item.extra.get("categories_unmapped", []))
            for item in self.items
        )

    def fill_rates(self, fields: tuple[str, ...] = FILL_RATE_FIELDS) -> dict[str, float]:
        """Podíl položek, které dané pole mají. Podklad pro `schema-drift`."""
        if not self.items:
            return {name: 0.0 for name in fields}
        rates = {}
        for name in fields:
            filled = sum(1 for item in self.items if _is_filled(getattr(item, name, None)))
            rates[name] = round(filled / len(self.items), 4)
        return rates

    def merge(self, other: "ExtractResult") -> "ExtractResult":
        """Sloučí výsledky z více snapshotů téhož zdroje (stránkovaný výpis)."""
        self.items.extend(other.items)
        self.items_unparsed += other.items_unparsed
        for note in other.notes:
            if note not in self.notes:
                self.notes.append(note)
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "items_found": self.items_found,
            "items_valid": self.items_valid,
            "items_rejected": self.items_rejected,
            "items_unparsed": self.items_unparsed,
            "category_values_rejected": self.category_values_rejected,
            "fill_rates": self.fill_rates(),
            "notes": list(self.notes),
            "items": [item.to_dict() for item in self.items],
        }


def _is_filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, dict, str)):
        return len(value) > 0
    return True


class Adapter(Protocol):
    """Rozhraní, které musí splnit každý adaptér."""

    name: str

    def fetch_plan(self, source: dict) -> list[Request]: ...

    def extract(self, snapshot: Snapshot) -> ExtractResult: ...


# ---------------------------------------------------------------------------
# Sdílené pomůcky pro adaptéry
# ---------------------------------------------------------------------------

_CATEGORY_SEPARATOR_RE = re.compile(r"[\s_]+")
_CATEGORY_HYPHENS_RE = re.compile(r"-{2,}")


def normalize_category_key(value: Any) -> str:
    """Normalizace shodná s pravidlem v `config/categories.json`."""
    text = unicodedata.normalize("NFD", str(value or "").strip().lower())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = _CATEGORY_SEPARATOR_RE.sub("-", text)
    return _CATEGORY_HYPHENS_RE.sub("-", text).strip("-")


@lru_cache(maxsize=4)
def _category_lookup(config_path: str) -> dict[str, str]:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    lookup = {
        normalize_category_key(category["id"]): category["id"]
        for category in config.get("categories", [])
    }
    for entry in config.get("aliases", []):
        lookup[normalize_category_key(entry["alias"])] = entry["category_id"]
    return lookup


def normalize_categories(
    values: list[str], config_path: str | Path | None = None,
) -> tuple[list[str], list[str]]:
    """Převede známé hodnoty na kanonická ID; neznámé vrátí zvlášť.

    Pořadí prvního výskytu zůstává zachované. Neznámou hodnotu funkce
    neodhaduje ani nevydává jako kategorii publikovatelnou dál.
    """
    path = Path(config_path) if config_path else (
        Path(__file__).resolve().parents[3] / "config" / "categories.json")
    lookup = _category_lookup(str(path.resolve()))
    canonical: list[str] = []
    rejected: list[str] = []
    for raw in values:
        cleaned = clean_text(raw)
        if not cleaned:
            continue
        category_id = lookup.get(normalize_category_key(cleaned))
        if category_id:
            if category_id not in canonical:
                canonical.append(category_id)
        elif cleaned not in rejected:
            rejected.append(cleaned)
    return canonical, rejected

def clean_text(value: Any, *, max_entity_rounds: int = 3) -> str | None:
    """Sjednotí bílé znaky a rozbalí HTML entity. Prázdný výsledek je `None`.

    Entity se rozbalují opakovaně, protože české redakční systémy popisy
    běžně ukládají dvojitě escapované. Po `max_entity_rounds` kolech se
    přestává; zbylé entity pozná volající přes `has_entities()`.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    for _ in range(max_entity_rounds):
        unescaped = html.unescape(value)
        if unescaped == value:
            break
        value = unescaped
    value = value.replace(" ", " ").replace("​", "")
    value = _WS_RE.sub(" ", value).strip()
    return value or None


def has_entities(value: str | None) -> bool:
    return bool(value) and bool(_ENTITY_RE.search(value))


def parse_iso_datetime(value: Any) -> tuple[str | None, bool | None]:
    """Přečte datum ve tvaru podle ISO 8601 (schema.org, iCal, atribut `datetime`).

    Vrací dvojici `(iso_s_offsetem, all_day)`. Nerozpoznaný vstup vrací
    `(None, None)` — nikdy odhad. Chybí-li v zápisu časové pásmo, doplní se
    Europe/Prague; jiná zóna u českého zdroje by byl výmysl a stejnou
    konvenci používá už `tools/fb-events/parse.py`.
    """
    if not isinstance(value, str):
        return None, None
    text = value.strip()
    if not text:
        return None, None

    # Samotné datum bez času: schema.org i iCal tím míní celý den.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            day = date.fromisoformat(text)
        except ValueError:
            return None, None
        return datetime.combine(day, time(0, 0), TZ).isoformat(), True

    normalized = text.replace(" ", "T", 1) if re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?", text) else text
    normalized = re.sub(r"([Zz])$", "+00:00", normalized)
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None, None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    else:
        parsed = parsed.astimezone(TZ)
    return parsed.isoformat(), False


def record_extract(connection: sqlite3.Connection, fetch_id: int,
                   result: ExtractResult, *, autocommit: bool = True) -> None:
    """Zapíše výsledek extrakce do `source_extract`.

    Klíčem je `fetch_id`, takže opakovaná extrakce nad týmž stažením
    přepíše předchozí řádek místo toho, aby vyrobila druhý.
    """
    connection.execute(
        "INSERT INTO source_extract (fetch_id, items_found, items_valid, "
        "items_unparsed, fill_rates) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(fetch_id) DO UPDATE SET "
        "items_found = excluded.items_found, items_valid = excluded.items_valid, "
        "items_unparsed = excluded.items_unparsed, fill_rates = excluded.fill_rates",
        (fetch_id, result.items_found, result.items_valid, result.items_unparsed,
         json.dumps(result.fill_rates(), ensure_ascii=False, sort_keys=True)),
    )
    if autocommit:
        connection.commit()
