"""Rozlišení vloženého odkazu: detail akce, výpis, nebo nerozpoznáno.

Toto je heuristika a **bude se mýlit**. Návrh je proto vědomě vychýlený:
když si není jistá, vrací `unknown`. Z kalendáře udělat jednu nesmyslnou
„akci“ je horší chyba než nechat člověka rozhodnout.

Modul nesahá na síť. Vstupem je HTML jako text, výstupem `Classification`.

## Jak se rozhoduje

Nejdřív se ze stránky posbírají **signály** (`Signals`), z nich se poskládají
pojmenované **indicie**, a teprve indicie rozhodují. Každý výsledek nese
seznam indicií, které se uplatnily, aby bylo v logu i v poznámce kandidáta
vidět proč.

Indicie pro výpis:

* `L1-jsonld-vice-akci` — 3 a více objektů `schema.org/Event`,
* `L2-datovane-odkazy` — 4 a více odkazů, u kterých je v okolí datum,
  a zároveň 3 a více různých termínů na stránce,
* `L3-strankovani` — stránkovací prvek a aspoň dva různé termíny,
* `L4-kalendarova-mrizka` — tabulka s řadou názvů dnů v týdnu nebo mřížka
  s desítkami číselných buněk,
* `L5-microdata-vice-akci` — 3 a více bloků `itemtype=schema.org/Event`,
* `L6-mnoho-odkazu-na-akce` — 8 a více různých odkazů ve tvaru detailu akce
  (`/akce/…`, `/events/…`). Doplněk k `L2` pro weby, které datum sázejí po
  částech do samostatných elementů, takže se z textu nepřečte.

Indicie pro detail:

* `D1-jedina-jsonld-akce` — právě jeden `schema.org/Event` mimo `ItemList`,
* `D2-og-type-event` — `og:type` je `event` nebo `article:event`,
* `D3-jeden-termin-jeden-nazev` — nejvýše dva různé termíny a právě jeden
  `<h1>` (to je ten „právě jeden termín a jeden název“ z ADR 0005),
* `D4-pridat-do-kalendare` — odkaz na `.ics` nebo tlačítko „přidat do
  kalendáře“ a nejvýše dva různé termíny,
* `D5-detailni-tvar-url` — vložená adresa má tvar detailu (`/akce/<slug>`)
  a stránka má název i termín. Sama o sobě nerozhoduje: uplatní se až
  ve chvíli, kdy se neuchytila žádná indicie výpisu. Většina českých webů
  akcí nemá strukturovaná data, takže bez tohoto signálu by valná část
  skutečných detailů skončila v `unknown`.

Rozhodnutí v tomto pořadí:

1. silná indicie detailu (`D1`, `D2`) **a zároveň** silná indicie výpisu
   (`L1`, `L4`, `L5`) → `unknown`, protokolovaný spor,
2. silná indicie detailu → `event-detail`,
3. jakákoli indicie výpisu → `listing`,
4. slabá indicie detailu (`D3`, `D4`) → `event-detail`,
5. jinak → `unknown`.

Pořadí je záměrné. Silná strukturovaná značka detailu přebíjí slabé textové
indicie výpisu, ale nikdy nepřebije jinou silnou značku — spor se nedohaduje.

`event-detail` navíc **vyžaduje název i termín**. Bez nich by vznikl
kandidát, kterému Curator nemá co ověřit, takže se místo toho vrací
`unknown`.

## Kde má heuristika hranice

* Stránka, která obsah dotahuje JavaScriptem, přijde jako prázdná kostra.
  Pozná se podle krátkého textu a skončí v `unknown` s tímto důvodem.
* Detail akce s postranním výpisem „další akce“ má mnoho termínů, takže
  `D3` neprojde. Zachytí ho `D5`, ale jen pokud má adresa tvar detailu.
* Výpis se dvěma nebo třemi akcemi bez stránkování je pod prahem `L2`
  a projde jako `unknown`, případně jako detail, pokud má jediný `<h1>`.
  To je ta nejnepříjemnější, ale zároveň nejméně škodlivá záměna: kandidát
  jde stejně Curatorovi k ověření.
* Přehledová stránka kategorie na adrese `/akce/koncerty` má tvar detailu.
  Pokud na ní je málo odkazů a málo termínů, `D5` ji prohlásí za detail.
  Curator to pozná podle názvu bez termínu v poznámce kandidáta.
* Vícedenní akce zapsaná jako „15.–17. srpna“ se počítá jako jeden termín.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urlsplit

# Tagy, jejichž obsah není vidět a nesmí se počítat do textu stránky.
INVISIBLE_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "head"})
# Tagy, po kterých se do textu vkládá zlom, aby se slova neslepila.
BLOCK_TAGS = frozenset({
    "p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
    "article", "section", "header", "footer", "nav", "ul", "ol", "table", "dl",
    "dt", "dd", "figcaption", "blockquote", "time", "span", "a", "strong", "em",
})

MONTHS = {
    "ledna": 1, "leden": 1, "unora": 2, "unor": 2, "brezna": 3, "brezen": 3,
    "dubna": 4, "duben": 4, "kvetna": 5, "kveten": 5, "cervna": 6, "cerven": 6,
    "cervence": 7, "cervenec": 7, "srpna": 8, "srpen": 8, "zari": 9,
    "rijna": 10, "rijen": 10, "listopadu": 11, "listopad": 11,
    "prosince": 12, "prosinec": 12,
}
MAX_DAY = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30,
           7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}

WEEKDAYS = frozenset({
    "po", "ut", "st", "ct", "pa", "so", "ne",
    "pon", "ute", "str", "ctv", "pat", "sob", "ned",
    "pondeli", "utery", "streda", "ctvrtek", "patek", "sobota", "nedele",
})

# "15. 8. 2026", "15.8.", ale ne "1.500" ani pořadí "1. 2. 3."
RE_DATE_NUM = re.compile(r"(?<![\d.])(\d{1,2})\.\s?(\d{1,2})\.(?:\s?(\d{4}))?(?![\d.])")
RE_DATE_WORD = re.compile(
    r"(?<!\d)(\d{1,2})\.\s?(" + "|".join(sorted(MONTHS, key=len, reverse=True)) +
    r")(?:\s+(\d{4}))?\b")
RE_DATE_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")

RE_PAGINATION_ATTR = re.compile(r"(?i)\b(pagination|pager|paging|strankovani|page-numbers)\b")
RE_CALENDAR_ATTR = re.compile(r"(?i)(kalendar|calendar|datepicker|fc-daygrid|month-grid)")
RE_EVENT_PATH = re.compile(
    r"(?i)/(akce|akci|udalost|udalosti|event|events|program|koncert|koncerty|"
    r"predstaveni|vystava|vystavy|detail)/[^/?#]+")
RE_ICS = re.compile(r"(?i)(\.ics\b|/ical|format=ical|add-to-calendar|pridat-do-kalendare)")
RE_ADD_TO_CALENDAR = re.compile(r"(?i)(p[rř]idat do kalend[aá][rř]e|add to calendar|"
                                r"exportovat do kalend[aá][rř]e)")

EVENT_TYPES = re.compile(r"(?i)(^|[^a-z])((music|theater|theatre|dance|comedy|literary|"
                         r"screening|social|sports|business|childrens|course|delivery|"
                         r"education|exhibition|festival|food|hackathon|literary|sale|"
                         r"visual)?event|festival)$")

WINDOW_BEFORE = 180   # kolik znaků před odkazem se prohledává na datum
WINDOW_AFTER = 60
MIN_TEXT_LENGTH = 400  # pod tím je stránka spíš kostra pro JavaScript


@dataclass
class Signals:
    """Změřené vlastnosti stránky. Bez interpretace."""

    jsonld_events: int = 0
    jsonld_events_in_list: int = 0
    microdata_events: int = 0
    distinct_dates: int = 0
    dated_links: int = 0
    event_links: int = 0
    pagination: bool = False
    calendar_grid: bool = False
    h1_count: int = 0
    og_type: str | None = None
    ics_link: bool = False
    text_length: int = 0
    url_detail_shape: bool = False
    title: str | None = None
    date_text: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    venue: str | None = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Classification:
    kind: str                       # event-detail | listing | unknown
    reasons: list[str] = field(default_factory=list)
    signals: Signals = field(default_factory=Signals)

    @property
    def reason_text(self) -> str:
        return ", ".join(self.reasons) if self.reasons else "bez indicií"


class PageParser(HTMLParser):
    """Vytáhne z HTML text, odkazy a značky. Bez závislosti na cizí knihovně.

    Nesnaží se o věrný rendering. Stačí, aby vzájemná poloha textu a odkazů
    zhruba odpovídala tomu, co vidí člověk.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.length = 0
        self.anchors: list[dict] = []
        self.jsonld: list[str] = []
        self.meta: dict[str, str] = {}
        self.times: list[str] = []
        self.itemtypes: list[str] = []
        self.h1_count = 0
        self.title: str | None = None
        self.pagination = False
        self.calendar_attr = False
        self.numeric_cells = 0
        self.weekday_cells = 0
        self._invisible = 0
        self._ldjson_depth = 0
        self._ld_buffer: list[str] = []
        self._anchor_stack: list[dict] = []
        self._cell: list[str] | None = None
        self._in_title = False
        self._in_h1 = False
        self._h1_text: list[str] = []

    # --- sběr ---------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {name: (value or "") for name, value in attrs}
        if tag == "script" and "ld+json" in values.get("type", "").lower():
            self._ldjson_depth = 1
            self._ld_buffer = []
            return
        if tag in INVISIBLE_TAGS:
            self._invisible += 1
        if tag in BLOCK_TAGS:
            self._append("\n")

        markers = " ".join((values.get("class", ""), values.get("id", ""),
                            values.get("rel", "")))
        if RE_PAGINATION_ATTR.search(markers) or values.get("rel", "").lower() == "next":
            self.pagination = True
        if RE_CALENDAR_ATTR.search(" ".join((values.get("class", ""), values.get("id", "")))):
            self.calendar_attr = True

        if tag == "a":
            href = values.get("href", "")
            self._anchor_stack.append({"href": href, "start": self.length, "end": self.length})
        elif tag == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            if key:
                self.meta[key] = values.get("content", "")
        elif tag == "time" and values.get("datetime"):
            self.times.append(values["datetime"])
        elif tag == "h1":
            self.h1_count += 1
            self._in_h1 = self.h1_count == 1
            self._h1_text = []
        elif tag == "title":
            self._in_title = True
        elif tag in ("td", "th"):
            self._cell = []
        if "itemtype" in values:
            self.itemtypes.append(values["itemtype"])

    def handle_startendtag(self, tag: str, attrs) -> None:
        # Samouzavírací tag nesmí zvýšit počítadlo neviditelných bloků.
        if tag in INVISIBLE_TAGS:
            return
        self.handle_starttag(tag, attrs)
        if tag in ("a", "h1", "title", "td", "th"):
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._ldjson_depth:
            self.jsonld.append("".join(self._ld_buffer))
            self._ldjson_depth = 0
            self._ld_buffer = []
            return
        if tag in INVISIBLE_TAGS and self._invisible:
            self._invisible -= 1
        if tag in BLOCK_TAGS:
            self._append("\n")
        if tag == "a" and self._anchor_stack:
            anchor = self._anchor_stack.pop()
            anchor["end"] = self.length
            if anchor["href"]:
                self.anchors.append(anchor)
        elif tag == "h1":
            self._in_h1 = False
        elif tag == "title":
            self._in_title = False
        elif tag in ("td", "th") and self._cell is not None:
            cell = " ".join("".join(self._cell).split())
            if cell.isdigit() and len(cell) <= 2:
                self.numeric_cells += 1
            if _fold(cell).strip(".") in WEEKDAYS:
                self.weekday_cells += 1
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._ldjson_depth:
            self._ld_buffer.append(data)
            return
        if self._in_title and self.title is None:
            self.title = " ".join(data.split()) or None
        if self._invisible:
            return
        if self._in_h1:
            self._h1_text.append(data)
        if self._cell is not None:
            self._cell.append(data)
        self._append(data)

    def _append(self, data: str) -> None:
        self.chunks.append(data)
        self.length += len(data)

    # --- výstup -------------------------------------------------------------

    @property
    def text(self) -> str:
        return "".join(self.chunks)

    @property
    def h1_text(self) -> str | None:
        return " ".join("".join(self._h1_text).split()) or None


def _fold(text: str) -> str:
    """Malá písmena bez diakritiky. Délka řetězce se zachovává kvůli indexům."""
    out = []
    for ch in text.lower():
        decomposed = unicodedata.normalize("NFD", ch)
        base = "".join(c for c in decomposed if not unicodedata.combining(c))
        out.append(base if len(base) == 1 else ch)
    return "".join(out)


def _valid(day: int, month: int, year: int | None) -> bool:
    if not (1 <= month <= 12 and 1 <= day <= MAX_DAY[month]):
        return False
    if year is None:
        return True
    if not (2000 <= year <= 2100):
        return False
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def find_dates(text: str) -> list[tuple[tuple[int, int, int | None], int, int]]:
    """Termíny v textu jako `((den, měsíc, rok|None), začátek, konec)`.

    Hledá se ve složené podobě bez diakritiky, ale indexy odpovídají
    původnímu textu, takže se z něj dá vyříznout doslovný zápis data.
    """
    folded = _fold(text)
    found: list[tuple[tuple[int, int, int | None], int, int]] = []

    for match in RE_DATE_NUM.finditer(folded):
        day, month = int(match.group(1)), int(match.group(2))
        year = int(match.group(3)) if match.group(3) else None
        if _valid(day, month, year):
            found.append(((day, month, year), match.start(), match.end()))

    for match in RE_DATE_WORD.finditer(folded):
        day = int(match.group(1))
        month = MONTHS[match.group(2)]
        year = int(match.group(3)) if match.group(3) else None
        if _valid(day, month, year):
            found.append(((day, month, year), match.start(), match.end()))

    for match in RE_DATE_ISO.finditer(folded):
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if _valid(day, month, year):
            found.append(((day, month, year), match.start(), match.end()))

    found.sort(key=lambda item: item[1])
    return found


def distinct_dates(dates) -> set[tuple[int, int, int | None]]:
    """Různé termíny. Týž den zapsaný s rokem i bez roku je jeden termín."""
    with_year = {(day, month) for (day, month, year) in dates if year is not None}
    result = set()
    for day, month, year in dates:
        if year is None and (day, month) in with_year:
            continue
        result.add((day, month, year))
    return result


def _jsonld_objects(raw_blocks: list[str]):
    """Objekty z JSON-LD s příznakem, jestli visí v `ItemList`."""
    for block in raw_blocks:
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        yield from _walk_jsonld(data, in_list=False)


def _walk_jsonld(node, in_list: bool):
    if isinstance(node, list):
        for item in node:
            yield from _walk_jsonld(item, in_list)
        return
    if not isinstance(node, dict):
        return
    yield node, in_list
    types = node.get("@type")
    types = types if isinstance(types, list) else [types]
    nested_in_list = in_list or any(
        isinstance(t, str) and t.lower() in ("itemlist", "collectionpage", "breadcrumblist")
        for t in types)
    for key, value in node.items():
        if key == "@type":
            continue
        yield from _walk_jsonld(value, nested_in_list)


def _is_event(node: dict) -> bool:
    types = node.get("@type")
    types = types if isinstance(types, list) else [types]
    return any(isinstance(t, str) and EVENT_TYPES.search(t.strip()) for t in types)


def looks_like_detail_url(url: str | None) -> bool:
    """Má adresa tvar detailu akce, tedy `/akce/<neco>`?

    Rozlišuje `https://www.zamek-slatinany.cz/cs/akce` (výpis) od
    `https://www.zamek-slatinany.cz/cs/akce/132536-hrajte-si-…` (detail).
    """
    if not url:
        return False
    path = urlsplit(url).path
    match = RE_EVENT_PATH.search(path)
    if not match:
        return False
    # `/akce/2026-08` nebo `/akce/srpen` je spíš filtr výpisu než detail.
    slug = path[match.start():].split("/")[2]
    folded = _fold(slug)
    if folded in MONTHS or re.fullmatch(r"\d{4}(-\d{1,2})?", folded):
        return False
    return True


def collect_signals(html: str, url: str | None = None) -> Signals:
    parser = PageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — rozbité HTML nesmí shodit běh fronty
        pass

    text = parser.text
    dates = find_dates(text)
    unique = distinct_dates([value for value, _, _ in dates])

    # <time datetime="…"> je nejspolehlivější zdroj termínu, když ho web má.
    for value in parser.times:
        match = RE_DATE_ISO.match(value.strip())
        if match:
            triple = (int(match.group(3)), int(match.group(2)), int(match.group(1)))
            if _valid(*triple):
                unique.add(triple)

    signals = Signals()
    signals.url_detail_shape = looks_like_detail_url(url)
    signals.text_length = len(" ".join(text.split()))
    signals.h1_count = parser.h1_count
    signals.og_type = (parser.meta.get("og:type") or "").strip().lower() or None
    signals.distinct_dates = len(unique)
    signals.pagination = parser.pagination
    signals.calendar_grid = (
        parser.weekday_cells >= 5 or (parser.calendar_attr and parser.numeric_cells >= 20))

    event_hrefs = set()
    dated_hrefs = set()
    for anchor in parser.anchors:
        href = anchor["href"]
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        if RE_EVENT_PATH.search(href):
            event_hrefs.add(href)
        window = text[max(0, anchor["start"] - WINDOW_BEFORE):anchor["end"] + WINDOW_AFTER]
        if find_dates(window):
            dated_hrefs.add(href)
        if RE_ICS.search(href):
            signals.ics_link = True
    signals.event_links = len(event_hrefs)
    signals.dated_links = len(dated_hrefs)
    if RE_ADD_TO_CALENDAR.search(text):
        signals.ics_link = True

    for itemtype in parser.itemtypes:
        if re.search(r"(?i)schema\.org/[A-Za-z]*Event\b", itemtype):
            signals.microdata_events += 1

    event_node = None
    for node, in_list in _jsonld_objects(parser.jsonld):
        if not _is_event(node):
            continue
        signals.jsonld_events += 1
        if in_list:
            signals.jsonld_events_in_list += 1
        elif event_node is None:
            event_node = node

    # Název: JSON-LD, pak <h1>, pak og:title, pak <title>.
    if event_node and isinstance(event_node.get("name"), str):
        signals.title = " ".join(event_node["name"].split()) or None
    if not signals.title:
        signals.title = parser.h1_text or (parser.meta.get("og:title") or "").strip() or None
    if not signals.title:
        signals.title = parser.title

    if event_node:
        signals.start_at = _as_text(event_node.get("startDate"))
        signals.end_at = _as_text(event_node.get("endDate"))
        location = event_node.get("location")
        if isinstance(location, dict):
            signals.venue = _as_text(location.get("name"))
        elif isinstance(location, str):
            signals.venue = location.strip() or None

    signals.date_text = signals.start_at
    if not signals.date_text and dates:
        start, end = dates[0][1], dates[0][2]
        signals.date_text = " ".join(text[start:end].split()) or None

    return signals


def _as_text(value) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def classify(html: str, url: str | None = None) -> Classification:
    """Rozhodne, co je vložený odkaz zač. Při pochybnostech `unknown`."""
    signals = collect_signals(html, url)

    if signals.text_length < MIN_TEXT_LENGTH and signals.jsonld_events == 0:
        return Classification(
            "unknown",
            [f"stránka nese jen {signals.text_length} znaků textu — pravděpodobně "
             "kostra dotahovaná JavaScriptem, obsah v HTML není"],
            signals)

    listing: list[str] = []
    if signals.jsonld_events >= 3:
        listing.append(f"L1-jsonld-vice-akci ({signals.jsonld_events})")
    if signals.jsonld_events_in_list >= 2:
        listing.append(f"L1-jsonld-akce-v-itemlist ({signals.jsonld_events_in_list})")
    if signals.dated_links >= 4 and signals.distinct_dates >= 3:
        listing.append(f"L2-datovane-odkazy ({signals.dated_links} odkazů, "
                       f"{signals.distinct_dates} termínů)")
    if signals.pagination and signals.distinct_dates >= 2:
        listing.append("L3-strankovani")
    if signals.calendar_grid:
        listing.append("L4-kalendarova-mrizka")
    if signals.microdata_events >= 3:
        listing.append(f"L5-microdata-vice-akci ({signals.microdata_events})")
    if signals.event_links >= 8:
        listing.append(f"L6-mnoho-odkazu-na-akce ({signals.event_links})")

    detail: list[str] = []
    single_jsonld = (signals.jsonld_events == 1 and signals.jsonld_events_in_list == 0)
    if single_jsonld:
        detail.append("D1-jedina-jsonld-akce")
    if signals.og_type in ("event", "article:event"):
        detail.append("D2-og-type-event")
    if signals.distinct_dates and signals.distinct_dates <= 2 and signals.h1_count == 1:
        detail.append(f"D3-jeden-termin-jeden-nazev ({signals.distinct_dates} termín/y)")
    if signals.ics_link and signals.distinct_dates and signals.distinct_dates <= 2:
        detail.append("D4-pridat-do-kalendare")
    if signals.url_detail_shape and signals.distinct_dates and signals.title:
        detail.append("D5-detailni-tvar-url")

    strong_detail = [r for r in detail if r.startswith(("D1", "D2"))]
    strong_listing = [r for r in listing if r.startswith(("L1", "L4", "L5"))]

    if strong_detail and strong_listing:
        return Classification(
            "unknown",
            ["spor silných indicií: " + ", ".join(strong_detail + strong_listing)
             + " — stránka vypadá zároveň jako detail i jako výpis, nedohaduje se"],
            signals)

    if strong_detail:
        return _as_detail(signals, strong_detail + [r for r in detail if r not in strong_detail])
    if listing:
        return Classification("listing", listing, signals)
    if detail:
        return _as_detail(signals, detail)

    return Classification("unknown", [_no_evidence(signals)], signals)


def _as_detail(signals: Signals, reasons: list[str]) -> Classification:
    """Detail bez názvu nebo bez termínu není detail; kandidát by byl prázdný."""
    missing = []
    if not signals.title:
        missing.append("název")
    if not signals.date_text:
        missing.append("termín")
    if missing:
        return Classification(
            "unknown",
            [f"vypadá jako detail akce ({', '.join(reasons)}), ale chybí "
             + " a ".join(missing) + " — kandidát by neměl co ověřovat"],
            signals)
    return Classification("event-detail", reasons, signals)


def _no_evidence(signals: Signals) -> str:
    if signals.distinct_dates == 0:
        return "na stránce není žádný termín ani značka schema.org/Event"
    return (f"termínů na stránce: {signals.distinct_dates}, datovaných odkazů: "
            f"{signals.dated_links}, nadpisů h1: {signals.h1_count} — na detail "
            "ani na výpis to nestačí")
