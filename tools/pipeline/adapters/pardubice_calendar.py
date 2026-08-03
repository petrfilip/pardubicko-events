"""Adaptér městského kalendáře Pardubice.eu bez strukturovaných dat.

Zdroj `pardubice-calendar` je v registru `high` s denním intervalem. Karta
akce nemá JSON-LD ani mikrodata; používá ale stabilní HTML strukturu
`article.event-Card`. Adaptér čte pouze hodnoty, které jsou přímo v kartě.
Obec ani místo z volně psané adresy neodvozuje.

Kalendář stránkuje po dvanácti kartách a `?page=N` vrací kumulativně prvních
N stránek. Jeden požadavek na šestou stránku tedy pokryje nejbližších 72
položek bez duplicit, které by vznikly slučováním stránek 1 až 6.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from .base import (
    ExtractResult, RawItem, Request, Snapshot, clean_text,
    normalize_categories, parse_iso_datetime,
)
from .htmlutil import Node, parse_html

name = "pardubice_calendar"

PAGES = 6


def fetch_plan(source: dict) -> list[Request]:
    base_url = source.get("url")
    if not base_url:
        raise ValueError(f"Zdroj {source.get('id')!r} nemá url.")
    parts = urlsplit(base_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["page"] = str(PAGES)
    url = urlunsplit((parts.scheme, parts.netloc, parts.path,
                      urlencode(query), parts.fragment))
    return [Request(url=url, label=f"pages-1-{PAGES}", kind="listing")]


def extract(snapshot: Snapshot) -> ExtractResult:
    root = parse_html(snapshot.text())
    result = ExtractResult()
    cards = root.find_all("article", cls="event-Card")

    for card in cards:
        item = _to_item(card, snapshot)
        if item is None:
            result.items_unparsed += 1
        else:
            result.items.append(item)

    if not cards:
        result.notes.append(
            "Stránka neobsahuje žádný article.event-Card; šablona se mohla změnit.")
    if result.items_unparsed:
        result.notes.append(
            f"{result.items_unparsed} karet nemá název ani čitelný termín.")
    return result


def _to_item(card: Node, snapshot: Snapshot) -> RawItem | None:
    title_node = card.find(cls="event-Card-title")
    time_node = card.find("time", attr="datetime")
    title = clean_text(title_node.text() if title_node else None)
    date_raw = time_node.get("datetime") if time_node else None
    start_at, all_day = parse_iso_datetime(date_raw)
    if not title and not start_at:
        return None

    link = card.find("a", cls="event-Card-inner") or card.find("a")
    href = clean_text(link.get("href") if link else None)
    url = urljoin(snapshot.url, href) if href else None
    address_node = card.find(cls="event-Address")
    categories = [
        value for node in card.find_all(cls="Tag-label")
        if (value := clean_text(node.text()))
    ]
    additional_dates = [
        value for node in card.find_all(cls="color-darkestGrey")
        if (value := clean_text(node.text()))
        and node is not time_node
        and not _is_descendant(node, time_node)
    ]

    notes: list[str] = []
    if date_raw and start_at is None:
        notes.append(f"Termín „{date_raw}“ se nepodařilo přečíst jako ISO 8601.")
    extra = {"encoding": "pardubice-html-card"}
    if additional_dates:
        # Doplňkové termíny jsou bez roku a času. Rozbalovat je by byl odhad.
        extra["additional_dates"] = additional_dates
        notes.append("Karta uvádí další termíny bez úplného data; nejsou rozbaleny.")

    canonical_categories, categories_unmapped = normalize_categories(categories)
    if categories_unmapped:
        extra["categories_unmapped"] = categories_unmapped
        notes.append(
            "Neznámé zdrojové kategorie nebyly namapovány: "
            + ", ".join(categories_unmapped) + ".")

    return RawItem(
        uid=clean_text(link.get("data-id") if link else None) or url,
        title=title,
        date_text=date_raw,
        start_at=start_at,
        end_at=None,
        all_day=all_day,
        venue=None,
        address=clean_text(address_node.text() if address_node else None),
        municipality=None,
        description=None,
        url=url,
        price_text=None,
        categories=canonical_categories,
        organizers=[],
        recurring=True if additional_dates else None,
        extra=extra,
        notes=notes,
    )


def _is_descendant(node: Node, possible_parent: Node | None) -> bool:
    current = node.parent
    while current is not None:
        if current is possible_parent:
            return True
        current = current.parent
    return False
