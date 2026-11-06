-- Migration 001: Initial TickerWatch Schema
-- Enables Write-Ahead Logging (WAL) and strict constraints

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connector_id TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    publisher TEXT NOT NULL,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at_utc TEXT,
    published_at_original TEXT,
    observed_at_utc TEXT NOT NULL,
    updated_at_utc TEXT,
    content_hash TEXT NOT NULL,
    snippet TEXT,
    raw_metadata TEXT NOT NULL,
    language TEXT DEFAULT 'en',
    tickers TEXT NOT NULL DEFAULT '[]',
    created_at_utc TEXT NOT NULL,
    CONSTRAINT uq_connector_source UNIQUE (connector_id, source_item_id)
);

CREATE INDEX IF NOT EXISTS idx_articles_connector_source ON articles(connector_id, source_item_id);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_articles_content_hash ON articles(content_hash);
CREATE INDEX IF NOT EXISTS idx_articles_observed ON articles(observed_at_utc DESC);

CREATE TABLE IF NOT EXISTS connector_state (
    connector_id TEXT PRIMARY KEY,
    cursor TEXT,
    last_successful_fetch TEXT,
    last_attempted_fetch TEXT,
    last_observed_item TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'HEALTHY',
    last_error TEXT,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connector_id TEXT NOT NULL,
    start_time_utc TEXT NOT NULL,
    completion_time_utc TEXT NOT NULL,
    number_fetched INTEGER NOT NULL DEFAULT 0,
    number_inserted INTEGER NOT NULL DEFAULT 0,
    number_skipped INTEGER NOT NULL DEFAULT 0,
    number_failed INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_details TEXT,
    duration_ms REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_ingestion_connector ON ingestion_attempts(connector_id, start_time_utc DESC);

CREATE TABLE IF NOT EXISTS dead_letter_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connector_id TEXT NOT NULL,
    source_item_id TEXT,
    error_category TEXT NOT NULL,
    error_message TEXT NOT NULL,
    raw_payload TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    first_failure_at_utc TEXT NOT NULL,
    last_failure_at_utc TEXT NOT NULL,
    retry_status TEXT NOT NULL DEFAULT 'NEW'
);

-- Full-Text Search (FTS5) for article titles and snippets
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title,
    snippet,
    content='articles',
    content_rowid='id'
);

-- Triggers to maintain FTS index synchronized with articles table
CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
  INSERT INTO articles_fts(rowid, title, snippet) VALUES (new.id, new.title, new.snippet);
END;

CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
  INSERT INTO articles_fts(articles_fts, rowid, title, snippet) VALUES('delete', old.id, old.title, old.snippet);
END;

CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
  INSERT INTO articles_fts(articles_fts, rowid, title, snippet) VALUES('delete', old.id, old.title, old.snippet);
  INSERT INTO articles_fts(rowid, title, snippet) VALUES (new.id, new.title, new.snippet);
END;
