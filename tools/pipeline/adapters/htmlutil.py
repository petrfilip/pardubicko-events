"""Minimální strom nad `html.parser` a čtečka mikrodat.

Proč vlastní strom a ne knihovna: fáze 2 má běžet na jednom stroji bez
build kroku a `docs/project-vision.md` drží závislosti na minimu. Pro to,
co adaptéry potřebují — najít bloky podle značky a třídy a přečíst z nich
text a atributy — stačí standardní knihovna.

Parser je záměrně tolerantní. České obecní weby běžně nezavírají `<p>`
ani `<li>`; koncová značka bez páru se proto ignoruje a značka, která
zavírá starší úroveň, uzavře i vše nad ní.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Iterator

# Prvky bez obsahu. Kdyby se pushovaly na zásobník, `<br>` uprostřed textu
# by spolkl zbytek bloku.
VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})

# Do textu se nepočítá obsah skriptů a stylů; jinak by se do názvu akce
# dostal kus JavaScriptu.
NON_TEXT_ELEMENTS = frozenset({"script", "style", "template", "noscript"})

_WS_RE = re.compile(r"\s+")


class Node:
    """Uzel stromu. Textové děti jsou obyčejné řetězce."""

    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str] | None = None,
                 parent: "Node | None" = None) -> None:
        self.tag = tag
        self.attrs: dict[str, str] = attrs or {}
        self.children: list["Node | str"] = []
        self.parent = parent

    # -- přístup k atributům --------------------------------------------------

    def get(self, name: str, default: str | None = None) -> str | None:
        return self.attrs.get(name, default)

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def has_class(self, name: str) -> bool:
        return name in self.classes

    # -- text -----------------------------------------------------------------

    def text(self) -> str:
        """Text uzlu se sjednocenými bílými znaky, bez skriptů a stylů."""
        parts: list[str] = []
        self._collect_text(parts)
        return _WS_RE.sub(" ", " ".join(parts)).strip()

    def _collect_text(self, parts: list[str]) -> None:
        if self.tag in NON_TEXT_ELEMENTS:
            return
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            else:
                child._collect_text(parts)

    def raw_text(self) -> str:
        """Doslovný text včetně obsahu skriptů (kvůli JSON-LD)."""
        parts: list[str] = []
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            else:
                parts.append(child.raw_text())
        return "".join(parts)

    # -- hledání ---------------------------------------------------------------

    def iter_elements(self) -> Iterator["Node"]:
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.iter_elements()

    def find_all(self, tag: str | None = None, *, cls: str | None = None,
                 attr: str | None = None, attr_value: str | None = None,
                 limit: int | None = None) -> list["Node"]:
        found: list[Node] = []
        for node in self.iter_elements():
            if tag is not None and node.tag != tag:
                continue
            if cls is not None and not node.has_class(cls):
                continue
            if attr is not None:
                if attr not in node.attrs:
                    continue
                if attr_value is not None and node.attrs.get(attr) != attr_value:
                    continue
            found.append(node)
            if limit is not None and len(found) >= limit:
                break
        return found

    def find(self, tag: str | None = None, **kwargs: Any) -> "Node | None":
        found = self.find_all(tag, limit=1, **kwargs)
        return found[0] if found else None

    def __repr__(self) -> str:  # pragma: no cover — jen pro ladění
        return f"<Node {self.tag} class={self.attrs.get('class')!r}>"


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.stack: list[Node] = [self.root]

    @property
    def current(self) -> Node:
        return self.stack[-1]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.current)
        self.current.children.append(node)
        if tag not in VOID_ELEMENTS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.current)
        self.current.children.append(node)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_ELEMENTS:
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return
        # Koncová značka bez páru: ignorovat. Zavřít nejbližší uzel by
        # rozsypalo strukturu celého zbytku stránky.

    def handle_data(self, data: str) -> None:
        if data.strip() or (self.current.children and data):
            self.current.children.append(data)


def parse_html(text: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(text)
    builder.close()
    return builder.root


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------

def iter_jsonld(root: Node) -> Iterator[tuple[Any | None, str]]:
    """Projde bloky `application/ld+json`.

    U nečitelného bloku vrací `(None, syrový_text)`. Volající si ho započítá
    do `items_unparsed`; tiše ho zahodit by znamenalo ztratit akce bez stopy.
    """
    for script in root.find_all("script"):
        ctype = (script.get("type") or "").lower()
        if "ld+json" not in ctype:
            continue
        raw = script.raw_text().strip()
        if not raw:
            continue
        try:
            yield json.loads(raw), raw
        except (json.JSONDecodeError, ValueError):
            yield None, raw


def iter_jsonld_objects(payload: Any) -> Iterator[dict]:
    """Rozbalí `@graph`, seznamy i vnořené objekty na jednotlivé slovníky."""
    if isinstance(payload, list):
        for entry in payload:
            yield from iter_jsonld_objects(entry)
    elif isinstance(payload, dict):
        yield payload
        for key, value in payload.items():
            if key.startswith("@") and key != "@graph":
                continue
            if isinstance(value, (list, dict)):
                yield from iter_jsonld_objects(value)


# ---------------------------------------------------------------------------
# Mikrodata
# ---------------------------------------------------------------------------

_SRC_ATTR = {
    "audio": "src", "embed": "src", "iframe": "src", "img": "src",
    "source": "src", "track": "src", "video": "src",
}
_HREF_ATTR = {"a": "href", "area": "href", "link": "href"}


def itemtype_names(node: Node) -> set[str]:
    """Poslední segment každého `itemtype`, tedy např. `Event`."""
    raw = node.get("itemtype") or ""
    return {part.rstrip("/").rsplit("/", 1)[-1] for part in raw.split() if part}


def microdata_value(node: Node) -> Any:
    """Hodnota vlastnosti podle typu prvku (HTML Microdata, sekce 5)."""
    if node.get("itemscope") is not None:
        return microdata_item(node)
    if node.tag == "meta":
        return node.get("content")
    if node.tag in _HREF_ATTR:
        return node.get(_HREF_ATTR[node.tag])
    if node.tag in _SRC_ATTR:
        return node.get(_SRC_ATTR[node.tag])
    if node.tag == "object":
        return node.get("data")
    if node.tag in ("data", "meter"):
        return node.get("value")
    if node.tag == "time":
        return node.get("datetime") or node.text()
    content = node.get("content")
    if content is not None:
        return content
    return node.text()


def microdata_item(scope: Node) -> dict[str, Any]:
    """Vlastnosti jednoho `itemscope`. Vnořený scope se stane vnořeným slovníkem."""
    item: dict[str, Any] = {}
    types = itemtype_names(scope)
    if types:
        item["@type"] = sorted(types)[0] if len(types) == 1 else sorted(types)

    def walk(node: Node) -> None:
        for child in node.children:
            if not isinstance(child, Node):
                continue
            prop = child.get("itemprop")
            if prop:
                value = microdata_value(child)
                for name in prop.split():
                    existing = item.get(name)
                    if existing is None:
                        item[name] = value
                    elif isinstance(existing, list):
                        existing.append(value)
                    else:
                        item[name] = [existing, value]
            # Do vnořeného scope se nesestupuje — jeho vlastnosti patří jemu.
            if child.get("itemscope") is None:
                walk(child)

    walk(scope)
    return item


def find_microdata_scopes(root: Node, type_names: set[str]) -> list[Node]:
    """Všechny `itemscope` uzly, jejichž `itemtype` je v `type_names`."""
    return [node for node in root.iter_elements()
            if node.get("itemscope") is not None and itemtype_names(node) & type_names]
