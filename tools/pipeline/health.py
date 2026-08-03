#!/usr/bin/env python3
"""Sledování zdraví zdrojů (ADR 0004, architektura oddíl 5).

Zdroj přestane dodávat data zpravidla tiše: nespadne, vrátí 200, jen se
změní jeho struktura a adaptér vrátí prázdno. Tento modul takový stav
zviditelní — a zároveň se snaží nekřičet tam, kde se nic nestalo.

Tři signály se vyhodnocují **odděleně a nikdy se neslévají** do jediného
„zdroj nefunguje“:

  * stažení   — návratový kód, chyba, počet selhání v řadě,
  * extrakce  — počet položek proti baseline zdroje, vyplnění klíčových polí,
  * čerstvost — mění se obsah a existuje budoucí termín?

Bez tohoto rozdělení nelze odlišit odpověď 200 s prázdnou stránkou od
redesignu a od zdroje, který legitimně nemá program.

Druhé nosné pravidlo: **nulová výtěžnost sama o sobě chyba není.** Prahy
jsou relativní k historii konkrétního zdroje. Zdroj s baseline pod
`LOW_BASELINE_ITEMS` se podle objemu nehodnotí vůbec; u malé obce je
prázdný srpen normální stav, ne poplach.

Spuštění:

    python3 tools/pipeline/health.py report [--json]
    python3 tools/pipeline/health.py evaluate [--source ID] [--json]
    python3 tools/pipeline/health.py cancellations [--apply] [--json]
    python3 tools/pipeline/health.py migrate-facebook [--dry-run]
    python3 tools/pipeline/health.py thresholds [--json]

Modul používá jen standardní knihovnu.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402

REPO_ROOT = db.REPO_ROOT


# ---------------------------------------------------------------------------
# PRAHY
# ---------------------------------------------------------------------------
#
# Všechny prahy jsou na jednom místě záměrně. Jsou to **počáteční odhady
# určené ke kalibraci na skutečných datech**, ne ověřené konstanty
# (architektura, otevřená otázka 1). Balíček, který je změní, má povinnost
# zaznamenat, na jakých datech je ověřil a s jakým výsledkem.
#
# Hodnoty pocházejí z tabulky v `docs/phase-2-architecture.md`, oddíl 5.

#: Kolik selhání stažení v řadě znamená `broken`.
BROKEN_CONSECUTIVE_FAILURES = 3

#: Baseline pod touto hranicí = zdroj se podle objemu nehodnotí vůbec.
#: Nejcitlivější práh celého modulu. Chrání obce, které normálně nemají
#: co nabídnout, před falešnými poplachy.
LOW_BASELINE_ITEMS = 3

#: `degraded`, když počet položek klesne pod tento násobek baseline.
DEGRADED_ITEMS_RATIO = 0.4

#: `schema-drift`, když vyplnění klíčového pole klesne pod tento násobek
#: baseline téhož pole.
SCHEMA_DRIFT_FILL_RATIO = 0.8

#: `stale`, když se obsah nezměnil déle než tolik dní a zdroj nemá
#: žádný budoucí termín.
STALE_UNCHANGED_DAYS = 30

#: Kolikrát po sobě musí akce chybět ve zdravém stažení, aby se směla
#: odvodit jako zrušená. ADR 0004: zmizení z výpisu není důkaz.
CANCELLATION_MISSING_RUNS = 2

#: Z kolika posledních nenulových běhů se počítá `baseline_items_median`.
BASELINE_WINDOW_RUNS = 10

#: Kolik posledních záznamů `source_fetch` se vůbec načítá do historie.
HISTORY_LOOKBACK_RUNS = 60

#: Násobek `source.check_interval_days`, po kterém je kontrola po termínu.
#: U zdrojů s nízkým baseline je tohle spolu s úspěšností stažení jediné,
#: co se sleduje.
OVERDUE_INTERVAL_FACTOR = 2.0

#: Klíčová pole pro detekci `schema-drift`.
KEY_FIELDS = ("start_at", "title", "canonical_url")


def thresholds() -> dict:
    """Prahy v strojově čitelné podobě. Jediný zdroj pravdy jsou konstanty výše."""
    return {
        "broken_consecutive_failures": BROKEN_CONSECUTIVE_FAILURES,
        "low_baseline_items": LOW_BASELINE_ITEMS,
        "degraded_items_ratio": DEGRADED_ITEMS_RATIO,
        "schema_drift_fill_ratio": SCHEMA_DRIFT_FILL_RATIO,
        "stale_unchanged_days": STALE_UNCHANGED_DAYS,
        "cancellation_missing_runs": CANCELLATION_MISSING_RUNS,
        "baseline_window_runs": BASELINE_WINDOW_RUNS,
        "history_lookback_runs": HISTORY_LOOKBACK_RUNS,
        "overdue_interval_factor": OVERDUE_INTERVAL_FACTOR,
        "key_fields": list(KEY_FIELDS),
        "calibrated": False,
    }


# ---------------------------------------------------------------------------
# Stavy
# ---------------------------------------------------------------------------

STATE_HEALTHY = "healthy"
STATE_SUSPECT = "suspect"
STATE_DEGRADED = "degraded"
STATE_SCHEMA_DRIFT = "schema-drift"
STATE_STALE = "stale"
STATE_BROKEN = "broken"

#: Zdroj bez jediného záznamu o stažení. Není to stav v databázi (schéma ho
#: nezná), jen zobrazení v reportu.
STATE_UNCHECKED = "unchecked"

#: Pořadí pro report: nahoře to, kde se ztrácí nejvíc dat, tedy fronta
#: k opravě adaptéru.
STATE_SEVERITY = {
    STATE_BROKEN: 0,        # nestáhne se vůbec nic
    STATE_SUSPECT: 1,       # stáhne se, nevytěží se nic
    STATE_SCHEMA_DRIFT: 2,  # položky jsou, klíčová pole chybí
    STATE_DEGRADED: 3,      # položek je výrazně méně
    STATE_STALE: 4,         # obsah se dlouho nemění, nic budoucího
    STATE_HEALTHY: 5,
    STATE_UNCHECKED: 6,
}


# ---------------------------------------------------------------------------
# Čas
# ---------------------------------------------------------------------------

def _local_tz():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Europe/Prague")
    except Exception:  # noqa: BLE001 — v holém kontejneru nemusí být tzdata
        return datetime.now().astimezone().tzinfo or timezone.utc


LOCAL_TZ = _local_tz()


def _parse_ts(value) -> datetime | None:
    """Přečte ISO timestamp i holé datum. Nic nedomýšlí, při neúspěchu vrací None."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=LOCAL_TZ)

    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=LOCAL_TZ)


def _now(now=None) -> datetime:
    if now is None:
        return datetime.now(LOCAL_TZ)
    parsed = _parse_ts(now)
    if parsed is None:
        raise ValueError(f"Nesrozumitelný časový údaj: {now!r}")
    return parsed


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _days_between(earlier, later) -> int | None:
    start, end = _parse_ts(earlier), _parse_ts(later)
    if start is None or end is None:
        return None
    return (end - start).days


# ---------------------------------------------------------------------------
# Zápis surových měření
# ---------------------------------------------------------------------------
#
# Tenké obálky nad `source_fetch` a `source_extract`. Fetch vrstva smí psát
# i přímo; podstatné je, že se zaznamená **každý pokus, i neúspěšný** —
# bez neúspěšných záznamů nelze spočítat `consecutive_failures`.

def record_fetch(connection, source_id: str, *, fetched_at=None, http_status=None,
                 etag=None, content_hash=None, bytes_=None, duration_ms=None,
                 error=None) -> int:
    moment = _iso(_now(fetched_at))
    cursor = connection.execute(
        "INSERT INTO source_fetch ("
        "  source_id, fetched_at, http_status, etag, content_hash, bytes,"
        "  duration_ms, error"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (source_id, moment, http_status, etag, content_hash, bytes_,
         duration_ms, error),
    )
    return int(cursor.lastrowid)


def record_extract(connection, fetch_id: int, *, items_found: int, items_valid: int,
                   items_unparsed: int = 0, fill_rates: dict | None = None) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO source_extract ("
        "  fetch_id, items_found, items_valid, items_unparsed, fill_rates"
        ") VALUES (?, ?, ?, ?, ?)",
        (fetch_id, items_found, items_valid, items_unparsed,
         json.dumps(fill_rates or {}, ensure_ascii=False, sort_keys=True)),
    )


def fill_rates_from_items(items, fields=KEY_FIELDS) -> dict:
    """Podíl položek, které mají klíčové pole vyplněné.

    Prázdný vstup vrací prázdný slovník, ne samé nuly. Nula položek není
    důkaz o tom, že se pole přestalo plnit — to je jiný signál.
    """
    items = list(items or [])
    if not items:
        return {}
    rates = {}
    for name in fields:
        filled = sum(
            1 for item in items
            if (item.get(name) if isinstance(item, dict) else getattr(item, name, None))
            not in (None, "", [], {})
        )
        rates[name] = round(filled / len(items), 4)
    return rates


# ---------------------------------------------------------------------------
# Signály
# ---------------------------------------------------------------------------

@dataclass
class FetchSignal:
    """Signál stažení. Odpovídá zdroj vůbec?"""

    checked_at: str | None = None
    ok: bool | None = None            # None = neexistuje záznam o stažení
    http_status: int | None = None
    error: str | None = None
    consecutive_failures: int = 0
    last_success_at: str | None = None
    runs_known: int = 0


@dataclass
class ExtractSignal:
    """Signál extrakce. Vytěžilo se z odpovědi něco použitelného?"""

    items_found: int | None = None
    items_valid: int | None = None
    items_unparsed: int | None = None
    fill_rates: dict = field(default_factory=dict)
    baseline_items_median: float | None = None
    baseline_fill_rates: dict = field(default_factory=dict)
    baseline_runs: int = 0
    volume_evaluated: bool = False    # False = nízký baseline, objem se neřeší
    drifted_fields: list = field(default_factory=list)


@dataclass
class FreshnessSignal:
    """Signál čerstvosti. Mění se obsah a je na co se těšit?"""

    content_hash: str | None = None
    unchanged_days: int | None = None
    unchanged_runs: int = 0
    has_future_events: bool = False
    last_item_at: str | None = None


@dataclass
class Health:
    source_id: str
    state: str
    reason: str
    evaluated_at: str
    fetch: FetchSignal
    extract: ExtractSignal
    freshness: FreshnessSignal

    def to_dict(self) -> dict:
        return asdict(self)


def _history(connection, source_id: str, limit: int = HISTORY_LOOKBACK_RUNS) -> list[dict]:
    """Posledních N stažení zdroje, od nejnovějšího, s výsledkem extrakce."""
    rows = connection.execute(
        "SELECT f.id, f.fetched_at, f.http_status, f.content_hash, f.error,"
        "       e.items_found, e.items_valid, e.items_unparsed, e.fill_rates "
        "FROM source_fetch f "
        "LEFT JOIN source_extract e ON e.fetch_id = f.id "
        "WHERE f.source_id = ? "
        "ORDER BY f.fetched_at DESC, f.id DESC "
        "LIMIT ?",
        (source_id, limit),
    ).fetchall()

    history = []
    for row in rows:
        try:
            rates = json.loads(row["fill_rates"]) if row["fill_rates"] else {}
        except (TypeError, ValueError):
            rates = {}
        history.append({
            "id": row["id"],
            "fetched_at": row["fetched_at"],
            "http_status": row["http_status"],
            "content_hash": row["content_hash"],
            "error": row["error"],
            "items_found": row["items_found"],
            "items_valid": row["items_valid"],
            "items_unparsed": row["items_unparsed"],
            "fill_rates": rates if isinstance(rates, dict) else {},
            "ok": _is_success(row["error"], row["http_status"]),
        })
    return history


def _is_success(error, http_status) -> bool:
    """Úspěch = bez chyby a s kódem, který něco vrátil (včetně 304)."""
    if error:
        return False
    if http_status is None:
        return False
    return 200 <= int(http_status) < 400


def _fetch_signal(history: list[dict]) -> FetchSignal:
    if not history:
        return FetchSignal()

    latest = history[0]
    failures = 0
    for run in history:
        if run["ok"]:
            break
        failures += 1

    last_success = next((run["fetched_at"] for run in history if run["ok"]), None)
    return FetchSignal(
        checked_at=latest["fetched_at"],
        ok=latest["ok"],
        http_status=latest["http_status"],
        error=latest["error"],
        consecutive_failures=failures,
        last_success_at=last_success,
        runs_known=len(history),
    )


def _baseline_items(previous: list[dict]) -> tuple[float | None, int]:
    """Medián posledních `BASELINE_WINDOW_RUNS` **nenulových** běhů.

    Nulové běhy se do baseline nezapočítávají schválně: jinak by se zdroj,
    který jednou vypadne, sám sobě snížil laťku a příště by prázdný výsledek
    prošel jako normální.
    """
    counts = [
        run["items_found"] for run in previous
        if run["items_found"] is not None and run["items_found"] > 0
    ][:BASELINE_WINDOW_RUNS]
    if not counts:
        return None, 0
    return float(statistics.median(counts)), len(counts)


def _baseline_fill(previous: list[dict]) -> dict:
    """Medián vyplnění klíčových polí přes běhy, ve kterých vůbec něco bylo."""
    usable = [
        run for run in previous
        if run["items_found"] and run["items_found"] > 0 and run["fill_rates"]
    ][:BASELINE_WINDOW_RUNS]

    baseline = {}
    for name in KEY_FIELDS:
        values = [
            float(run["fill_rates"][name]) for run in usable
            if isinstance(run["fill_rates"].get(name), (int, float))
        ]
        if values:
            baseline[name] = float(statistics.median(values))
    return baseline


def _extract_signal(history: list[dict], stored: dict | None) -> ExtractSignal:
    stored = stored or {}
    latest = history[0] if history else None
    previous = history[1:]

    baseline, baseline_runs = _baseline_items(previous)
    if baseline is None and stored.get("baseline_items_median") is not None:
        # Bez vlastní historie se použije nasazený baseline — typicky seed
        # z `config/facebook-sources.json` (viz migrate_facebook_health).
        baseline = float(stored["baseline_items_median"])

    baseline_fill = _baseline_fill(previous)
    if not baseline_fill and stored.get("baseline_fill_rates"):
        try:
            parsed = json.loads(stored["baseline_fill_rates"])
            if isinstance(parsed, dict):
                baseline_fill = {
                    key: float(value) for key, value in parsed.items()
                    if isinstance(value, (int, float))
                }
        except (TypeError, ValueError):
            baseline_fill = {}

    signal = ExtractSignal(
        baseline_items_median=baseline,
        baseline_fill_rates=baseline_fill,
        baseline_runs=baseline_runs,
        volume_evaluated=baseline is not None and baseline >= LOW_BASELINE_ITEMS,
    )
    if latest is None:
        return signal

    signal.items_found = latest["items_found"]
    signal.items_valid = latest["items_valid"]
    signal.items_unparsed = latest["items_unparsed"]
    signal.fill_rates = latest["fill_rates"]

    # Drift se posuzuje jen tam, kde vůbec nějaké položky jsou. Nula položek
    # je jiný signál a fill rate z nuly položek nic neznamená.
    if latest["items_found"]:
        for name in KEY_FIELDS:
            base = baseline_fill.get(name)
            current = latest["fill_rates"].get(name)
            if base is None or current is None or base <= 0:
                continue
            if float(current) < SCHEMA_DRIFT_FILL_RATIO * base:
                signal.drifted_fields.append(name)

    return signal


def _freshness_signal(connection, source_id: str, history: list[dict],
                      now: datetime, stored: dict | None) -> FreshnessSignal:
    stored = stored or {}
    signal = FreshnessSignal(last_item_at=stored.get("last_item_at"))

    last_with_items = next(
        (run["fetched_at"] for run in history
         if run["items_found"] is not None and run["items_found"] > 0), None)
    if last_with_items:
        signal.last_item_at = last_with_items

    # Budoucí termín: stačí, že akce hlášená tímto zdrojem ještě neskončila.
    # Porovnává se na dny, protože časové zóny v datech nejsou jednotné.
    today = now.date().isoformat()
    row = connection.execute(
        "SELECT count(*) AS n FROM event_source es "
        "JOIN event e ON e.id = es.event_id "
        "WHERE es.source_id = ? AND e.cancelled = 0 "
        "  AND substr(coalesce(e.end_at, e.start_at), 1, 10) >= ?",
        (source_id, today),
    ).fetchone()
    signal.has_future_events = bool(row and row["n"])

    if not history:
        return signal

    current_hash = history[0]["content_hash"]
    signal.content_hash = current_hash
    if not current_hash:
        return signal

    streak = []
    for run in history:
        if run["content_hash"] != current_hash:
            break
        streak.append(run)
    signal.unchanged_runs = len(streak)
    if len(streak) >= 2:
        # Měří se rozpětí mezi prvním a posledním výskytem téhož obsahu, ne
        # doba od poslední kontroly. Dlouho nekontrolovaný zdroj není `stale`,
        # to je jiný problém (viz `check_overdue` v reportu).
        signal.unchanged_days = _days_between(
            streak[-1]["fetched_at"], streak[0]["fetched_at"])
    return signal


def _stored_health(connection, source_id: str) -> dict | None:
    row = connection.execute(
        "SELECT * FROM source_health WHERE source_id = ?", (source_id,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Vyhodnocení stavu
# ---------------------------------------------------------------------------

def decide(fetch: FetchSignal, extract: ExtractSignal,
           freshness: FreshnessSignal) -> tuple[str, str]:
    """Ze tří signálů odvodí stav. Pořadí podmínek je z architektury, oddíl 5.

    Signály zůstávají oddělené i po tomto kroku — stav je jen jejich shrnutí
    pro report, ne náhrada. Kdo potřebuje vědět, *co* se pokazilo, čte signály.
    """
    if fetch.ok is None:
        return STATE_HEALTHY, "bez záznamu o stažení, není co hodnotit"

    if fetch.consecutive_failures >= BROKEN_CONSECUTIVE_FAILURES:
        return STATE_BROKEN, (
            f"{fetch.consecutive_failures}× po sobě selhalo stažení "
            f"(práh {BROKEN_CONSECUTIVE_FAILURES})")

    if extract.volume_evaluated:
        baseline = extract.baseline_items_median or 0.0
        if extract.items_found == 0:
            return STATE_SUSPECT, (
                f"stažení prošlo, ale extrakce vrátila 0 položek proti "
                f"baseline {baseline:g}")
        if (extract.items_found is not None
                and extract.items_found < DEGRADED_ITEMS_RATIO * baseline):
            return STATE_DEGRADED, (
                f"{extract.items_found} položek proti baseline {baseline:g} "
                f"(práh {DEGRADED_ITEMS_RATIO:g}×)")

    if extract.drifted_fields:
        return STATE_SCHEMA_DRIFT, (
            "přestala se plnit klíčová pole: " + ", ".join(extract.drifted_fields))

    if (freshness.unchanged_days is not None
            and freshness.unchanged_days > STALE_UNCHANGED_DAYS
            and not freshness.has_future_events):
        return STATE_STALE, (
            f"obsah beze změny {freshness.unchanged_days} dní a žádný budoucí termín")

    if not extract.volume_evaluated and extract.items_found == 0:
        # Výslovně pojmenovaný nepoplach. Malá obec v srpnu nic nemá a je to
        # normální stav; sleduje se u ní jen stažení a interval kontroly.
        return STATE_HEALTHY, (
            "0 položek, ale nízký baseline "
            f"(< {LOW_BASELINE_ITEMS}) — podle objemu se nehodnotí")

    return STATE_HEALTHY, "stažení i extrakce odpovídají baseline"


def evaluate(connection, source_id: str, *, now=None, persist: bool = True) -> Health:
    """Vyhodnotí zdraví jednoho zdroje z uložené historie a zapíše `source_health`."""
    moment = _now(now)
    stored = _stored_health(connection, source_id)
    history = _history(connection, source_id)

    fetch = _fetch_signal(history)
    extract = _extract_signal(history, stored)
    freshness = _freshness_signal(connection, source_id, history, moment, stored)
    state, reason = decide(fetch, extract, freshness)

    health = Health(
        source_id=source_id, state=state, reason=reason,
        evaluated_at=_iso(moment), fetch=fetch, extract=extract, freshness=freshness,
    )
    if persist:
        _persist(connection, health, stored, moment)
    return health


def _persist(connection, health: Health, stored: dict | None, moment: datetime) -> None:
    previous_state = (stored or {}).get("state")
    first_alerted_at = (stored or {}).get("first_alerted_at")
    if health.state == STATE_HEALTHY:
        first_alerted_at = None
    elif not first_alerted_at or previous_state == STATE_HEALTHY:
        first_alerted_at = _iso(moment)

    baseline_fill = (
        json.dumps(health.extract.baseline_fill_rates, ensure_ascii=False,
                   sort_keys=True)
        if health.extract.baseline_fill_rates else None
    )
    # Bez jediného záznamu o stažení není co říct; poznámka z migrace nebo od
    # člověka je pak cennější než konstatování, že se nic neví.
    note = health.reason if health.fetch.ok is not None else None

    connection.execute(
        "INSERT INTO source_health ("
        "  source_id, state, consecutive_failures, last_checked_at, last_success_at,"
        "  last_item_at, baseline_items_median, baseline_fill_rates,"
        "  first_alerted_at, note"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(source_id) DO UPDATE SET "
        "  state = excluded.state,"
        "  consecutive_failures = excluded.consecutive_failures,"
        "  last_checked_at = coalesce(excluded.last_checked_at, source_health.last_checked_at),"
        "  last_success_at = coalesce(excluded.last_success_at, source_health.last_success_at),"
        "  last_item_at = coalesce(excluded.last_item_at, source_health.last_item_at),"
        "  baseline_items_median = coalesce(excluded.baseline_items_median,"
        "                                   source_health.baseline_items_median),"
        "  baseline_fill_rates = coalesce(excluded.baseline_fill_rates,"
        "                                 source_health.baseline_fill_rates),"
        "  first_alerted_at = excluded.first_alerted_at,"
        "  note = coalesce(excluded.note, source_health.note)",
        (
            health.source_id, health.state, health.fetch.consecutive_failures,
            health.fetch.checked_at, health.fetch.last_success_at,
            health.freshness.last_item_at, health.extract.baseline_items_median,
            baseline_fill, first_alerted_at, note,
        ),
    )
    connection.commit()


def evaluate_all(connection, *, now=None, persist: bool = True) -> list[Health]:
    """Vyhodnotí všechny zdroje, o kterých už něco víme."""
    rows = connection.execute(
        "SELECT id FROM source WHERE id IN ("
        "  SELECT source_id FROM source_fetch "
        "  UNION SELECT source_id FROM source_health) "
        "ORDER BY id"
    ).fetchall()
    return [evaluate(connection, row["id"], now=now, persist=persist) for row in rows]


# ---------------------------------------------------------------------------
# Karanténa a odvození zrušení
# ---------------------------------------------------------------------------

def mark_missing(connection, source_id: str, seen_urls, *, now=None,
                 force: bool = False) -> dict:
    """Zaznamená, které akce zdroj v tomto běhu ještě hlásil a které ne.

    Počítadlo `missing_runs` se zvyšuje **jen u zdravého zdroje**. U rozbitého
    adaptéru by prázdný výpis počítadlo nafoukl a za dva běhy by se odvodilo
    zrušení z vlastní chyby. To je přesně ten způsob, jakým se monitoring mění
    v generátor nepravd.
    """
    moment = _iso(_now(now))
    stored = _stored_health(connection, source_id)
    state = (stored or {}).get("state", STATE_HEALTHY)
    if state != STATE_HEALTHY and not force:
        return {"source_id": source_id, "state": state, "skipped": True,
                "seen": 0, "missing": 0}

    seen = {url for url in (seen_urls or []) if url}
    rows = connection.execute(
        "SELECT event_id, url, missing_runs FROM event_source WHERE source_id = ?",
        (source_id,),
    ).fetchall()

    seen_count = missing_count = 0
    for row in rows:
        if row["url"] in seen:
            connection.execute(
                "UPDATE event_source SET missing_runs = 0, last_seen_at = ? "
                "WHERE event_id = ? AND url = ?",
                (moment, row["event_id"], row["url"]),
            )
            seen_count += 1
        else:
            connection.execute(
                "UPDATE event_source SET missing_runs = missing_runs + 1 "
                "WHERE event_id = ? AND url = ?",
                (row["event_id"], row["url"]),
            )
            missing_count += 1
    connection.commit()
    return {"source_id": source_id, "state": state, "skipped": False,
            "seen": seen_count, "missing": missing_count}


def fetched_window(connection, source_id: str, now: datetime) -> tuple[str, str] | None:
    """Okno, o kterém poslední běh zdroje vůbec něco vypovídá.

    Dolní hranicí je dnešek — o proběhlých akcích zdroj nic netvrdí. Horní
    hranicí je nejzazší termín, který zdroj v posledním běhu ještě hlásil.
    Za tuto hranici výpis nedohlédl (stránkování, ořez na nejbližší akce),
    takže nepřítomnost akce tam není informace.

    Vrací `None`, když zdroj nehlásí nic — pak okno neexistuje a nesmí se
    odvodit vůbec nic.
    """
    row = connection.execute(
        "SELECT max(substr(coalesce(e.end_at, e.start_at), 1, 10)) AS horizon "
        "FROM event_source es JOIN event e ON e.id = es.event_id "
        "WHERE es.source_id = ? AND es.missing_runs = 0",
        (source_id,),
    ).fetchone()
    horizon = row["horizon"] if row else None
    if not horizon:
        return None
    today = now.date().isoformat()
    if horizon < today:
        return None
    return today, horizon


def derive_cancellations(connection, source_id: str | None = None, *, now=None,
                         window: tuple[str, str] | None = None,
                         apply: bool = True) -> list[dict]:
    """Odvodí zrušení akcí podle ADR 0004.

    Všechny tři podmínky musí platit současně:

    1. zdroj je `healthy`,
    2. termín akce spadal do staženého okna,
    3. `event_source.missing_runs >= CANCELLATION_MISSING_RUNS`.

    Jinak se akce ponechá beze změny. Zmizení z výpisu není důkaz.

    Vrací rozhodnutí i pro akce, které zrušené nebyly, včetně důvodu — bez
    toho se nedá ověřit, že se monitoring drží na uzdě.
    """
    moment = _now(now)
    if source_id:
        source_ids = [source_id]
    else:
        source_ids = [
            row["source_id"] for row in connection.execute(
                "SELECT DISTINCT source_id FROM event_source "
                "WHERE source_id IS NOT NULL AND missing_runs > 0 ORDER BY source_id")
        ]

    decisions: list[dict] = []
    for current in source_ids:
        stored = _stored_health(connection, current)
        state = (stored or {}).get("state")
        source_window = window or fetched_window(connection, current, moment)

        rows = connection.execute(
            "SELECT es.event_id, es.url, es.missing_runs, e.start_at, e.end_at,"
            "       e.cancelled, e.title "
            "FROM event_source es JOIN event e ON e.id = es.event_id "
            "WHERE es.source_id = ? AND es.missing_runs > 0 AND e.cancelled = 0 "
            "ORDER BY es.event_id",
            (current,),
        ).fetchall()

        for row in rows:
            decision = {
                "event_id": row["event_id"], "source_id": current,
                "title": row["title"], "missing_runs": row["missing_runs"],
                "state": state, "window": list(source_window) if source_window else None,
                "cancelled": False, "reason": "",
            }
            event_day = (row["start_at"] or "")[:10]

            if state != STATE_HEALTHY:
                decision["reason"] = (
                    f"zdroj není healthy (je {state or 'bez záznamu'}) — "
                    "chybějící akce může být chyba zdroje, ne zrušení")
            elif source_window is None:
                decision["reason"] = "stažené okno nelze určit, zdroj nehlásí nic"
            elif not (source_window[0] <= event_day <= source_window[1]):
                decision["reason"] = (
                    f"termín {event_day} je mimo stažené okno "
                    f"{source_window[0]}–{source_window[1]}")
            elif row["missing_runs"] < CANCELLATION_MISSING_RUNS:
                decision["reason"] = (
                    f"chybí jen {row['missing_runs']}× "
                    f"(práh {CANCELLATION_MISSING_RUNS})")
            else:
                decision["cancelled"] = True
                decision["reason"] = (
                    f"zdravý zdroj, termín v okně, chybí {row['missing_runs']}× po sobě")
                if apply:
                    connection.execute(
                        "UPDATE event SET cancelled = 1 WHERE id = ?", (row["event_id"],))

            decisions.append(decision)

    if apply:
        connection.commit()
    return decisions


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def report_rows(connection, *, now=None) -> list[dict]:
    """Přehled `stav zdroje × dnů od poslední položky`, seřazený k opravě."""
    moment = _now(now)
    rows = connection.execute(
        "SELECT s.id AS source_id, s.name, s.priority, s.check_interval_days,"
        "       s.enabled, h.state, h.consecutive_failures, h.last_checked_at,"
        "       h.last_success_at, h.last_item_at, h.baseline_items_median,"
        "       h.first_alerted_at, h.note "
        "FROM source s LEFT JOIN source_health h ON h.source_id = s.id"
    ).fetchall()

    result = []
    for row in rows:
        state = row["state"] or STATE_UNCHECKED
        baseline = row["baseline_items_median"]
        interval = row["check_interval_days"] or 0
        days_since_check = _days_between(row["last_checked_at"], moment)
        result.append({
            "source_id": row["source_id"],
            "name": row["name"],
            "state": state,
            "priority": row["priority"],
            "enabled": bool(row["enabled"]),
            "days_since_last_item": _days_between(row["last_item_at"], moment),
            "days_since_check": days_since_check,
            "check_interval_days": interval,
            "check_overdue": bool(
                days_since_check is not None and interval
                and days_since_check > interval * OVERDUE_INTERVAL_FACTOR),
            "consecutive_failures": row["consecutive_failures"] or 0,
            "baseline_items_median": baseline,
            "volume_evaluated": baseline is not None and baseline >= LOW_BASELINE_ITEMS,
            "last_checked_at": row["last_checked_at"],
            "last_item_at": row["last_item_at"],
            "first_alerted_at": row["first_alerted_at"],
            "note": row["note"],
        })

    result.sort(key=lambda item: (
        STATE_SEVERITY.get(item["state"], 99),
        -(item["days_since_last_item"]
          if item["days_since_last_item"] is not None else -1),
        item["source_id"],
    ))
    return result


def render_report(rows: list[dict], *, now=None) -> str:
    moment = _now(now)
    header = ("stav", "zdroj", "dnů od položky", "dnů od kontroly", "selhání",
              "baseline", "objem")
    table = [header]
    for row in rows:
        table.append((
            row["state"],
            row["source_id"],
            "—" if row["days_since_last_item"] is None else str(row["days_since_last_item"]),
            ("—" if row["days_since_check"] is None
             else str(row["days_since_check"]) + ("!" if row["check_overdue"] else "")),
            str(row["consecutive_failures"] or ""),
            ("—" if row["baseline_items_median"] is None
             else f"{row['baseline_items_median']:g}"),
            "ano" if row["volume_evaluated"] else "ne",
        ))

    widths = [max(len(line[i]) for line in table) for i in range(len(header))]
    lines = [
        f"Zdraví zdrojů k {_iso(moment)} "
        f"(prahy nejsou zkalibrované, viz thresholds())",
        "",
    ]
    for index, line in enumerate(table):
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(line)).rstrip())
        if index == 0:
            lines.append("  ".join("-" * width for width in widths))

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    summary = ", ".join(
        f"{state}: {counts[state]}"
        for state in sorted(counts, key=lambda s: STATE_SEVERITY.get(s, 99))
    )
    lines += ["", f"Celkem {len(rows)} zdrojů — {summary or 'nic'}."]

    queue = [row for row in rows
             if row["state"] in (STATE_BROKEN, STATE_SUSPECT, STATE_SCHEMA_DRIFT,
                                 STATE_DEGRADED)]
    if queue:
        lines.append("Fronta k opravě adaptéru: "
                     + ", ".join(row["source_id"] for row in queue))
    overdue = [row for row in rows if row["check_overdue"]]
    if overdue:
        lines.append("Po termínu kontroly (!): "
                     + ", ".join(row["source_id"] for row in overdue))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Jednorázová migrace stavu z konfigurace
# ---------------------------------------------------------------------------

CONFIG_FIELDS_TO_REMOVE = ("verified_at", "upcoming_events_at_check")

MIGRATION_HINT = (
    "Hotovo v databázi. Pokud starý `config/facebook-sources.json` ještě pole "
    + " a ".join(f"`{name}`" for name in CONFIG_FIELDS_TO_REMOVE)
    + " obsahuje, je potřeba je odebrat — konfigurace patří člověku a provozní "
      "stav do ní nepatří (architektura, „Konfigurace proti stavu“). Aktuální "
      "schéma tato pole už zakazuje; skript zůstává jen pro migraci starších kopií."
)


def migrate_facebook_health(connection, root: Path | None = None, *,
                            apply: bool = True) -> dict:
    """Přenese `verified_at` a `upcoming_events_at_check` do `source_health`.

    Mapování:

      * `verified_at`               → `last_checked_at` a `last_success_at`
      * `upcoming_events_at_check`  → `baseline_items_median` (seed baseline,
        než se nasbírá vlastní historie stažení)
      * kladná výtěžnost při ověření → `last_item_at`

    Zapisuje jen do prázdných polí. Provozní stav, který mezitím vznikl
    z reálných běhů, je vždycky lepší než ruční poznámka z konfigurace,
    takže se nepřepisuje. Opakovaný běh proto nic nerozbije.

    Konfigurační soubor se **needituje**; viz MIGRATION_HINT.
    """
    root = Path(root) if root else REPO_ROOT
    path = root / "config" / "facebook-sources.json"
    result = {"pages": 0, "migrated": [], "skipped": [], "applied": apply,
              "hint": MIGRATION_HINT, "config_path": str(path)}
    if not path.is_file():
        result["skipped"].append({"source_id": None, "reason": f"{path} neexistuje"})
        return result

    data = json.loads(path.read_text(encoding="utf-8"))
    known = {row["id"] for row in connection.execute("SELECT id FROM source")}

    for page in data.get("pages") or []:
        result["pages"] += 1
        source_id = page.get("source_id")
        verified_at = page.get("verified_at")
        upcoming = page.get("upcoming_events_at_check")
        if verified_at is None and upcoming is None:
            continue
        if source_id not in known:
            # `source_health.source_id` má cizí klíč do `source`. Stránka bez
            # záznamu v registru se nemigruje; nejdřív patří do registru.
            result["skipped"].append({
                "source_id": source_id,
                "reason": "chybí v tabulce source (config/source-registry.json)",
                "verified_at": verified_at, "upcoming_events_at_check": upcoming,
            })
            continue

        baseline = float(upcoming) if isinstance(upcoming, (int, float)) else None
        last_item_at = verified_at if baseline else None
        note = (
            "seed z config/facebook-sources.json: ověřeno "
            f"{verified_at}, {upcoming} nadcházejících akcí při kontrole. "
            "Pole z konfigurace odebrat."
        )
        if apply:
            connection.execute(
                "INSERT INTO source_health ("
                "  source_id, state, last_checked_at, last_success_at, last_item_at,"
                "  baseline_items_median, note"
                ") VALUES (?, 'healthy', ?, ?, ?, ?, ?) "
                "ON CONFLICT(source_id) DO UPDATE SET "
                "  last_checked_at = coalesce(source_health.last_checked_at,"
                "                             excluded.last_checked_at),"
                "  last_success_at = coalesce(source_health.last_success_at,"
                "                             excluded.last_success_at),"
                "  last_item_at = coalesce(source_health.last_item_at,"
                "                          excluded.last_item_at),"
                "  baseline_items_median = coalesce(source_health.baseline_items_median,"
                "                                   excluded.baseline_items_median),"
                "  note = coalesce(source_health.note, excluded.note)",
                (source_id, verified_at, verified_at, last_item_at, baseline, note),
            )
        result["migrated"].append({
            "source_id": source_id, "verified_at": verified_at,
            "upcoming_events_at_check": upcoming,
        })

    if apply:
        connection.commit()
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_report(args) -> int:
    connection = db.connect(args.database, create=False)
    rows = report_rows(connection)
    if args.json:
        print(json.dumps(
            {"generated_at": _iso(_now()), "thresholds": thresholds(), "sources": rows},
            ensure_ascii=False, indent=2))
    else:
        print(render_report(rows))
    return 0


def cmd_evaluate(args) -> int:
    connection = db.connect(args.database, create=False)
    if args.source:
        results = [evaluate(connection, args.source)]
    else:
        results = evaluate_all(connection)

    if args.json:
        print(json.dumps([health.to_dict() for health in results],
                         ensure_ascii=False, indent=2))
        return 0

    if not results:
        print("Žádný zdroj nemá historii stažení — není co vyhodnocovat.")
        return 0
    for health in sorted(results, key=lambda h: (STATE_SEVERITY.get(h.state, 99),
                                                 h.source_id)):
        print(f"{health.state:<13} {health.source_id:<28} {health.reason}")
    print(f"\nVyhodnoceno zdrojů: {len(results)}.")
    return 0


def cmd_cancellations(args) -> int:
    connection = db.connect(args.database, create=False)
    decisions = derive_cancellations(connection, args.source, apply=args.apply)
    if args.json:
        print(json.dumps(decisions, ensure_ascii=False, indent=2))
        return 0
    if not decisions:
        print("Žádná akce nechybí ve výpisu. Není co odvozovat.")
        return 0
    for decision in decisions:
        mark = "ZRUŠENO " if decision["cancelled"] else "ponecháno"
        print(f"{mark} {decision['event_id']:<40} {decision['reason']}")
    changed = sum(1 for decision in decisions if decision["cancelled"])
    print(f"\nPosouzeno {len(decisions)} akcí, zrušeno {changed}."
          + ("" if args.apply else " (nanečisto, zápis vyžaduje --apply)"))
    return 0


def cmd_migrate_facebook(args) -> int:
    connection = db.connect(args.database, create=False)
    result = migrate_facebook_health(connection, args.root, apply=not args.dry_run)
    for entry in result["migrated"]:
        print(f"přeneseno  {entry['source_id']:<28} verified_at={entry['verified_at']} "
              f"upcoming={entry['upcoming_events_at_check']}")
    for entry in result["skipped"]:
        print(f"PŘESKOČENO {str(entry['source_id']):<28} {entry['reason']}")
    print(f"\nStránek v konfiguraci: {result['pages']}, "
          f"přeneseno: {len(result['migrated'])}, "
          f"přeskočeno: {len(result['skipped'])}."
          + ("" if not args.dry_run else " (nanečisto, bez zápisu)"))
    print("\n" + MIGRATION_HINT)
    return 0


def cmd_thresholds(args) -> int:
    values = thresholds()
    if args.json:
        print(json.dumps(values, ensure_ascii=False, indent=2))
        return 0
    print("Prahy zdraví zdrojů — výchozí odhad, nezkalibrováno na reálných datech:\n")
    for key, value in values.items():
        print(f"  {key:<28} {value}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, default=None,
                        help=f"Cesta k databázi (výchozí {db.DEFAULT_DB_PATH}).")
    parser.add_argument("--root", type=Path, default=None, help="Kořen repozitáře.")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="Přehled stavu zdrojů.")
    report.add_argument("--json", action="store_true")

    evaluate_cmd = sub.add_parser("evaluate", help="Vyhodnotit zdraví z historie.")
    evaluate_cmd.add_argument("--source", help="Jen tento zdroj.")
    evaluate_cmd.add_argument("--json", action="store_true")

    cancellations = sub.add_parser("cancellations", help="Odvodit zrušení akcí.")
    cancellations.add_argument("--source", help="Jen tento zdroj.")
    cancellations.add_argument("--apply", action="store_true",
                               help="Zapsat; bez toho běží nanečisto.")
    cancellations.add_argument("--json", action="store_true")

    migrate = sub.add_parser(
        "migrate-facebook",
        help="Jednorázově přenést verified_at a upcoming_events_at_check.")
    migrate.add_argument("--dry-run", action="store_true", help="Nic nezapisovat.")

    thresholds_cmd = sub.add_parser("thresholds", help="Vypsat použité prahy.")
    thresholds_cmd.add_argument("--json", action="store_true")

    args = parser.parse_args()
    return {
        "report": cmd_report,
        "evaluate": cmd_evaluate,
        "cancellations": cmd_cancellations,
        "migrate-facebook": cmd_migrate_facebook,
        "thresholds": cmd_thresholds,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
