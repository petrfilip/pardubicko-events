-- Migrace 0003: report běhu pipeline přes API (ADR 0008, etapa 2).

-- Běh posílá pipeline z NanoClaw přes POST /api/v1/runs. Místo cesty k
-- souboru se ukládá celý report a token, který ho poslal.
ALTER TABLE pipeline_run ADD COLUMN actor TEXT;
ALTER TABLE pipeline_run ADD COLUMN report TEXT;

-- Ke kterému běhu stažení patří; podklad pro detail běhu v adminu.
-- Stažení z doby před API běh nemají.
ALTER TABLE source_fetch ADD COLUMN run_id TEXT REFERENCES pipeline_run(id);

CREATE INDEX IF NOT EXISTS idx_pipeline_run_started ON pipeline_run(started_at);
CREATE INDEX IF NOT EXISTS idx_source_fetch_run ON source_fetch(run_id);
