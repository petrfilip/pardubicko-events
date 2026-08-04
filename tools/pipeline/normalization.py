#!/usr/bin/env python3
"""Deterministická normalizace výstupu adaptérů do kandidátního tvaru.

Vrstva nic nepublikuje a nic nedohaduje. Když nelze bezpečně určit název,
termín, konkrétní URL nebo obec, vrátí důvod karantény spolu s doslovným raw
payloadem. Díky tomu lze rozhodnutí zopakovat nad stejným snapshotem.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ingest"))

from adapters.base import RawItem, TZ, clean_text, parse_iso_datetime  # noqa: E402
from urlnorm import InvalidUrl, normalize_url  # noqa: E402

_CZECH_DATE = re.compile(
    r"^\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})"
    r"(?:\s+(?:od\s+)?(\d{1,2}):(\d{2}))?\s*$",
    re.IGNORECASE,
)
_FREE = re.compile(
    r"\b(?:zdarma|vstup\s+voln(?:y|ý)|bez\s+vstupn(?:eho|ého))\b",
    re.IGNORECASE,
)
_CZK_AMOUNT = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[ .]\d{3})*|\d+)(?![.,]\d)\s*(?:kč|czk)\b",
    re.IGNORECASE,
)
_CZK = re.compile(r"\b(?:kč|czk)\b", re.IGNORECASE)
_PRICE_RANGE = re.compile(
    r"\d[\d .]*(?:-|–|—|\baž\b|\bdo\b)\s*\d[\d .]*(?:kč|czk)\b",
    re.IGNORECASE,
)
_DECIMAL_CZK = re.compile(r"\d+[,.]\d{1,2}\s*(?:kč|czk)\b", re.IGNORECASE)


@dataclass(frozen=True)
class NormalizedCandidate:
    candidate_id: str
    raw: dict[str, Any]
    normalized: dict[str, Any]
    quarantine_reasons: tuple[str, ...]

    @property
    def state(self) -> str:
        return "quarantined" if self.quarantine_reasons else "new"

    def payload(self, source_id: str, provenance: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source_id": source_id,
            "raw": self.raw,
            "normalized": self.normalized,
            "quarantine_reasons": list(self.quarantine_reasons),
            "provenance": {"snapshots": provenance},
        }


def normalize_item(connection, source: dict[str, Any], item: RawItem) -> NormalizedCandidate:
    """Normalizuje jednu položku; nejasnosti vyjádří stabilními kódy."""
    raw = item.to_dict()
    reasons: list[str] = []

    title = clean_text(item.title)
    if not title:
        reasons.append("missing-title")

    start_at, start_all_day = _normalize_datetime(item.start_at, item.date_text)
    if not start_at:
        reasons.append("missing-or-invalid-start")
    end_at, _ = _normalize_datetime(item.end_at, None)
    if item.end_at and not end_at:
        reasons.append("invalid-end")
    if start_at and end_at and datetime.fromisoformat(end_at) < datetime.fromisoformat(start_at):
        reasons.append("end-before-start")

    canonical_url, url_reason = _normalize_item_url(item.url, source.get("url"))
    if url_reason:
        reasons.append(url_reason)

    municipality = _normalize_municipality(
        connection, item.municipality, source.get("municipality_name"))
    if municipality["municipality_id"] is None:
        reasons.append(municipality["reason"] or "unresolved-municipality")

    price, price_reason = normalize_price(item.price_text)
    if price_reason:
        reasons.append(price_reason)

    normalized = {
        "title": title,
        "description": clean_text(item.description),
        "start_at": start_at,
        "end_at": end_at,
        "all_day": bool(item.all_day if item.all_day is not None else start_all_day),
        "venue": clean_text(item.venue),
        "address": clean_text(item.address),
        "municipality": municipality["municipality_name"],
        "municipality_id": municipality["municipality_id"],
        "municipality_input": municipality["input"],
        "municipality_match": municipality["method"],
        "price": price,
        "canonical_url": canonical_url,
        "categories": list(dict.fromkeys(item.categories)),
        "organizers": [value for value in (clean_text(v) for v in item.organizers) if value],
        "recurring": item.recurring,
    }
    fingerprint = {
        "source_id": source["id"],
        "uid": clean_text(item.uid),
        "url": canonical_url,
        "title": title,
        "start_at": start_at,
        # U nečitelných položek zabrání raw payload kolizi prázdných hodnot.
        "raw_fallback": raw if not any((item.uid, canonical_url, title, start_at)) else None,
    }
    digest = hashlib.sha256(_canonical_json(fingerprint).encode("utf-8")).hexdigest()[:24]
    return NormalizedCandidate(
        candidate_id=f"adapter-{source['id']}-{digest}",
        raw=raw,
        normalized=normalized,
        quarantine_reasons=tuple(dict.fromkeys(reasons)),
    )


def normalize_price(value: str | None) -> tuple[dict[str, Any], str | None]:
    """Vrátí typ/text/jednoznačnou celočíselnou cenu v Kč."""
    text = clean_text(value)
    if not text:
        return {"type": "unknown", "text": None, "amount": None, "currency": None}, None

    free = bool(_FREE.search(_ascii_fold(text)))
    amounts = []
    for match in _CZK_AMOUNT.finditer(text):
        amount = int(re.sub(r"[ .]", "", match.group(1)))
        if amount not in amounts:
            amounts.append(amount)
    has_czk = bool(_CZK.search(text))
    nonsingular = bool(_PRICE_RANGE.search(text) or _DECIMAL_CZK.search(text))
    if free and has_czk:
        return {
            "type": "unknown", "text": text, "amount": None, "currency": None,
        }, "conflicting-price"
    if free:
        return {"type": "free", "text": text, "amount": 0, "currency": "CZK"}, None
    if has_czk:
        return {
            "type": "paid", "text": text,
            "amount": amounts[0] if len(amounts) == 1 and not nonsingular else None,
            "currency": "CZK",
        }, None
    return {"type": "unknown", "text": text, "amount": None, "currency": None}, None


def snapshot_provenance(fetches) -> list[dict[str, Any]]:
    """Stabilní provenance bez cest a času konkrétního běhu."""
    values = {
        (fetched.url, fetched.snapshot.content_hash,
         fetched.snapshot.request_label): {
            "url": fetched.url,
            "content_hash": fetched.snapshot.content_hash,
            "request_label": fetched.snapshot.request_label,
        }
        for fetched in fetches if fetched.snapshot is not None
    }
    return [values[key] for key in sorted(values, key=lambda value: tuple(v or "" for v in value))]


def _normalize_datetime(value: str | None, fallback: str | None) -> tuple[str | None, bool | None]:
    normalized, all_day = parse_iso_datetime(value)
    if normalized:
        return normalized, all_day
    if not fallback:
        return None, None
    match = _CZECH_DATE.fullmatch(fallback)
    if not match:
        return None, None
    day, month, year, hour, minute = match.groups()
    try:
        parsed = datetime(
            int(year), int(month), int(day), int(hour or 0), int(minute or 0), tzinfo=TZ)
    except ValueError:
        return None, None
    return parsed.isoformat(), hour is None


def _normalize_item_url(item_url: str | None, source_url: str | None) -> tuple[str | None, str | None]:
    raw = clean_text(item_url)
    fallback = clean_text(source_url)
    if not raw:
        if not fallback:
            return None, "missing-url"
        try:
            return normalize_url(fallback), "missing-item-url"
        except InvalidUrl:
            return None, "invalid-url"
    try:
        absolute = urljoin(fallback or "", raw)
        return normalize_url(absolute), None
    except InvalidUrl:
        return None, "invalid-url"


def _normalize_municipality(connection, item_value, source_value) -> dict[str, Any]:
    item_text = clean_text(item_value)
    source_text = clean_text(source_value)
    selected = item_text or source_text
    method_prefix = "item" if item_text else "source"
    if not selected:
        return {"input": None, "municipality_id": None, "municipality_name": None,
                "method": None, "reason": "missing-municipality"}

    key = _fold(selected)
    matches: dict[int, tuple[str, str]] = {}
    # Alias text není ve sjednoceném SELECTu; načte se zvlášť, aby porovnání
    # bylo stejné pro diakritiku, velikost písmen i vícenásobné mezery.
    for row in connection.execute("SELECT id, name FROM municipality"):
        if _fold(row["name"]) == key:
            matches[int(row["id"])] = (row["name"], "canonical")
    for row in connection.execute(
            "SELECT a.alias, m.id, m.name FROM municipality_alias a "
            "JOIN municipality m ON m.id = a.municipality_id"):
        if _fold(row["alias"]) == key:
            matches[int(row["id"])] = (row["name"], "alias")
    if len(matches) == 1:
        municipality_id, (name, kind) = next(iter(matches.items()))
        return {"input": selected, "municipality_id": municipality_id,
                "municipality_name": name, "method": f"{method_prefix}-{kind}",
                "reason": None}
    return {"input": selected, "municipality_id": None, "municipality_name": None,
            "method": None,
            "reason": "ambiguous-municipality" if matches else "unknown-municipality"}


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return " ".join("".join(
        char for char in normalized if not unicodedata.combining(char)).split())


def _ascii_fold(value: str) -> str:
    return _fold(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
