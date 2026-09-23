-- Migrace 0002: API tokeny a historie změn (ADR 0008, etapa 1).

-- Token agenta. Ukládá se jen SHA-256 hash; samotný token se ukáže jednou
-- při vytvoření. Název tokenu je zároveň aktér v historii změn, proto je
-- neměnný a jedinečný.
CREATE TABLE api_token (
  id                  INTEGER PRIMARY KEY,
  name                TEXT NOT NULL UNIQUE,
  token_hash          TEXT NOT NULL UNIQUE,
  daily_publish_limit INTEGER NOT NULL DEFAULT 50,
  created_at          TEXT NOT NULL,
  last_used_at        TEXT,
  revoked_at          TEXT,
  CHECK (daily_publish_limit >= 0)
);

-- Každá změna dat. Nahrazuje git diff: kdo, v jakém běhu, co a jak to
-- vypadalo předtím a potom. `before` a `after` jsou JSON celé entity, aby
-- šla změna vrátit bez znalosti toho, která pole se měnila.
CREATE TABLE change_log (
  id         INTEGER PRIMARY KEY,
  at         TEXT NOT NULL,
  actor      TEXT NOT NULL,
  actor_kind TEXT NOT NULL,
  run_id     TEXT,
  entity     TEXT NOT NULL,
  entity_id  TEXT NOT NULL,
  action     TEXT NOT NULL,
  before     TEXT,
  after      TEXT,
  note       TEXT,
  CHECK (actor_kind IN ('agent', 'admin', 'system'))
);

CREATE INDEX idx_change_log_entity ON change_log(entity, entity_id, id);
CREATE INDEX idx_change_log_actor ON change_log(actor, at);
CREATE INDEX idx_change_log_run ON change_log(run_id);

-- Týdny už nejsou soubory v manifestu, ale odvozený index (ADR 0006).
-- Pořadí se odvozuje z ISO označení, aby nově založený týden zapadl mezi
-- převzaté bez přečíslování.
UPDATE week SET position =
  CAST(substr(id, 1, 4) AS INTEGER) * 100 + CAST(substr(id, 7, 2) AS INTEGER);

ALTER TABLE event ADD COLUMN updated_at TEXT;

INSERT OR IGNORE INTO repo_meta (key, value)
  SELECT 'catalog_updated_at', value FROM repo_meta WHERE key = 'manifest_generated_at';
