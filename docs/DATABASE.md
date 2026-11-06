# TickerWatch Persistence & Database Specification

**Database Engine:** SQLite 3  
**Journal Mode:** Write-Ahead Logging (WAL)  
**Synchronous Mode:** NORMAL  
**Location:** `tickerwatch.db` (configurable via `DATABASE_URL`)

---

## 1. Concurrency & WAL Performance

TickerWatch is designed as an embedded, local-first engine capable of sustaining high write concurrency without blocking background tasks or UI reads:

- **Write-Ahead Logging (WAL):** Enabled via `PRAGMA journal_mode=WAL;`. Readers never block writers, and writers never block readers.
- **Synchronous NORMAL:** Enabled via `PRAGMA synchronous=NORMAL;`. Guarantees WAL integrity across power cycles while eliminating disk fsync bottlenecks on every single write.
- **Busy Timeout:** `PRAGMA busy_timeout=10000;` ensures SQLite automatically retries for up to 10 seconds if another thread is holding an active write lock.
- **Foreign Keys:** `PRAGMA foreign_keys=ON;` enforces relational integrity.

---

## 2. Table Specifications

### A. `articles`
Stores canonical, normalised financial information records.

```sql
CREATE TABLE articles (
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
```

**Indexes:**
- `idx_articles_connector_source ON articles(connector_id, source_item_id)`
- `idx_articles_published ON articles(published_at_utc DESC)`
- `idx_articles_content_hash ON articles(content_hash)`
- `idx_articles_observed ON articles(observed_at_utc DESC)`

### B. `connector_state`
Persists cursor state and real-time operational status for each connector.

```sql
CREATE TABLE connector_state (
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
```

### C. `ingestion_attempts`
Maintains a durable diagnostic log of all connector polling cycles.

```sql
CREATE TABLE ingestion_attempts (
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

CREATE INDEX idx_ingestion_connector ON ingestion_attempts(connector_id, start_time_utc DESC);
```

### D. `dead_letter_items`
Quarantines unprocessable or malformed records for inspection without stopping batches.

```sql
CREATE TABLE dead_letter_items (
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
```
*Note: `raw_payload` is automatically truncated to 64 KB to bound memory and disk footprint.*

### E. `articles_fts` (Full-Text Search)
SQLite FTS5 virtual table indexing `title` and `snippet`, synchronized via automatic database triggers (`articles_ai`, `articles_ad`, `articles_au`).

```sql
CREATE VIRTUAL TABLE articles_fts USING fts5(
    title,
    snippet,
    content='articles',
    content_rowid='id'
);
```

---

## 3. Transactional Correctness & Idempotency Guarantee

### The Zero-Data-Loss Rule:
1. `save_batch_atomic(...)` executes within an immediate database transaction (`BEGIN IMMEDIATE`).
2. New articles are inserted with `ON CONFLICT(connector_id, source_item_id) DO NOTHING`.
3. If an article was previously persisted, `rowcount == 0` and it is counted as `skipped` without raising an error.
4. The connector's cursor in `connector_state` is updated **in the same transaction**.
5. Only upon successful transaction commit (`conn.commit()`) is the cursor durable.
6. If the application crashes, loses power, or encounters a disk fault during processing:
   - The transaction rolls back.
   - The cursor remains at the previous state.
   - Upon restart, the connector re-fetches the unpersisted batch and stores it without data loss or duplicates.
