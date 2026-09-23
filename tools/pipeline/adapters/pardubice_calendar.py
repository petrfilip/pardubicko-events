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

import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from .base import (
    TZ, ExtractResult, RawItem, Request, Snapshot, clean_text,
    normalize_categories, parse_iso_datetime,
)
from .htmlutil import Node, parse_html

name = "pardubice_calendar"

PAGES = 6
DETAIL_DATE_RE = re.compile(
    r"(?:Po|Út|St|Čt|Pá|So|Ne)?\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})"
    r"\s+(\d{1,2}):(\d{2})(?:\s*[–—-]\s*(\d{1,2}):(\d{2}))?",
    re.IGNORECASE,
)


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


def extract_detail(snapshot: Snapshot) -> ExtractResult:
    """Přečte detail akce a bezpečně rozbalí jeho úplné termíny.

    Výpisová karta záměrně nechává doplňkové termíny nerozbalené. Detail je
    jiný kontrakt: každý termín obsahuje den, měsíc, rok i čas, takže zde
    nevzniká odhad. Výstup je stále jen kurátorský návrh, nikoli publikace.
    """
    root = parse_html(snapshot.text())
    article = root.find("article", cls="article-Detail")
    if article is None:
        return ExtractResult(
            items_unparsed=1,
            notes=["Stránka neobsahuje article.article-Detail."],
        )

    title_node = article.find("h1", cls="PageHeader-title")
    title = clean_text(title_node.text() if title_node else None)
    address_node = article.find("address", cls="event-Address")
    address = clean_text(address_node.text() if address_node else None)
    description_node = article.find(cls="Text--content")
    description_paragraph = description_node.find("p") if description_node else None
    description = clean_text(
        description_paragraph.text() if description_paragraph else None)
    categories = [
        value for node in article.find_all(cls="Tag-label")
        if (value := clean_text(node.text()))
    ]
    canonical_categories, categories_unmapped = normalize_categories(categories)
    price_text = _detail_price(article)
    external_url = _detail_external_url(article, snapshot.url)
    map_address = _detail_map_address(article)
    accessibility = _detail_info_table(article)

    time_nodes = [
        node for container in article.find_all(cls="article-Detail-date")
        if (node := container.find("time")) is not None
    ]
    result = ExtractResult()
    for index, time_node in enumerate(time_nodes, start=1):
        date_text = clean_text(time_node.text())
        parsed = _parse_detail_date(date_text)
        if parsed is None:
            result.items_unparsed += 1
            result.notes.append(f"Úplný termín se nepodařilo přečíst: {date_text!r}.")
            continue
        start_at, end_at = parsed
        extra = {
            "encoding": "pardubice-html-detail",
            "term_index": index,
            "map_address": map_address,
            "external_url": external_url,
            "accessibility": accessibility,
        }
        if categories_unmapped:
            extra["categories_unmapped"] = categories_unmapped
        notes = []
        if categories_unmapped:
            notes.append(
                "Neznámé zdrojové kategorie nebyly namapovány: "
                + ", ".join(categories_unmapped) + ".")
        result.items.append(RawItem(
            uid=f"{snapshot.url}#term-{index}",
            title=title,
            date_text=date_text,
            start_at=start_at,
            end_at=end_at,
            all_day=False,
            venue=None,
            address=address,
            municipality=None,
            description=description,
            url=snapshot.url,
            price_text=price_text,
            categories=canonical_categories,
            organizers=[],
            recurring=True if len(time_nodes) > 1 else None,
            extra=extra,
            notes=notes,
        ))

    if not title:
        result.notes.append("Detail nemá čitelný název.")
    if not result.items and result.items_unparsed == 0:
        result.items_unparsed = 1
        result.notes.append("Detail neobsahuje žádný úplný termín.")
    return result


def canonical_detail_url(value: str) -> str:
    """Odstraní stránkovací parametr přenesený z odkazu ve výpisu."""
    parts = urlsplit(value)
    query = [(key, item) for key, item in parse_qsl(parts.query, keep_blank_values=True)
             if key != "page"]
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(query), parts.fragment))


def _parse_detail_date(value: str | None) -> tuple[str, str | None] | None:
    match = DETAIL_DATE_RE.search(value or "")
    if not match:
        return None
    day, month, year, hour, minute, end_hour, end_minute = match.groups()
    try:
        start = datetime(
            int(year), int(month), int(day), int(hour), int(minute), tzinfo=TZ)
        end = None
        if end_hour is not None:
            end = datetime(
                int(year), int(month), int(day), int(end_hour), int(end_minute),
                tzinfo=TZ)
    except ValueError:
        return None
    return start.isoformat(), end.isoformat() if end else None


def _detail_price(article: Node) -> str | None:
    for container in article.find_all(cls="panel-Base-contentInner"):
        text = clean_text(container.text())
        if text and "Vstupné" in text:
            value = text.split("Vstupné", 1)[1].strip()
            return clean_text(value)
    return None


def _detail_external_url(article: Node, base_url: str) -> str | None:
    for link in article.find_all("a"):
        href = clean_text(link.get("href"))
        if link.has_class("text-noWrap") and href and not href.startswith("javascript:"):
            return urljoin(base_url, href)
    return None


def _detail_map_address(article: Node) -> str | None:
    panel = article.find(cls="panel-GoogleMap-content")
    paragraph = panel.find("p") if panel else None
    return clean_text(paragraph.text() if paragraph else None)


def _detail_info_table(article: Node) -> dict[str, str]:
    values: dict[str, str] = {}
    panel = article.find(cls="panel-EventInfo")
    if panel is None:
        return values
    for row in panel.find_all("tr"):
        heading = row.find("th")
        cell = row.find("td")
        key = clean_text(heading.text() if heading else None)
        value = clean_text(cell.text() if cell else None)
        if key and value:
            values[key] = value
    return values


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
