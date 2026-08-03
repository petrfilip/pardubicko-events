"""Import repozitáře do provozní databáze.

Čte konfiguraci a publikovaná data z gitu a přenáší je do SQLite. Import je
idempotentní: obsahové tabulky se před zápisem vyprázdní, takže opakovaný
běh dá stejný výsledek.

Import nic nedomýšlí a nic neopravuje. Údaje přenáší doslovně, aby byl
následný export prokazatelně bezeztrátový. Rozpor v datech nepřechází
mlčky — import na něj spadne.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402

REPO_ROOT = db.REPO_ROOT

# Pole, která musí být shodná u všech kopií jedné akce (ADR 0001).
IDENTITY_FIELDS = ("title", "start_at", "end_at", "venue", "municipality")


class ImportError_(Exception):
    """Rozpor ve zdrojových datech. Import nepokračuje."""


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _identity(event: dict) -> tuple:
    return tuple(event.get(key) for key in IDENTITY_FIELDS) + (
        (event.get("source") or {}).get("url"),
        (event.get("source") or {}).get("type"),
        event.get("description"),
        event.get("all_day"),
        event.get("cancelled"),
        event.get("last_verified_at"),
        tuple(event.get("categories") or []),
        (event.get("price") or {}).get("type"),
        (event.get("price") or {}).get("text"),
    )


def import_weeks(connection, root: Path,
                 municipality_lookup: dict[str, int],
                 category_lookup: dict[str, str],
                 category_axes: dict[str, str],
                 category_search_terms: dict[str, str]) -> tuple[int, int]:
    manifest = _load(root / "data" / "manifest.json")
    db.set_meta(connection, "manifest_schema_version",
                str(manifest.get("schema_version", 1)))
    db.set_meta(connection, "manifest_generated_at", manifest.get("generated_at"))

    for position, week in enumerate(manifest.get("weeks") or []):
        connection.execute(
            "INSERT INTO week (id, date_from, date_to, file, position) "
            "VALUES (?, ?, ?, ?, ?)",
            (week["id"], week["from"], week["to"], week["file"], position),
        )

    identities: dict[str, tuple] = {}
    origins: dict[str, str] = {}
    events_seen = 0

    for week in manifest.get("weeks") or []:
        data = _load(root / week["file"])
        connection.execute(
            "UPDATE week SET generated_at = ? WHERE id = ?",
            (data.get("generated_at"), week["id"]),
        )

        for position, event in enumerate(data.get("events") or []):
            events_seen += 1
            event_id = event["id"]
            identity = _identity(event)

            if event_id in identities:
                if identities[event_id] != identity:
                    raise ImportError_(
                        f"Akce {event_id!r} má v {week['file']} jiné údaje než "
                        f"v {origins[event_id]}. Kopie jedné akce musí být "
                        f"shodné až na pole week (ADR 0001)."
                    )
            else:
                identities[event_id] = identity
                origins[event_id] = week["file"]
                _insert_event(
                    connection, event, municipality_lookup, category_lookup,
                    category_axes, category_search_terms)

            connection.execute(
                "INSERT INTO event_week (event_id, week_id, position) VALUES (?, ?, ?)",
                (event_id, week["id"], position),
            )

    return len(identities), events_seen


def _insert_event(connection, event: dict, municipality_lookup: dict[str, int],
                  category_lookup: dict[str, str], category_axes: dict[str, str],
                  category_search_terms: dict[str, str]) -> None:
    price = event.get("price") or {}
    source = event.get("source") or {}

    connection.execute(
        "INSERT INTO event ("
        "  id, title, description, start_at, end_at, all_day, venue,"
        "  municipality_name, municipality_id, price_type, price_text,"
        "  price_amount, price_currency,"
        "  source_type, source_url, cancelled, status, last_verified_at,"
        "  match_title_norm"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'published', ?, ?)",
        (
            event["id"], event["title"], event.get("description"),
            event["start_at"], event.get("end_at"),
            1 if event.get("all_day") else 0,
            event.get("venue"), event["municipality"],
            municipality_lookup.get(event["municipality"]),
            price.get("type", "unknown"), price.get("text"),
            price.get("amount"), price.get("currency"),
            source.get("type"), source.get("url"),
            1 if event.get("cancelled") else 0,
            event.get("last_verified_at"),
            _normalize_title(event["title"]),
        ),
    )

    canonical_categories: list[str] = []
    for name in event.get("categories") or []:
        category_id = category_lookup.get(_normalize_category(name))
        if category_id is None:
            raise ImportError_(
                f"Akce {event['id']!r} obsahuje neznámou kategorii {name!r}.")
        if category_id not in canonical_categories:
            canonical_categories.append(category_id)

    if not canonical_categories:
        raise ImportError_(f"Akce {event['id']!r} nemá žádnou kategorii.")
    if not any(category_axes.get(category_id) == "kind"
               for category_id in canonical_categories):
        raise ImportError_(
            f"Akce {event['id']!r} nemá kategorii povinné osy kind.")

    for position, category_id in enumerate(canonical_categories):
        connection.execute(
            "INSERT INTO event_category (event_id, position, name, category_id) "
            "VALUES (?, ?, ?, ?)",
            (event["id"], position, category_id, category_id),
        )

    if source.get("url"):
        connection.execute(
            "INSERT OR IGNORE INTO event_source (event_id, url, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?)",
            (event["id"], source["url"], event.get("last_verified_at"),
             event.get("last_verified_at")),
        )

    connection.execute(
        "INSERT INTO event_fts (event_id, title, description, venue, municipality, categories) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            event["id"], event["title"], event.get("description") or "",
            event.get("venue") or "", event["municipality"],
            " ".join(category_search_terms[category_id]
                     for category_id in canonical_categories),
        ),
    )


def _normalize_title(title: str) -> str:
    """Klíč pro blokování při deduplikaci. Bez diakritiky, bez interpunkce."""
    stripped = unicodedata.normalize("NFD", title.casefold())
    ascii_only = "".join(ch for ch in stripped if not unicodedata.combining(ch))
    return " ".join(
        "".join(ch if ch.isalnum() else " " for ch in ascii_only).split())


def _normalize_category(value: str) -> str:
    """Stejný porovnávací tvar jako v validační vrstvě."""
    decomposed = unicodedata.normalize("NFKD", value.strip().lower())
    without_marks = "".join(
        ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(
        r"-{2,}", "-", re.sub(r"[\s_]+", "-", without_marks)).strip("-")


def import_municipalities(connection, root: Path) -> tuple[int, dict[str, int]]:
    """Načte číselník a vrátí pouze jednoznačné doslovné názvy."""
    data = _load(root / "config" / "municipalities.json")
    by_name: dict[str, list[int]] = {}

    for municipality in data.get("municipalities") or []:
        connection.execute(
            "INSERT INTO municipality (id, name, district, region) "
            "VALUES (?, ?, ?, ?)",
            (municipality["code"], municipality["name"],
             municipality["district"], municipality["region"]),
        )
        by_name.setdefault(municipality["name"], []).append(municipality["code"])

    # Shodné názvy se v ČR vyskytují vícekrát. Bez okresu u akce se vazba
    # nesmí odhadnout, proto jsou v lookupu jen jednoznačné názvy.
    lookup = {
        name: codes[0] for name, codes in by_name.items() if len(codes) == 1
    }
    return len(data.get("municipalities") or []), lookup


def import_municipality_aliases(connection, root: Path,
                                municipality_lookup: dict[str, int]) -> tuple[int, dict[str, int]]:
    """Načte pouze doložené aliasy a rozšíří normalizační lookup.

    Jméno i kód cílové obce se ověřují proti číselníku. Rozpor je chyba,
    nikoli podnět k odhadu. Původní text se později zachová v raw payloadu.
    """
    path = root / "config" / "municipality-aliases.json"
    if not path.is_file():
        return 0, municipality_lookup
    data = _load(path)
    lookup = dict(municipality_lookup)
    seen: set[str] = set()
    for item in data.get("aliases") or []:
        alias = item["alias"].strip()
        key = _normalize_municipality(alias)
        if not key or key in seen:
            raise ImportError_(f"Duplicitní nebo prázdný alias obce {alias!r}.")
        target = connection.execute(
            "SELECT id, name FROM municipality WHERE id = ?",
            (item["municipality_code"],),
        ).fetchone()
        if target is None or target["name"] != item["municipality_name"]:
            raise ImportError_(
                f"Alias {alias!r} odkazuje na neexistující nebo nesouhlasící obec "
                f"{item['municipality_code']} / {item['municipality_name']!r}.")
        connection.execute(
            "INSERT INTO municipality_alias (alias, municipality_id) VALUES (?, ?)",
            (alias, item["municipality_code"]),
        )
        lookup[alias] = item["municipality_code"]
        seen.add(key)
    return len(seen), lookup


def _normalize_municipality(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.strip().casefold())
    return " ".join("".join(
        ch for ch in decomposed if not unicodedata.combining(ch)
    ).split())


def import_categories(connection, root: Path) -> tuple[
        int, int, dict[str, str], dict[str, str], dict[str, str]]:
    """Zrcadlí slovník a vytvoří normalizované mapování pro akce."""
    data = _load(root / "config" / "categories.json")
    lookup: dict[str, str] = {}
    axes: dict[str, str] = {}
    terms: dict[str, list[str]] = {}

    for category in data.get("categories") or []:
        connection.execute(
            "INSERT INTO category "
            "(id, axis, sort_order, label, description) VALUES (?, ?, ?, ?, ?)",
            (category["id"], category["axis"], category.get("order"),
             category["label"], category.get("description")),
        )
        lookup[_normalize_category(category["id"])] = category["id"]
        axes[category["id"]] = category["axis"]
        terms[category["id"]] = [category["id"], category["label"]]

    for alias in data.get("aliases") or []:
        connection.execute(
            "INSERT INTO category_alias (alias, category_id) VALUES (?, ?)",
            (alias["alias"], alias["category_id"]),
        )
        key = _normalize_category(alias["alias"])
        previous = lookup.get(key)
        if previous is not None and previous != alias["category_id"]:
            raise ImportError_(
                f"Alias kategorie {alias['alias']!r} míří na "
                f"{alias['category_id']!r}, ale normalizovaný klíč už patří "
                f"k {previous!r}.")
        lookup[key] = alias["category_id"]
        terms[alias["category_id"]].append(alias["alias"])

    return (
        len(data.get("categories") or []),
        len(data.get("aliases") or []),
        lookup,
        axes,
        {category_id: " ".join(dict.fromkeys(values))
         for category_id, values in terms.items()},
    )


def import_sources(connection, root: Path) -> int:
    registry = _load(root / "config" / "source-registry.json")
    for source in registry.get("sources") or []:
        connection.execute(
            "INSERT INTO source ("
            "  id, name, url, type, adapter, municipality_name, district, region,"
            "  priority, check_interval_days, enabled, notes"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source["id"], source["name"], source["url"], source["type"],
                source.get("adapter"), source.get("municipality"),
                source.get("district"), source.get("region"), source["priority"],
                source["check_interval_days"],
                1 if source.get("enabled", True) else 0, source.get("notes"),
            ),
        )
    return len(registry.get("sources") or [])


def import_facebook_pages(connection, root: Path) -> int:
    path = root / "config" / "facebook-sources.json"
    if not path.is_file():
        return 0
    data = _load(path)
    for page in data.get("pages") or []:
        connection.execute(
            "INSERT INTO facebook_page ("
            "  source_id, name, facebook_page, municipality_name, district, region,"
            "  priority, check_interval_days, enabled, notes"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                page["source_id"], page["name"], page["facebook_page"],
                page.get("municipality"), page.get("district"), page.get("region"),
                page["priority"], page["check_interval_days"],
                1 if page.get("enabled", True) else 0, page.get("notes"),
            ),
        )
    return len(data.get("pages") or [])


def import_candidates(connection, root: Path) -> int:
    count = 0
    known_events = {
        row["id"] for row in connection.execute("SELECT id FROM event")
    }
    for path in sorted((root / "research").glob("candidates*.json")):
        data = _load(path)
        for candidate in data.get("candidates") or []:
            production_id = candidate.get("production_event_id")
            connection.execute(
                "INSERT OR REPLACE INTO candidate ("
                "  id, source_file, discovery_method, payload, state, event_id,"
                "  created_at, reviewed_at, notes"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    candidate["id"], path.name, candidate["discovery_method"],
                    json.dumps(candidate, ensure_ascii=False),
                    candidate["status"],
                    production_id if production_id in known_events else None,
                    candidate["discovered_at"], candidate.get("reviewed_at"),
                    candidate.get("notes"),
                ),
            )
            count += 1
    return count


def import_all(connection, root: Path | None = None) -> dict[str, int]:
    root = Path(root) if root else REPO_ROOT
    db.reset(connection)

    municipalities, municipality_lookup = import_municipalities(connection, root)
    municipality_aliases, municipality_lookup = import_municipality_aliases(
        connection, root, municipality_lookup)
    (categories, category_aliases, category_lookup, category_axes,
     category_search_terms) = import_categories(connection, root)
    unique_events, event_rows = import_weeks(
        connection, root, municipality_lookup, category_lookup, category_axes,
        category_search_terms)
    stats = {
        "municipalities": municipalities,
        "municipality_aliases": municipality_aliases,
        "categories": categories,
        "category_aliases": category_aliases,
        "sources": import_sources(connection, root),
        "facebook_pages": import_facebook_pages(connection, root),
        "events": unique_events,
        "event_rows": event_rows,
        "candidates": import_candidates(connection, root),
    }
    connection.commit()
    return stats
