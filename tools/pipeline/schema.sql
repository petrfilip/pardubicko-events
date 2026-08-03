-- Schéma provozní databáze fáze 2 (ADR 0002).
--
-- Databáze je odvozený provozní store, ne zdroj pravdy. Zdrojem pravdy je
-- git: konfiguraci vlastní člověk, publikovaná data existují jako export
-- v `data/`. Soubor databáze do gitu nepatří.
--
-- Hranice, která platí napříč schématem:
--   * konfigurace  — zrcadlo souborů v `config/`, mění ji člověk
--   * provozní stav — píše pipeline, do konfigurace se nikdy nevrací
--
-- Odchylky od návrhu v docs/phase-2-architecture.md jsou vysvětlené u
-- konkrétních tabulek. Vždy jde o totéž: uchovat data doslovně, aby byl
-- export prokazatelně bezeztrátový dřív, než vznikne normalizační vrstva.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Metadata repozitáře
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS repo_meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS schema_migration (
  version    INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Geografie a slovníky
-- ---------------------------------------------------------------------------

-- Zrcadlo config/municipalities.json, plní import repozitáře. Akce dál
-- nesou název obce doslovně v `event.municipality_name`, aby export zůstal
-- bezeztrátový.
CREATE TABLE IF NOT EXISTS municipality (
  id       INTEGER PRIMARY KEY,        -- kód obce z číselníku
  name     TEXT NOT NULL,
  district TEXT NOT NULL,
  region   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS municipality_alias (
  alias           TEXT PRIMARY KEY,
  municipality_id INTEGER NOT NULL REFERENCES municipality(id)
);

-- Zrcadlo config/categories.json. Doslovný text zůstává současně
-- v `event_category.name`, aby import/export neměnil publikovaná data.
CREATE TABLE IF NOT EXISTS category (
  id          TEXT PRIMARY KEY,
  axis        TEXT NOT NULL,
  sort_order  INTEGER,
  label       TEXT NOT NULL,
  description TEXT
);

CREATE TABLE IF NOT EXISTS category_alias (
  alias       TEXT PRIMARY KEY,
  category_id TEXT NOT NULL REFERENCES category(id)
);

-- ---------------------------------------------------------------------------
-- Konfigurace zdrojů (zrcadlo config/source-registry.json)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS source (
  id                  TEXT PRIMARY KEY,
  name                TEXT NOT NULL,
  url                 TEXT NOT NULL,
  type                TEXT NOT NULL,
  adapter             TEXT,
  municipality_name   TEXT,
  district            TEXT,
  region              TEXT,             -- NULL u nadregionálních kanálů
  priority            TEXT NOT NULL,
  check_interval_days INTEGER NOT NULL,
  enabled             INTEGER NOT NULL DEFAULT 1,
  notes               TEXT
);

CREATE TABLE IF NOT EXISTS facebook_page (
  source_id           TEXT PRIMARY KEY,
  name                TEXT NOT NULL,
  facebook_page       TEXT NOT NULL,
  municipality_name   TEXT,
  district            TEXT,
  region              TEXT,
  priority            TEXT NOT NULL,
  check_interval_days INTEGER NOT NULL,
  enabled             INTEGER NOT NULL DEFAULT 1,
  notes               TEXT
);

-- ---------------------------------------------------------------------------
-- Provozní stav sběru (ADR 0004)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS source_fetch (
  id           INTEGER PRIMARY KEY,
  source_id    TEXT NOT NULL REFERENCES source(id),
  url          TEXT,
  fetched_at   TEXT NOT NULL,
  http_status  INTEGER,
  etag         TEXT,
  last_modified TEXT,
  content_hash TEXT,
  bytes        INTEGER,
  duration_ms  INTEGER,
  error        TEXT
);

CREATE INDEX IF NOT EXISTS idx_source_fetch_source
  ON source_fetch(source_id, fetched_at DESC);

CREATE TABLE IF NOT EXISTS source_extract (
  fetch_id       INTEGER PRIMARY KEY REFERENCES source_fetch(id),
  items_found    INTEGER NOT NULL,
  items_valid    INTEGER NOT NULL,
  items_unparsed INTEGER NOT NULL DEFAULT 0,
  fill_rates     TEXT NOT NULL DEFAULT '{}'   -- JSON
);

CREATE TABLE IF NOT EXISTS source_health (
  source_id             TEXT PRIMARY KEY REFERENCES source(id),
  state                 TEXT NOT NULL DEFAULT 'healthy',
  consecutive_failures  INTEGER NOT NULL DEFAULT 0,
  last_checked_at       TEXT,
  last_success_at       TEXT,
  last_item_at          TEXT,
  baseline_items_median REAL,
  baseline_fill_rates   TEXT,
  first_alerted_at      TEXT,
  note                  TEXT,
  CHECK (state IN ('healthy', 'suspect', 'degraded', 'schema-drift', 'stale', 'broken'))
);

-- Jeden dávkový běh a jeho izolované per-source kroky (WP3). Stav zdroje se
-- zapisuje mimo jeho obsahovou transakci, takže i pád adaptéru zůstane
-- auditovatelný a nevrátí úspěšné výsledky předchozích zdrojů.
CREATE TABLE IF NOT EXISTS pipeline_run (
  id          TEXT PRIMARY KEY,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  status      TEXT NOT NULL,
  offline     INTEGER NOT NULL DEFAULT 0,
  dry_run     INTEGER NOT NULL DEFAULT 0,
  report_path TEXT,
  error       TEXT,
  CHECK (status IN ('running', 'success', 'partial', 'failed', 'no-change'))
);

CREATE TABLE IF NOT EXISTS pipeline_source_run (
  run_id                 TEXT NOT NULL REFERENCES pipeline_run(id) ON DELETE CASCADE,
  source_id              TEXT NOT NULL REFERENCES source(id),
  started_at             TEXT NOT NULL,
  finished_at            TEXT,
  status                 TEXT NOT NULL,
  items_found            INTEGER NOT NULL DEFAULT 0,
  items_valid            INTEGER NOT NULL DEFAULT 0,
  candidates_created     INTEGER NOT NULL DEFAULT 0,
  candidates_existing    INTEGER NOT NULL DEFAULT 0,
  candidates_updated     INTEGER NOT NULL DEFAULT 0,
  candidates_quarantined INTEGER NOT NULL DEFAULT 0,
  error                  TEXT,
  PRIMARY KEY (run_id, source_id),
  CHECK (status IN ('running', 'success', 'no-change', 'failed', 'skipped'))
);

-- ---------------------------------------------------------------------------
-- Kandidáti a inbox
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS inbox (
  id              INTEGER PRIMARY KEY,
  url             TEXT NOT NULL,
  url_norm        TEXT NOT NULL UNIQUE,
  submitted_at    TEXT NOT NULL,
  submitted_via   TEXT NOT NULL,
  note            TEXT,
  state           TEXT NOT NULL DEFAULT 'new',
  resolved_kind   TEXT,
  candidate_id    TEXT,
  attempts        INTEGER NOT NULL DEFAULT 0,
  last_attempt_at TEXT,
  error           TEXT,
  CHECK (state IN ('new', 'fetched', 'candidate', 'source-proposal', 'failed')),
  CHECK (submitted_via IN ('cli', 'api'))
);

CREATE TABLE IF NOT EXISTS candidate (
  id               TEXT PRIMARY KEY,
  source_file      TEXT,               -- původ v research/, kvůli bezeztrátovému exportu
  source_id        TEXT REFERENCES source(id),
  inbox_id         INTEGER REFERENCES inbox(id),
  discovery_method TEXT NOT NULL,
  payload          TEXT NOT NULL,      -- JSON celého kandidáta, doslovně
  state            TEXT NOT NULL,
  event_id         TEXT REFERENCES event(id),
  created_at       TEXT NOT NULL,
  reviewed_at      TEXT,
  notes            TEXT
);

-- Nejisté shody deduplikace se nikdy neslučují automaticky. Fronta drží
-- reprodukovatelný rozklad skóre i stav lidského rozhodnutí.
CREATE TABLE IF NOT EXISTS match_review (
  id           INTEGER PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidate(id) ON DELETE CASCADE,
  event_id     TEXT NOT NULL REFERENCES event(id) ON DELETE CASCADE,
  score        REAL NOT NULL,
  breakdown    TEXT NOT NULL,
  state        TEXT NOT NULL DEFAULT 'pending',
  created_at   TEXT NOT NULL,
  decided_at   TEXT,
  note         TEXT,
  UNIQUE (candidate_id, event_id),
  CHECK (state IN ('pending', 'merged', 'separate'))
);

-- ---------------------------------------------------------------------------
-- Publikovaná data
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS week (
  id           TEXT PRIMARY KEY,       -- 2026-W31
  date_from    TEXT NOT NULL,
  date_to      TEXT NOT NULL,
  file         TEXT NOT NULL,
  generated_at TEXT,
  position     INTEGER NOT NULL        -- pořadí v manifestu
);

CREATE TABLE IF NOT EXISTS event (
  id                TEXT PRIMARY KEY,
  title             TEXT NOT NULL,
  description       TEXT,
  start_at          TEXT NOT NULL,
  end_at            TEXT,
  all_day           INTEGER NOT NULL DEFAULT 0,
  venue             TEXT,
  -- Název obce doslovně tak, jak je v publikovaných datech. Vazba na
  -- číselník je zatím volitelná; doplní ji P1-6. Bez toho by export
  -- nebyl bezeztrátový, protože číselník ještě neexistuje.
  municipality_name TEXT NOT NULL,
  municipality_id   INTEGER REFERENCES municipality(id),
  price_type        TEXT NOT NULL DEFAULT 'unknown',
  price_text        TEXT,
  price_amount      INTEGER,
  price_currency    TEXT,
  source_type       TEXT NOT NULL,
  source_url        TEXT NOT NULL,
  cancelled         INTEGER NOT NULL DEFAULT 0,
  status            TEXT NOT NULL DEFAULT 'published',
  last_verified_at  TEXT,
  first_seen_at     TEXT,
  last_seen_at      TEXT,
  match_title_norm  TEXT NOT NULL DEFAULT '',
  CHECK (price_type IN ('free', 'paid', 'unknown')),
  CHECK (status IN ('draft', 'published', 'quarantined'))
);

CREATE INDEX IF NOT EXISTS idx_event_start ON event(start_at);
CREATE INDEX IF NOT EXISTS idx_event_municipality ON event(municipality_name);
CREATE INDEX IF NOT EXISTS idx_event_match ON event(match_title_norm);

-- Zařazení akce do týdenních souborů.
--
-- Odchylka od architektury: členství je uložené explicitně, nikoli odvozené
-- z překryvu termínu s rozsahem týdne. Důvod je ověřitelnost — export musí
-- reprodukovat dnešní publikovaná data doslovně, včetně rozhodnutí, která
-- udělal kurátor. Přechod na odvozené členství je otevřená otázka 4
-- v docs/phase-2-architecture.md a je to samostatné rozhodnutí.
CREATE TABLE IF NOT EXISTS event_week (
  event_id TEXT NOT NULL REFERENCES event(id) ON DELETE CASCADE,
  week_id  TEXT NOT NULL REFERENCES week(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,           -- pořadí akce v týdenním souboru
  PRIMARY KEY (event_id, week_id)
);

CREATE TABLE IF NOT EXISTS event_category (
  event_id    TEXT NOT NULL REFERENCES event(id) ON DELETE CASCADE,
  position    INTEGER NOT NULL,        -- pořadí se zachovává kvůli exportu
  name        TEXT NOT NULL,           -- doslovná hodnota z dat
  category_id TEXT REFERENCES category(id),
  PRIMARY KEY (event_id, position)
);

-- Které zdroje akci hlásily. Podklad pro deduplikaci i pro odvození
-- zrušení podle ADR 0004.
CREATE TABLE IF NOT EXISTS event_source (
  event_id      TEXT NOT NULL REFERENCES event(id) ON DELETE CASCADE,
  source_id     TEXT REFERENCES source(id),
  url           TEXT NOT NULL,
  first_seen_at TEXT,
  last_seen_at  TEXT,
  missing_runs  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (event_id, url)
);

-- ---------------------------------------------------------------------------
-- Fulltext
-- ---------------------------------------------------------------------------
--
-- remove_diacritics 2 je podstatné: bez něj dotaz "ridic" nenajde "řidič".
-- Ověřeno v php:8.3-cli i v python:3.12-slim.

CREATE VIRTUAL TABLE IF NOT EXISTS event_fts USING fts5(
  event_id UNINDEXED,
  title,
  description,
  venue,
  municipality,
  categories,
  tokenize="unicode61 remove_diacritics 2"
);
