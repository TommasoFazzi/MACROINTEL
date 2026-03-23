-- Migration 024: Intelligence Sources Matrix
-- Crea l'anagrafica delle fonti con autorevolezza e dominio tematico.
-- Aggiunge source_id (FK) e domain alla tabella articles.

-- ============================================================
-- Tabella intelligence_sources
-- ============================================================
CREATE TABLE IF NOT EXISTS intelligence_sources (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    domain          TEXT NOT NULL,          -- cyber|tech|supply_chain|economics|defense|geopolitics|intelligence
    source_type     TEXT,                   -- Think Tank, Investigativo, Tecnico, OSINT, Ufficiale/Gov, ...
    authority_score NUMERIC(3,1) NOT NULL CHECK (authority_score BETWEEN 1.0 AND 5.0),
    llm_context     TEXT,                   -- contesto da iniettare nei prompt LLM per questa fonte
    feed_names      TEXT[] DEFAULT '{}',    -- nomi esatti nel campo articles.source (da feeds.yaml)
    has_rss         BOOLEAN DEFAULT FALSE,  -- TRUE se feed RSS attivo in feeds.yaml
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_intelligence_sources_domain
    ON intelligence_sources(domain);

CREATE INDEX IF NOT EXISTS idx_intelligence_sources_feed_names
    ON intelligence_sources USING GIN(feed_names);

COMMENT ON TABLE intelligence_sources IS
    'Anagrafica fonti con autorevolezza (1-5) e dominio tematico per RAG e Oracle.';
COMMENT ON COLUMN intelligence_sources.feed_names IS
    'Nomi feed esatti memorizzati in articles.source (derivati da feeds.yaml). Usati per backfill e lookup in ingestion.';
COMMENT ON COLUMN intelligence_sources.authority_score IS
    '1.0-5.0: peso autorevolezza. 5.0 = ufficiale/gold standard, 3.0 = cronaca regionale.';
COMMENT ON COLUMN intelligence_sources.domain IS
    'Dominio tematico: cyber | tech | supply_chain | economics | defense | geopolitics | intelligence';

-- ============================================================
-- Colonne su articles
-- ============================================================
ALTER TABLE articles
    ADD COLUMN IF NOT EXISTS source_id INTEGER REFERENCES intelligence_sources(id),
    ADD COLUMN IF NOT EXISTS domain    TEXT;

CREATE INDEX IF NOT EXISTS idx_articles_source_id ON articles(source_id);
CREATE INDEX IF NOT EXISTS idx_articles_domain    ON articles(domain);

COMMENT ON COLUMN articles.source_id IS
    'FK a intelligence_sources. Popolato al momento del salvataggio (Level 2) o via backfill (seed_sources.py).';
COMMENT ON COLUMN articles.domain IS
    'Denormalizzato da intelligence_sources.domain per query rapide senza JOIN.';
