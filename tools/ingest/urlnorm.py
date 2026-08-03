"""Normalizace URL pro inbox (ADR 0005).

Normalizovaná podoba je **klíč deduplikace**. Nad `inbox.url_norm` je unikátní
index, takže dvě podoby téhož odkazu se liší jen sledovacími parametry musí
dát tentýž řetězec, jinak vznikne druhý záznam.

Původní podoba odkazu se ukládá doslovně do `inbox.url`. Normalizace tedy
nic neztrácí, jen vyrábí klíč.

Co se dělá a proč:

* **schéma** — sjednocuje se na `https`. Odkaz zkopírovaný z historického
  odkazu (`http://`) je totéž místo jako z prohlížeče (`https://`).
  Chybějící schéma se doplní na `https`.
* **host** — malá písmena, IDN na punycode, výchozí port pryč.
  `www.` se **nestrhává**; na některých webech je to jiný virtuální host
  a nemáme jak poznat který.
* **sledovací parametry** — pryč (`utm_*`, `fbclid`, `gclid`, …), viz
  `TRACKING_PARAMS`. Ostatní parametry zůstávají, protože na českých webech
  běžně nesou identitu stránky (`?id=123`, `?page=akce`).
* **pořadí parametrů** — řadí se podle názvu. Bez toho by `?a=1&b=2`
  a `?b=2&a=1` byly dva záznamy.
* **fragment** — zahazuje se, protože server ho stejně nedostane.
  Výjimkou je `#!`, což je legacy routa jednostránkové aplikace a jde
  o jinou stránku.
* **procentní kódování** — nekódované znaky v cestě se zakódují, hex se
  zvelkopísmení. Cesta zkopírovaná z adresního řádku (`/akce/pouť`) a táž
  cesta zkopírovaná z HTML (`/akce/pou%C5%A5`) jsou pak jeden záznam.
* **koncové lomítko** — u kořene zůstává (`https://x.cz/`), u ostatních cest
  se odstraňuje (`https://x.cz/akce/` → `https://x.cz/akce`).

Známá mez: kdyby web běžel jen na `http`, `url_norm` ho nepřečte a záznam
skončí ve `failed` s chybou spojení. Původní odkaz zůstává v `inbox.url`,
takže se nic neztratí a člověk to vidí.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit

# Parametry, které mění jen měření návštěvnosti, nikoli obsah stránky.
TRACKING_PARAMS = frozenset({
    "fbclid", "gclid", "gbraid", "wbraid", "dclid", "msclkid", "yclid",
    "igshid", "mc_cid", "mc_eid", "_ga", "_gl", "ref_src", "ref_url",
    "vgo_ee", "oly_enc_id", "oly_anon_id",
})
TRACKING_PREFIXES = ("utm_",)

DEFAULT_PORTS = {"http": "80", "https": "443"}
ALLOWED_SCHEMES = ("http", "https")

# Znaky, které se v cestě nechávají nezakódované. `%` je mezi nimi schválně:
# už zakódovanou cestu nesmíme zakódovat podruhé.
PATH_SAFE = "/%:@!$&'()*+,;=~-._"
QUERY_SAFE = "%:@!$'()*+,;=~-._/?"

HAS_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
PERCENT = re.compile(r"%([0-9a-fA-F]{2})")


class InvalidUrl(ValueError):
    """Odkaz, který se nedá použít. Nedohaduje se, vrací se chyba."""


def is_tracking_param(name: str) -> bool:
    lowered = name.lower()
    return lowered in TRACKING_PARAMS or lowered.startswith(TRACKING_PREFIXES)


def normalize_url(raw: str) -> str:
    """Vrátí klíč deduplikace. Nevalidní vstup je `InvalidUrl`, ne dohad."""
    if raw is None:
        raise InvalidUrl("Odkaz chybí.")

    text = raw.strip().strip("<>").strip()
    if not text:
        raise InvalidUrl("Odkaz je prázdný.")
    if any(ch.isspace() for ch in text):
        raise InvalidUrl(f"Odkaz obsahuje mezeru: {raw!r}")

    if not HAS_SCHEME.match(text):
        text = "https://" + text

    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise InvalidUrl(f"Odkaz se nedá rozebrat: {exc}") from exc

    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise InvalidUrl(f"Nepodporované schéma {scheme!r}; inbox bere jen http a https.")
    # Přihlašovací údaje v odkazu do fronty nepatří, viz ADR 0005 (žádné
    # obcházení ochran).
    if parts.username or parts.password:
        raise InvalidUrl("Odkaz obsahuje přihlašovací údaje; takový zdroj se nesbírá.")

    host = (parts.hostname or "").strip(".")
    if not host or "." not in host:
        raise InvalidUrl(f"Odkaz nemá použitelné doménové jméno: {raw!r}")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        host = host.lower()

    netloc = host
    if port is not None and str(port) != DEFAULT_PORTS[scheme]:
        netloc = f"{host}:{port}"

    path = _encode(parts.path or "/", PATH_SAFE)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"

    kept = [(name, value) for name, value in parse_qsl(parts.query, keep_blank_values=True)
            if not is_tracking_param(name)]
    kept.sort(key=lambda pair: (pair[0], pair[1]))
    query = "&".join(
        f"{_encode(name, QUERY_SAFE)}={_encode(value, QUERY_SAFE)}" if value != ""
        else _encode(name, QUERY_SAFE)
        for name, value in kept
    )

    fragment = parts.fragment if parts.fragment.startswith("!") else ""

    # Schéma se sjednocuje až tady, aby se z původního schématu stihl vzít
    # výchozí port.
    return urlunsplit(("https", netloc, path, query, fragment))


def _encode(value: str, safe: str) -> str:
    """Ustálí procentní kódování: nezakódované znaky zakóduje, hex zvelkopísmení.

    `unquote` se nepoužívá — dvojí dekódování by z `%2520` udělalo `%20`
    a změnilo tím cílovou stránku.
    """
    encoded = quote(value, safe=safe, encoding="utf-8")
    return PERCENT.sub(lambda m: "%" + m.group(1).upper(), encoded)


def display_url(url_norm: str) -> str:
    """Čitelná podoba pro výpis do terminálu. Do databáze nepatří."""
    parts = urlsplit(url_norm)
    return urlunsplit((parts.scheme, parts.netloc, unquote(parts.path),
                       parts.query, parts.fragment))
