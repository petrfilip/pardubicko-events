"""Testy sledování zdraví zdrojů (ADR 0004, balíček P1-5).

Spuštění bez jakékoli závislosti:

    python3 tools/pipeline/test_health.py

Data si každý případ připraví sám v databázi v paměti. Produkční
`var/pardubicko.db` se nikdy neotevírá.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import health  # noqa: E402

NOW = health._parse_ts("2026-08-02T12:00:00+02:00")
failures = []


def check(label, actual, expected):
    if actual != expected:
        failures.append(f"{label}\n    očekáváno: {expected!r}\n    dostal:    {actual!r}")


# --- příprava dat ------------------------------------------------------------

def fresh_db():
    return db.connect(":memory:")


def add_source(connection, source_id, *, name=None, interval=2):
    connection.execute(
        "INSERT INTO source (id, name, url, type, priority, check_interval_days) "
        "VALUES (?, ?, ?, 'html', 'high', ?)",
        (source_id, name or source_id, f"https://example.test/{source_id}", interval),
    )
    return source_id


def days_ago(count, *, hours=0):
    return health._iso(NOW - timedelta(days=count, hours=hours))


def add_run(connection, source_id, *, days, items=None, fill=None, status=200,
            error=None, content_hash=None, unparsed=0):
    """Jeden zaznamenaný běh: stažení a volitelně jeho extrakce."""
    fetch_id = health.record_fetch(
        connection, source_id, fetched_at=days_ago(days), http_status=status,
        content_hash=content_hash or f"hash-{source_id}-{days}", error=error)
    if items is not None:
        health.record_extract(connection, fetch_id, items_found=items,
                              items_valid=items, items_unparsed=unparsed,
                              fill_rates=fill or {})
    connection.commit()
    return fetch_id


def add_event(connection, event_id, source_id, *, start_at, url=None, missing_runs=0,
              cancelled=0):
    connection.execute(
        "INSERT INTO event (id, title, start_at, municipality_name, source_type,"
        " source_url, cancelled) VALUES (?, ?, ?, 'Chrudim', 'html', ?, ?)",
        (event_id, f"Akce {event_id}", start_at, url or f"https://example.test/{event_id}",
         cancelled),
    )
    connection.execute(
        "INSERT INTO event_source (event_id, source_id, url, missing_runs) "
        "VALUES (?, ?, ?, ?)",
        (event_id, source_id, url or f"https://example.test/{event_id}", missing_runs),
    )
    connection.commit()


FULL = {"start_at": 1.0, "title": 1.0, "canonical_url": 1.0}


# --- 1. velký zdroj, který přestal vracet položky ----------------------------
# Zdroj se spolehlivě chová jako čtyřicetiakcový. Nula je u něj rozbití.

conn = fresh_db()
add_source(conn, "chrudimska-beseda")
for day in range(20, 1, -2):
    add_run(conn, "chrudimska-beseda", days=day, items=40, fill=FULL)
add_run(conn, "chrudimska-beseda", days=0, items=0, fill={})

result = health.evaluate(conn, "chrudimska-beseda", now=NOW)
check("baseline 40 → suspect", result.state, "suspect")
check("baseline se spočítal z historie", result.extract.baseline_items_median, 40.0)
check("hodnotí se podle objemu", result.extract.volume_evaluated, True)
check("stažení samo je v pořádku", result.fetch.ok, True)
check("selhání stažení nula", result.fetch.consecutive_failures, 0)

stored = conn.execute(
    "SELECT state, baseline_items_median, first_alerted_at FROM source_health "
    "WHERE source_id = 'chrudimska-beseda'").fetchone()
check("stav se uložil", stored["state"], "suspect")
check("baseline se uložil", stored["baseline_items_median"], 40.0)
check("první poplach je datovaný", bool(stored["first_alerted_at"]), True)


# --- 1b. výrazný propad počtu položek ----------------------------------------

conn = fresh_db()
add_source(conn, "pardubice-calendar")
for day in range(20, 1, -2):
    add_run(conn, "pardubice-calendar", days=day, items=40, fill=FULL)
add_run(conn, "pardubice-calendar", days=0, items=10, fill=FULL)
check("propad na čtvrtinu baseline → degraded",
      health.evaluate(conn, "pardubice-calendar", now=NOW).state, "degraded")

conn = fresh_db()
add_source(conn, "pardubice-calendar")
for day in range(20, 1, -2):
    add_run(conn, "pardubice-calendar", days=day, items=40, fill=FULL)
add_run(conn, "pardubice-calendar", days=0, items=20, fill=FULL)
check("poloviční výtěžnost degraded není (práh je 0,4×)",
      health.evaluate(conn, "pardubice-calendar", now=NOW).state, "healthy")


# --- 2. malá obec, která nemá co nabídnout -----------------------------------
# NEJDŮLEŽITĚJŠÍ TEST BALÍČKU. Nulová výtěžnost sama o sobě chyba není.
# Kdyby tady vznikl poplach, monitoring se po týdnu vypne a je k ničemu.

conn = fresh_db()
add_source(conn, "slatinany-city")
for day in range(20, 1, -2):
    add_run(conn, "slatinany-city", days=day, items=1, fill=FULL)
add_run(conn, "slatinany-city", days=0, items=0, fill={})

result = health.evaluate(conn, "slatinany-city", now=NOW)
check("baseline 1 a nula položek → zůstává healthy", result.state, "healthy")
check("baseline 1 se podle objemu nehodnotí", result.extract.volume_evaluated, False)
check("baseline je znám", result.extract.baseline_items_median, 1.0)
check("důvod pojmenuje nízký baseline", "nízký baseline" in result.reason, True)

# Ani zdroj, který nikdy nic nevydal, nesmí alarmovat.
conn2 = fresh_db()
add_source(conn2, "ufc-janderov")
for day in (14, 7, 0):
    add_run(conn2, "ufc-janderov", days=day, items=0, fill={})
result = health.evaluate(conn2, "ufc-janderov", now=NOW)
check("zdroj bez jediné položky zůstává healthy", result.state, "healthy")
check("bez historie položek není baseline", result.extract.baseline_items_median, None)

# Práh je hraniční: baseline 2 se ještě nehodnotí, baseline 3 už ano.
for baseline, expected in ((2, "healthy"), (3, "suspect")):
    conn3 = fresh_db()
    add_source(conn3, "hranicni")
    for day in range(20, 1, -2):
        add_run(conn3, "hranicni", days=day, items=baseline, fill=FULL)
    add_run(conn3, "hranicni", days=0, items=0, fill={})
    check(f"baseline {baseline} → {expected}",
          health.evaluate(conn3, "hranicni", now=NOW).state, expected)


# --- 3. přestalo se plnit klíčové pole ---------------------------------------
# Položky pořád chodí, ale bez termínu jsou k ničemu. To je redesign zdroje.

conn = fresh_db()
add_source(conn, "zamek-litomysl")
for day in range(20, 1, -2):
    add_run(conn, "zamek-litomysl", days=day, items=12, fill=FULL)
add_run(conn, "zamek-litomysl", days=0, items=12,
        fill={"start_at": 0.0, "title": 1.0, "canonical_url": 1.0})

result = health.evaluate(conn, "zamek-litomysl", now=NOW)
check("prázdný start_at → schema-drift", result.state, "schema-drift")
check("pojmenuje se konkrétní pole", result.extract.drifted_fields, ["start_at"])
check("počet položek přitom neklesl", result.extract.items_found, 12)

# Mírný pokles vyplnění se nesmí počítat jako drift (práh je 0,8× baseline).
conn = fresh_db()
add_source(conn, "zamek-nachod")
for day in range(20, 1, -2):
    add_run(conn, "zamek-nachod", days=day, items=12, fill=FULL)
add_run(conn, "zamek-nachod", days=0, items=12,
        fill={"start_at": 0.9, "title": 1.0, "canonical_url": 1.0})
check("pokles vyplnění na 0,9 driftem není",
      health.evaluate(conn, "zamek-nachod", now=NOW).state, "healthy")

# Pole, které zdroj nikdy neplnil, nesmí být drift (baseline 0).
conn = fresh_db()
add_source(conn, "bez-url")
for day in range(20, 1, -2):
    add_run(conn, "bez-url", days=day, items=12,
            fill={"start_at": 1.0, "title": 1.0, "canonical_url": 0.0})
add_run(conn, "bez-url", days=0, items=12,
        fill={"start_at": 1.0, "title": 1.0, "canonical_url": 0.0})
check("nikdy neplněné pole není drift",
      health.evaluate(conn, "bez-url", now=NOW).state, "healthy")


# --- 4. opakované selhání stažení --------------------------------------------

conn = fresh_db()
add_source(conn, "mfc-hlinsko")
for day in range(20, 5, -2):
    add_run(conn, "mfc-hlinsko", days=day, items=6, fill=FULL)
for day in (4, 2, 0):
    add_run(conn, "mfc-hlinsko", days=day, status=503, error="HTTP 503")

result = health.evaluate(conn, "mfc-hlinsko", now=NOW)
check("tři selhání v řadě → broken", result.state, "broken")
check("počet selhání sedí", result.fetch.consecutive_failures, 3)
check("poslední úspěch se pamatuje", result.fetch.last_success_at, days_ago(6))

# Dvě selhání ještě broken nejsou — práh je tři.
conn = fresh_db()
add_source(conn, "mfc-hlinsko")
for day in range(20, 3, -2):
    add_run(conn, "mfc-hlinsko", days=day, items=6, fill=FULL)
for day in (2, 0):
    add_run(conn, "mfc-hlinsko", days=day, status=503, error="HTTP 503")
result = health.evaluate(conn, "mfc-hlinsko", now=NOW)
check("dvě selhání ještě nejsou broken", result.state != "broken", True)
check("dvě selhání se ale počítají", result.fetch.consecutive_failures, 2)

# Selhání stažení nesmí zamlčet, že extrakce naposledy fungovala: signály
# jsou oddělené a baseline zůstává znám.
check("baseline přežije výpadek stažení", result.extract.baseline_items_median, 6.0)


# --- 5. obsah beze změny a nic budoucího -------------------------------------

conn = fresh_db()
add_source(conn, "vesely-kopec")
for day in (40, 33, 26, 19, 12, 5, 0):
    add_run(conn, "vesely-kopec", days=day, items=1, fill=FULL,
            content_hash="stejny-obsah")
add_event(conn, "akce-loni", "vesely-kopec", start_at="2026-06-01T10:00:00+02:00")

result = health.evaluate(conn, "vesely-kopec", now=NOW)
check("beze změny 40 dní a bez budoucích termínů → stale", result.state, "stale")
check("doba beze změny se změřila", result.freshness.unchanged_days, 40)
check("žádný budoucí termín", result.freshness.has_future_events, False)

# Tentýž zdroj s budoucí akcí stale není — obsah se nemění, protože se
# program nezměnil, ne protože zdroj umřel.
conn = fresh_db()
add_source(conn, "vesely-kopec")
for day in (40, 33, 26, 19, 12, 5, 0):
    add_run(conn, "vesely-kopec", days=day, items=1, fill=FULL,
            content_hash="stejny-obsah")
add_event(conn, "akce-pristi-mesic", "vesely-kopec", start_at="2026-09-01T10:00:00+02:00")
check("budoucí termín stale ruší",
      health.evaluate(conn, "vesely-kopec", now=NOW).state, "healthy")

# Krátká historie beze změny stale nespouští.
conn = fresh_db()
add_source(conn, "kratka-historie")
for day in (10, 5, 0):
    add_run(conn, "kratka-historie", days=day, items=1, fill=FULL,
            content_hash="stejny-obsah")
check("deset dní beze změny stale není",
      health.evaluate(conn, "kratka-historie", now=NOW).state, "healthy")


# --- 6. odvození zrušení ------------------------------------------------------
# Zmizení z výpisu není důkaz. Teprve dvě po sobě jdoucí zdravá stažení.

def cancellation_fixture(missing_runs, *, state="healthy"):
    connection = fresh_db()
    add_source(connection, "chrudimska-beseda")
    for day in range(20, -1, -2):
        add_run(connection, "chrudimska-beseda", days=day, items=8, fill=FULL)
    health.evaluate(connection, "chrudimska-beseda", now=NOW)
    if state != "healthy":
        connection.execute(
            "UPDATE source_health SET state = ? WHERE source_id = 'chrudimska-beseda'",
            (state,))
        connection.commit()

    # Akce, kterou zdroj pořád hlásí — drží horní hranici staženého okna.
    add_event(connection, "koncert-v-programu", "chrudimska-beseda",
              start_at="2026-08-20T19:00:00+02:00")
    # Akce, která z výpisu zmizela.
    add_event(connection, "koncert-zmizel", "chrudimska-beseda",
              start_at="2026-08-10T19:00:00+02:00", missing_runs=missing_runs)
    return connection


conn = cancellation_fixture(1)
decisions = health.derive_cancellations(conn, "chrudimska-beseda", now=NOW)
check("jedno chybějící stažení nic neruší", [d["cancelled"] for d in decisions], [False])
check("důvod je vidět", "chybí jen 1×" in decisions[0]["reason"], True)
check("akce zůstala nezrušená",
      conn.execute("SELECT cancelled FROM event WHERE id = 'koncert-zmizel'")
      .fetchone()["cancelled"], 0)

conn = cancellation_fixture(2)
decisions = health.derive_cancellations(conn, "chrudimska-beseda", now=NOW)
check("dvě chybějící stažení u zdravého zdroje ruší",
      [d["cancelled"] for d in decisions], [True])
check("zrušení se zapsalo",
      conn.execute("SELECT cancelled FROM event WHERE id = 'koncert-zmizel'")
      .fetchone()["cancelled"], 1)

# Podmínka 1: nezdravý zdroj neruší nic, i kdyby akce chyběla desetkrát.
conn = cancellation_fixture(10, state="suspect")
decisions = health.derive_cancellations(conn, "chrudimska-beseda", now=NOW)
check("nezdravý zdroj neruší", [d["cancelled"] for d in decisions], [False])
check("důvod pojmenuje stav zdroje", "není healthy" in decisions[0]["reason"], True)

# Podmínka 2: termín za horizontem staženého okna se neruší. Výpis tam
# nedohlédl, takže o té akci nic netvrdí.
conn = cancellation_fixture(3)
add_event(conn, "festival-za-horizontem", "chrudimska-beseda",
          start_at="2026-12-31T19:00:00+01:00", missing_runs=3)
decisions = {d["event_id"]: d for d in health.derive_cancellations(
    conn, "chrudimska-beseda", now=NOW)}
check("akce v okně se zruší", decisions["koncert-zmizel"]["cancelled"], True)
check("akce za horizontem se nezruší",
      decisions["festival-za-horizontem"]["cancelled"], False)
check("důvod pojmenuje okno",
      "mimo stažené okno" in decisions["festival-za-horizontem"]["reason"], True)

# Okno se dá zadat i ručně, když ho zná volající (rozsah, který adaptér stáhl).
conn = cancellation_fixture(2)
decisions = health.derive_cancellations(
    conn, "chrudimska-beseda", now=NOW, window=("2026-08-02", "2026-08-05"),
    apply=False)
check("ručně zadané užší okno akci vynechá",
      [d["cancelled"] for d in decisions], [False])

# Běh nanečisto nesmí nic zapsat.
conn = cancellation_fixture(2)
health.derive_cancellations(conn, "chrudimska-beseda", now=NOW, apply=False)
check("bez apply se nic nemění",
      conn.execute("SELECT cancelled FROM event WHERE id = 'koncert-zmizel'")
      .fetchone()["cancelled"], 0)


# --- 6b. počítadlo missing_runs ----------------------------------------------
# Počítadlo se smí zvyšovat jen ve zdravém běhu, jinak by si rozbitý adaptér
# sám vyrobil důkaz o zrušení.

conn = cancellation_fixture(0)
result = health.mark_missing(
    conn, "chrudimska-beseda", ["https://example.test/koncert-v-programu"], now=NOW)
check("chybějící akce se započítala", result["missing"], 1)
check("viděná akce se vynulovala", result["seen"], 1)
check("missing_runs je 1",
      conn.execute("SELECT missing_runs FROM event_source "
                   "WHERE event_id = 'koncert-zmizel'").fetchone()["missing_runs"], 1)
health.mark_missing(conn, "chrudimska-beseda",
                    ["https://example.test/koncert-v-programu"], now=NOW)
check("missing_runs je po druhém běhu 2",
      conn.execute("SELECT missing_runs FROM event_source "
                   "WHERE event_id = 'koncert-zmizel'").fetchone()["missing_runs"], 2)
check("teď už se zrušení odvodí",
      [d["cancelled"] for d in health.derive_cancellations(
          conn, "chrudimska-beseda", now=NOW)], [True])

conn = cancellation_fixture(0, state="broken")
result = health.mark_missing(conn, "chrudimska-beseda", [], now=NOW)
check("u rozbitého zdroje se nepočítá nic", result["skipped"], True)
check("missing_runs zůstalo nula",
      conn.execute("SELECT missing_runs FROM event_source "
                   "WHERE event_id = 'koncert-zmizel'").fetchone()["missing_runs"], 0)


# --- tři signály zůstávají oddělené ------------------------------------------
# Stažení může selhat, aniž by se ztratilo, co víme o extrakci a o čerstvosti.

conn = fresh_db()
add_source(conn, "trojice")
for day in range(20, 3, -2):
    add_run(conn, "trojice", days=day, items=9, fill=FULL, content_hash="A")
add_run(conn, "trojice", days=0, status=500, error="HTTP 500")
result = health.evaluate(conn, "trojice", now=NOW)
check("signál stažení: selhalo", result.fetch.ok, False)
check("signál extrakce: baseline se nezahodil", result.extract.baseline_items_median, 9.0)
check("signál čerstvosti: víme, kdy naposledy něco přišlo",
      result.freshness.last_item_at, days_ago(4))
check("jedno selhání ještě není broken", result.state, "healthy")


# --- pomocné výpočty ----------------------------------------------------------

check("fill rate z položek",
      health.fill_rates_from_items([
          {"start_at": "2026-08-10", "title": "A", "canonical_url": "u"},
          {"start_at": None, "title": "B", "canonical_url": "u"},
      ]),
      {"start_at": 0.5, "title": 1.0, "canonical_url": 1.0})
check("prázdný vstup nevyrobí nuly", health.fill_rates_from_items([]), {})
check("304 je úspěch, ne selhání", health._is_success(None, 304), True)
check("404 je selhání", health._is_success(None, 404), False)
check("chyba je selhání i při 200", health._is_success("timeout", 200), False)
check("baseline ignoruje nulové běhy",
      health._baseline_items([
          {"items_found": 0}, {"items_found": 10}, {"items_found": 12},
      ])[0], 11.0)


# --- report -------------------------------------------------------------------

conn = fresh_db()
for source_id in ("rozbity", "podezrely", "zdravy", "nekontrolovany"):
    add_source(conn, source_id)

for day in (4, 2, 0):
    add_run(conn, "rozbity", days=day, status=503, error="HTTP 503")
for day in range(20, 1, -2):
    add_run(conn, "podezrely", days=day, items=40, fill=FULL)
add_run(conn, "podezrely", days=0, items=0)
for day in range(20, -1, -2):
    add_run(conn, "zdravy", days=day, items=5, fill=FULL)
health.evaluate_all(conn, now=NOW)

rows = health.report_rows(conn, now=NOW)
check("report obsahuje i nikdy nekontrolovaný zdroj", len(rows), 4)
check("pořadí řadí zdroje k opravě nahoru",
      [row["source_id"] for row in rows],
      ["rozbity", "podezrely", "zdravy", "nekontrolovany"])
check("nekontrolovaný zdroj má vlastní stav", rows[3]["state"], "unchecked")
check("dnů od poslední položky se počítá", rows[1]["days_since_last_item"], 2)
check("zdravý zdroj má položku dnes", rows[2]["days_since_last_item"], 0)
check("rozbitý zdroj nikdy položku nevydal", rows[0]["days_since_last_item"], None)
check("interval kontroly se hlídá", rows[3]["check_overdue"], False)

rendered = health.render_report(rows, now=NOW)
check("report je text s hlavičkou", rendered.splitlines()[0].startswith("Zdraví zdrojů"),
      True)
check("report jmenuje frontu k opravě", "Fronta k opravě adaptéru" in rendered, True)

payload = json.dumps({"sources": rows, "thresholds": health.thresholds()},
                     ensure_ascii=False)
check("report jde serializovat do JSON", "broken_consecutive_failures" in payload, True)


# --- migrace stavu z konfigurace ----------------------------------------------

# Migrační test používá starou podobu konfigurace jako izolovanou fixture.
# Produkční konfigurace po dokončení migrace provozní pole už nepřipouští.
config = {
    "schema_version": 1,
    "pages": [
        {"source_id": "velky-zdroj", "verified_at": "2026-08-02",
         "upcoming_events_at_check": 8},
        {"source_id": "prazdny-zdroj", "verified_at": "2026-08-02",
         "upcoming_events_at_check": 0},
        {"source_id": "neznamy-zdroj", "verified_at": "2026-08-02",
         "upcoming_events_at_check": 3},
    ],
}
fixture_dir = tempfile.TemporaryDirectory()
migration_root = Path(fixture_dir.name)
(migration_root / "config").mkdir()
(migration_root / "config" / "facebook-sources.json").write_text(
    json.dumps(config, ensure_ascii=False), encoding="utf-8")
pages = config["pages"]

conn = fresh_db()
for page in pages:
    add_source(conn, page["source_id"])
result = health.migrate_facebook_health(conn, migration_root)
check("migrace prošla všechny stránky", result["pages"], len(pages))
check("nic se nepřeskočilo, když zdroje existují", result["skipped"], [])
check("výstup říká, že se pole mají z konfigurace odebrat",
      "odebrat" in result["hint"], True)

sample = next(page for page in pages if page.get("upcoming_events_at_check"))
row = conn.execute(
    "SELECT * FROM source_health WHERE source_id = ?", (sample["source_id"],)).fetchone()
check("verified_at se stal poslední kontrolou",
      row["last_checked_at"], sample["verified_at"])
check("verified_at se stal posledním úspěchem",
      row["last_success_at"], sample["verified_at"])
check("upcoming_events_at_check se stal baseline",
      row["baseline_items_median"], float(sample["upcoming_events_at_check"]))
check("kladná výtěžnost datuje poslední položku",
      row["last_item_at"], sample["verified_at"])

zero = next(page for page in pages if page.get("upcoming_events_at_check") == 0)
row = conn.execute(
    "SELECT * FROM source_health WHERE source_id = ?", (zero["source_id"],)).fetchone()
check("nulová výtěžnost není poplach", row["state"], "healthy")
check("nulová výtěžnost nedatuje položku", row["last_item_at"], None)
check("nulová výtěžnost se podle objemu nehodnotí",
      row["baseline_items_median"] < health.LOW_BASELINE_ITEMS, True)

# Migrace se nesmí prát s provozním stavem, který mezitím vznikl.
before = conn.execute(
    "SELECT count(*) AS n FROM source_health").fetchone()["n"]
conn.execute("UPDATE source_health SET baseline_items_median = 99 WHERE source_id = ?",
             (sample["source_id"],))
conn.commit()
health.migrate_facebook_health(conn, migration_root)
check("opakovaná migrace nepřidá řádky",
      conn.execute("SELECT count(*) AS n FROM source_health").fetchone()["n"], before)
check("opakovaná migrace nepřepíše naměřený baseline",
      conn.execute("SELECT baseline_items_median FROM source_health WHERE source_id = ?",
                   (sample["source_id"],)).fetchone()["baseline_items_median"], 99.0)

# Stránka bez záznamu v registru zdrojů se nemigruje (cizí klíč) a je vidět.
conn = fresh_db()
add_source(conn, pages[0]["source_id"])
result = health.migrate_facebook_health(conn, migration_root)
check("migrují se jen známé zdroje", len(result["migrated"]), 1)
check("ostatní jsou vypsané jako přeskočené",
      len(result["skipped"]), len([p for p in pages if p.get("verified_at")]) - 1)
check("důvod přeskočení je konkrétní",
      "config/source-registry.json" in result["skipped"][0]["reason"], True)

# Konfigurační soubor se nesmí změnit.
after = json.loads((migration_root / "config" / "facebook-sources.json").read_text(
    encoding="utf-8"))
check("migrace konfiguraci needituje", after, config)


# --- seed baseline z migrace se použije, dokud není vlastní historie ----------

conn = fresh_db()
add_source(conn, "velky-zdroj")
health.migrate_facebook_health(conn, migration_root)
add_run(conn, "velky-zdroj", days=0, items=0)
result = health.evaluate(conn, "velky-zdroj", now=NOW)
check("seed z konfigurace slouží jako baseline prvního běhu",
      result.extract.baseline_items_median, 8.0)
check("prázdný první běh proti seedu je podezřelý", result.state, "suspect")


# --- výsledek ------------------------------------------------------------------

if failures:
    print(f"NEPROŠLO {len(failures)} kontrol:\n")
    for f in failures:
        print(" - " + f)
    raise SystemExit(1)
print("Všechny kontroly prošly.")
