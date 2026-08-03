"""Adaptér pro zdroje se `schema.org/Event` — JSON-LD i mikrodata.

První volba podle ADR 0003. Čte obě kódování téhož slovníku, protože
české weby je používají promíchaně a rozdíl je jen v zápisu, ne v datech.

Ověřené zdroje k 2. 8. 2026:

* `kultura-hk-official` — https://kultura.hradeckralove.cz/, mikrodata,
  15 akcí na stránku, stránkování `?paginator-page=N`, celkem 309 akcí.
* `bio-central` — https://www.biocentral.cz/program/, JSON-LD, 39 akcí
  na jedné stránce, bez stránkování.

Co adaptér záměrně nedělá:

* Nedoplňuje obec z názvu místa. `kultura-hk` uvádí `addressLocality`,
  a ta se přebírá; kde chybí, zůstává `municipality` prázdné.
* Nečte kategorie z okolního HTML. `kultura-hk` je má v textu vedle
  bloku mikrodat (`(Hudba, koncert)`), ale mimo `itemprop`. Vytáhnout je
  by znamenalo psát pravidlo na konkrétní šablonu, což do obecného
  adaptéru nepatří — patřilo by to do adaptéru pro ten jeden zdroj.
* Nerozbaluje `eventSchedule` ani opakování. Označí je příznakem
  `recurring` a doslovný zápis nechá v `extra`.

Spuštění (přes společný běhový nástroj):

    python3 tools/pipeline/fetch.py --source kultura-hk-official
    python3 tools/pipeline/fetch.py --source bio-central --offline
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from .base import (
    ExtractResult, RawItem, Request, Snapshot, clean_text, has_entities,
    normalize_categories, parse_iso_datetime,
)
from .htmlutil import (
    find_microdata_scopes,
    iter_jsonld,
    iter_jsonld_objects,
    microdata_item,
    parse_html,
)

name = "schema_org"

# Podtypy `Event` ze slovníku schema.org. Bez výčtu by adaptér přehlédl
# `TheaterEvent` i `ScreeningEvent`, což jsou u divadel a kin běžné typy.
EVENT_TYPES = frozenset({
    "Event", "BusinessEvent", "ChildrensEvent", "ComedyEvent", "CourseInstance",
    "DanceEvent", "DeliveryEvent", "EducationEvent", "EventSeries", "ExhibitionEvent",
    "Festival", "FoodEvent", "Hackathon", "LiteraryEvent", "MusicEvent",
    "PublicationEvent", "SaleEvent", "ScreeningEvent", "SocialEvent", "SportsEvent",
    "TheaterEvent", "VisualArtsEvent",
})

# Stránkování je vlastnost šablony konkrétního webu, ne konfigurace zdroje.
# Do `config/source-registry.json` nepatří (mění ho člověk, ne kód), proto
# je tady. Zdroj, který tu není, se stáhne jednou na svém `url`.
PAGINATION = {
    "kultura-hk-official": {"query": "paginator-page", "pages": 3, "first_page": 1},
}


def fetch_plan(source: dict) -> list[Request]:
    base_url = source.get("url")
    if not base_url:
        raise ValueError(f"Zdroj {source.get('id')!r} nemá url.")
    rule = PAGINATION.get(source.get("id") or "")
    if not rule:
        return [Request(url=base_url, label="page-1", kind="listing")]

    requests = []
    first = rule["first_page"]
    for page in range(first, first + rule["pages"]):
        separator = "&" if "?" in base_url else "?"
        url = base_url if page == first and first == 1 else (
            f"{base_url}{separator}{rule['query']}={page}")
        requests.append(Request(url=url, label=f"page-{page}", kind="listing"))
    return requests


def extract(snapshot: Snapshot) -> ExtractResult:
    root = parse_html(snapshot.text())
    result = ExtractResult()

    jsonld_events = 0
    for payload, raw in iter_jsonld(root):
        if payload is None:
            # Nečitelný blok je potenciálně ztracená akce, ne nic.
            result.items_unparsed += 1
            result.notes.append(
                f"Blok application/ld+json se nepodařilo naparsovat ({len(raw)} znaků).")
            continue
        for obj in iter_jsonld_objects(payload):
            if not _is_event(obj):
                continue
            jsonld_events += 1
            item = _to_item(obj, snapshot, encoding="json-ld")
            if item is None:
                result.items_unparsed += 1
            else:
                result.items.append(item)

    microdata_events = 0
    for scope in find_microdata_scopes(root, set(EVENT_TYPES)):
        microdata_events += 1
        item = _to_item(microdata_item(scope), snapshot, encoding="microdata")
        if item is None:
            result.items_unparsed += 1
        else:
            result.items.append(item)

    if jsonld_events and microdata_events:
        result.notes.append(
            f"Stránka nese obě kódování: {jsonld_events} akcí v JSON-LD a "
            f"{microdata_events} v mikrodatech. Duplicity řeší až deduplikace.")
    if not jsonld_events and not microdata_events:
        result.notes.append(
            "Na stránce není žádný schema.org/Event — ani v JSON-LD, ani v mikrodatech.")
    return result


# ---------------------------------------------------------------------------

def _is_event(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    raw_type = obj.get("@type")
    if isinstance(raw_type, str):
        types = {raw_type}
    elif isinstance(raw_type, list):
        types = {t for t in raw_type if isinstance(t, str)}
    else:
        return False
    return bool({t.rsplit("/", 1)[-1] for t in types} & EVENT_TYPES)


def _first(value: Any) -> Any:
    """Schema.org dovoluje u každé vlastnosti seznam. Bere se první hodnota."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _text_of(value: Any) -> str | None:
    value = _first(value)
    if isinstance(value, dict):
        value = value.get("name") or value.get("@value")
    return clean_text(value)


def _names(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    names = []
    for entry in values:
        text = _text_of(entry)
        if text and text not in names:
            names.append(text)
    return names


def _to_item(obj: dict, snapshot: Snapshot, *, encoding: str) -> RawItem | None:
    title = _text_of(obj.get("name"))
    start_raw = _first(obj.get("startDate"))
    start_at, all_day = parse_iso_datetime(start_raw)
    end_at, _ = parse_iso_datetime(_first(obj.get("endDate")))

    # Blok, ze kterého nejde přečíst ani název, ani termín, není akce.
    # Vydat ho jako položku se samými `null` by jen zašumělo frontu kurátora.
    if not title and not start_at:
        return None

    location = _first(obj.get("location"))
    venue, address, municipality = _read_location(location)

    url = _text_of(obj.get("url")) or _text_of(obj.get("@id"))
    if url:
        url = urljoin(snapshot.url, url)

    description = _text_of(obj.get("description"))
    notes: list[str] = []
    if has_entities(description):
        notes.append("Popis obsahuje HTML entity i po rozbalení; zdroj je escapuje vícenásobně.")

    extra: dict[str, Any] = {"encoding": encoding}
    if start_raw is not None and not isinstance(start_raw, (str, int, float)):
        extra["start_date_raw"] = start_raw
    for key in ("eventStatus", "eventAttendanceMode", "typicalAgeRange", "inLanguage"):
        value = _text_of(obj.get(key))
        if value:
            extra[key] = value

    recurring = None
    if obj.get("eventSchedule") is not None or obj.get("subEvent") is not None:
        recurring = True
        extra["schedule_raw"] = obj.get("eventSchedule") or obj.get("subEvent")
        notes.append("Zdroj uvádí opakování (eventSchedule/subEvent); rozpad na termíny "
                     "adaptér nedělá, posoudit ručně.")

    if start_raw is not None and start_at is None:
        notes.append(f"Termín „{start_raw}“ se nepodařilo přečíst jako ISO 8601.")

    source_categories = _names(obj.get("keywords")) or _names(obj.get("genre"))
    categories, categories_unmapped = normalize_categories(source_categories)
    if categories_unmapped:
        extra["categories_unmapped"] = categories_unmapped
        notes.append(
            "Neznámé zdrojové kategorie nebyly namapovány: "
            + ", ".join(categories_unmapped) + ".")

    return RawItem(
        uid=_text_of(obj.get("@id")) or url,
        title=title,
        date_text=start_raw if isinstance(start_raw, str) else None,
        start_at=start_at,
        end_at=end_at,
        all_day=all_day,
        venue=venue,
        address=address,
        municipality=municipality,
        description=description,
        url=url,
        price_text=_read_price(obj.get("offers")),
        categories=categories,
        organizers=_names(obj.get("organizer")) or _names(obj.get("performer")),
        recurring=recurring,
        extra=extra,
        notes=notes,
    )


def _read_location(location: Any) -> tuple[str | None, str | None, str | None]:
    """Místo, adresa a obec. Obec se bere jen z `addressLocality`."""
    if location is None:
        return None, None, None
    if isinstance(location, str):
        return clean_text(location), None, None
    if not isinstance(location, dict):
        return None, None, None

    venue = _text_of(location.get("name"))
    address_value = _first(location.get("address"))
    if isinstance(address_value, str):
        return venue, clean_text(address_value), None
    if not isinstance(address_value, dict):
        return venue, None, None

    municipality = clean_text(address_value.get("addressLocality"))
    parts = [clean_text(address_value.get(key)) for key in
             ("streetAddress", "postalCode", "addressLocality")]
    address = ", ".join(part for part in parts if part) or None
    return venue, address, municipality


def _read_price(offers: Any) -> str | None:
    """Doslovný zápis ceny. Typ ceny určuje až normalizace, ne adaptér."""
    offer = _first(offers)
    if offer is None:
        return None
    if isinstance(offer, str):
        return clean_text(offer)
    if not isinstance(offer, dict):
        return None
    price = offer.get("price")
    currency = clean_text(offer.get("priceCurrency"))
    if price is None or price == "":
        return clean_text(offer.get("description"))
    price_text = clean_text(price)
    return f"{price_text} {currency}".strip() if currency else price_text
