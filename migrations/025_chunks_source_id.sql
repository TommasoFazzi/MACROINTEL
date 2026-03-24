-- Migration 025: Add source_id to chunks table + extraction_method to articles
-- Links chunks directly to intelligence_sources for authority-weighted RAG retrieval
-- Also stores extraction_method so LLM knows when text came from PDF (pymupdf4llm)

-- 1. Add source_id FK to chunks
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS source_id INTEGER
    REFERENCES intelligence_sources(id);
CREATE INDEX IF NOT EXISTS idx_chunks_source_id ON chunks(source_id);

-- 2. Add extraction_method to articles (trafilatura, newspaper3k, pymupdf4llm, etc.)
ALTER TABLE articles ADD COLUMN IF NOT EXISTS extraction_method TEXT;

-- 3. Backfill chunks.source_id from articles.source_id
UPDATE chunks c SET source_id = a.source_id
FROM articles a
WHERE c.article_id = a.id
  AND c.source_id IS NULL
  AND a.source_id IS NOT NULL;

-- Verify
SELECT 'chunks with source_id' as metric, COUNT(*) as value
FROM chunks WHERE source_id IS NOT NULL
UNION ALL
SELECT 'chunks without source_id', COUNT(*)
FROM chunks WHERE source_id IS NULL;
