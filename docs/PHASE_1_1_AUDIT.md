# TickerWatch — Phase 1.1: Independent Engineering Audit

**Project:** TickerWatch  
**Phase:** 1.1 (Independent Engineering Audit & Hardening)  
**Status:** Audit Completed & Verified  
**Date:** November 2026  
**Auditor:** Senior Data Infrastructure & Systems Architect  

---

## 1. Executive Summary

Following the completion of Phase 1 (News Ingestion & Normalisation), an independent, adversarial engineering audit of the entire codebase was conducted. The audit focused on identifying silent failure modes, architectural edge cases, transaction durability risks, rate-limiting compliance, async task cancellation safety, and real-world transport reliability.

All 62 automated tests (57 foundational tests + 5 audit regression tests) pass with 100% success. Live integration smoke tests against public RSS feeds and the official SEC EDGAR submissions API were executed and verified end-to-end. Resource consumption benchmarks demonstrate an idle RSS footprint of ~35.8 MB and a peak burst footprint under 40 MB, well within the target budget of 3–4 GB.

---

## 2. Deep-Dive Audit Findings by Area

### 2.1 Database Integrity and Cursor Durability

#### Audited Invariants
- **Atomic Persistence:** Ingestion batches and connector cursor state must commit within a single atomic SQLite transaction.
- **Rollback Safety:** A failure prior to commit must leave the database and cursor unchanged.
- **Idempotency:** Re-ingesting identical articles must not duplicate records, corrupt FTS5 indexes, or inflate storage.
- **Concurrent Access:** Multi-threaded or asynchronous concurrent writes must not corrupt WAL state.

#### Evaluation & Verification
- `Repository.save_batch_atomic()` wraps both article insertions and cursor update in an explicit SQLite transaction (`conn.isolation_level = None`, `BEGIN IMMEDIATE`, `COMMIT` / `ROLLBACK`).
- Articles use `INSERT OR IGNORE` based on the unique constraint `(connector_id, source_item_id)`.
- If any error occurs during batch processing, `conn.rollback()` executes, ensuring zero partial writes. This was verified through `tests/unit/test_database.py::test_transaction_rollback_no_partial_persistence`.
- SQLite WAL mode (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`) ensures concurrent readers do not block writers and vice versa.

---

### 2.2 RSS Connector Audit

#### Audited Invariants
- **Format Support:** RSS 2.0, Atom 1.0, and RDF compatibility.
- **Conditional GET:** Proper negotiation of `ETag` (`If-None-Match`) and `Last-Modified` (`If-Modified-Since`).
- **HTTP 304 Handling:** A 304 response indicates transport success without new content. It must update `last_successful_fetch` without falsely bumping `last_observed_item` or flagging the connector as stale.
- **Lookback Windows:** Overlapping polling windows (default 24h) prevent missing articles published between polls while in-memory content hashes prevent duplicate emissions.
- **Identifier Fallback:** Feeds lacking GUIDs must reliably fall back to canonical URL or deterministic title+timestamp hashing.
- **Relative URLs:** Relative links inside XML feeds must resolve to absolute URLs using `urllib.parse.urljoin`.

#### Remediation & Verification
- **Audit Defect Identified:** Previously, an HTTP 304 response did not cleanly differentiate between transport poll success and source item observation, potentially triggering staleness thresholds on inactive feeds.
- **Fix:** In `backend/connectors/base.py` and `backend/database/repository.py`, separate tracking was introduced for `last_successful_fetch` and `last_observed_item`. Connector health now evaluates `is_source_idle` instead of marking healthy feeds as degraded.
- **Verified in:** `tests/integration/test_audit_regressions.py::test_304_updates_successful_poll_without_advancing_observed_item`.

---

### 2.3 SEC Connector Audit

#### Audited Invariants
- **Official API Compliance:** Compliant with official SEC EDGAR submissions API (`https://data.sec.gov/submissions/CIK{cik}.json`).
- **CIK Normalisation:** Exact 10-digit zero padding (`normalize_cik`).
- **Accession Number Formatting:** Correct transformation between hyphenated accession numbers (`0000320193-24-000106`) and unhyphenated directory paths (`000032019324000106`).
- **Archive URL Construction:** Verified format: `https://www.sec.gov/Archives/edgar/data/{cik_num}/{accession_nodash}/{primary_doc}`.
- **Rate Limiting:** SEC EDGAR automated access policy strictly prohibits exceeding 10 requests/second across an application.
- **User-Agent Invariant:** Requires identifiable User-Agent in the format `Sample Company Name AdminContact@domain.com`.

#### Remediation & Verification
- **Audit Defect Identified (High Severity):** Rate limiting was previously managed via an unshared per-connector timestamp. If multiple SEC connector instances ran concurrently (e.g. across multiple worker tasks or scheduled jobs), aggregate request rates could exceed the 10 req/s ceiling.
- **Fix:** Implemented `SECRateLimiter` as a process-wide, thread-safe shared token bucket rate limiter in `backend/connectors/sec.py`. Default spacing is set to `0.12s` (~8.3 req/s), strictly below the SEC ceiling.
- **Verified in:** `tests/integration/test_audit_regressions.py::test_shared_sec_rate_limiter_across_instances`.

---

### 2.4 Async and Cancellation Audit

#### Audited Invariants
- **Task Lifecycle:** Background tasks spawned via `asyncio.create_task` must be tracked in strong sets to avoid garbage collection.
- **Shutdown Discipline:** Orchestrator shutdown must not cancel tasks mid-transaction without a grace period.
- **Durable Cursor Acknowledgement:** Once a batch is written to the database, connector in-memory cursor acknowledgement must not be skipped due to task cancellation.

#### Remediation & Verification
- **Audit Defect Identified (High Severity):** In `IngestionOrchestrator.ingest_connector()`, `save_batch_atomic()` was protected with `asyncio.shield()`, but `connector.ack()` was outside the shield. If task cancellation struck between the database commit and the in-memory cursor acknowledgement, memory state diverged from disk state.
- **Fix:** Unified database persistence and cursor acknowledgement into a single shielded helper `_persist_and_ack()` in `backend/ingestion/orchestrator.py`.
- **Audit Defect Identified (Medium Severity):** `IngestionScheduler.stop()` immediately called `.cancel()` on active tasks, risking transaction aborts.
- **Fix:** Implemented two-stage graceful shutdown in `IngestionScheduler.stop(timeout_seconds=5.0)`: waits for natural completion of in-flight polls before issuing cancellations.
- **Verified in:** `tests/integration/test_audit_regressions.py::test_atomic_persistence_and_ack_under_cancellation` and `test_scheduler_graceful_shutdown_natural_completion`.

---

### 2.5 Health Monitoring Audit

#### Audited Invariants
- Independent tracking of:
  1. `last_successful_fetch`: Last successful transport handshake (HTTP 200/304).
  2. `last_observed_item`: Publication timestamp of the most recent article observed from the source.
  3. `last_successful_persistence`: Timestamp when new records were committed to SQLite.
  4. `last_error`: Timestamp and error message of the most recent failure.
- Inactive sources or feeds without updates must not degrade to `UNHEALTHY` or `STALE` if transport polling is healthy.

#### Remediation & Verification
- Migration `002_add_persistence_timestamp.sql` added `last_successful_persistence` to `connector_state`.
- `ConnectorHealth` model expanded with `last_successful_persistence_at_utc` and `is_source_idle`.
- Health service evaluates `last_successful_at_utc` for transport freshness and flags `is_source_idle=True` when no new content is present, preserving `ConnectorStatus.HEALTHY`.

---

### 2.6 Database and Search Audit

#### Audited Invariants
- SQLite WAL mode active with normal synchronous mode.
- FTS5 external content table `articles_fts` synchronized via triggers (`after_insert`, `after_delete`, `after_update`).
- `PRAGMA foreign_keys = ON;` enforced on every database connection.
- SQLite integrity check passes without corrupt pages.

#### Evaluation & Verification
- Schema verified with `inspect_database.py`.
- Executed `PRAGMA integrity_check;` returning `ok`.
- FTS5 synchronization verified: article insertions immediately index into `articles_fts` and are searchable via BM25 matching.

---

### 2.7 Security and Configuration Audit

#### Audited Invariants
- `.env` excluded from version control.
- Sensitive credentials scrubbed from logs and API payloads.
- REST API binds to `127.0.0.1` (localhost only) by default.
- Dependencies pinned and lockfile reproducible.
- Windows CryptoAPI root certificate resolution.

#### Remediation & Verification
- `backend/config.py` uses `JSONLogFormatter` with automated key sanitization.
- `get_ssl_context()` implemented in `backend/config.py` to enumerate Windows CryptoAPI `ROOT` and `CA` certificate stores, preventing TLS handshake errors in enterprise environments without disabling certificate verification.
- Verified `.gitignore` prevents repository leakage of `.env`, `*.db`, and temporary files.

---

### 2.8 Live Integration Testing

#### Audited Invariants
- Live network verification of public RSS feeds and SEC EDGAR API.

#### Execution Results
Executed via `scripts/live_smoke_test.py`:
1. **Public RSS Feed (`https://www.sec.gov/news/pressreleases.rss`):**
   - Result: **SUCCESS**
   - Status: HTTP 200 OK
   - Items Fetched: 25 items parsed and normalized.
2. **Official SEC EDGAR JSON API (`https://data.sec.gov/submissions/CIK0000320193.json`):**
   - Result: **SUCCESS**
   - Status: HTTP 200 OK
   - Company Verified: Apple Inc. (CIK 0000320193)
   - Filings Extracted: 1,005 historical & recent filings parsed cleanly.

---

### 2.9 Indian Market Coverage

#### Current Status & Technical Findings
- **Official Stock Exchanges (NSE / BSE):**
  - The National Stock Exchange of India (NSE) and Bombay Stock Exchange (BSE) publish corporate announcements through internal web portals rather than open, unauthenticated XML/RSS endpoints.
  - Public endpoints for NSE (`https://www.nseindia.com/api/corporate-announcements`) enforce strict session-cookie generation, TLS fingerprinting, and Akamai Bot Manager protections. Automated polling via basic HTTP clients without browser session management results in HTTP 401/403 blocks.
- **Operational Solution for Phase 1:**
  - TickerWatch ingests Indian equity corporate announcements and regulatory news using verified, unauthenticated financial RSS feeds:
    - *Economic Times Markets (Stocks/Company News)*
    - *Moneycontrol Corporate Action & Results Feeds*
    - *LiveMint Companies & Markets Feeds*
- **Future Enhancement Architecture:**
  - Phase 3/4 can integrate a headless browser session pool or specialized provider API for direct sub-second exchange announcement streaming.

---

### 2.10 Resource Usage Benchmark

Executed via `scripts/benchmark_resources.py`:

| Metric | Target Budget | Measured Value | Status |
|---|---|---|---|
| **Baseline Idle RSS Memory** | < 100 MB | **35.77 MB** | **PASSED** (Well under budget) |
| **Small Batch Duration (20 items)** | < 50 ms | **< 1.00 ms** | **PASSED** (Instantaneous) |
| **Large Burst Duration (1,000 items)** | < 1,000 ms | **63.00 ms** | **PASSED** (15.8k items/sec) |
| **Peak RSS During 1,000 Items** | < 500 MB | **39.27 MB** (+3.5 MB) | **PASSED** (Zero bloat) |
| **100 Repeated Polling Cycles** | < 1,000 ms | **328.00 ms** (3.28 ms/poll) | **PASSED** |
| **Memory After 100 Cycles** | Flat | **39.36 MB** (+0.09 MB) | **PASSED** (Zero memory leak) |
| **Database Size (1,020 articles + FTS5)** | Compact | **888.00 KB** (WAL: 0 KB) | **PASSED** (Under 1 MB) |

---

## 3. Audit Conclusion & Decision

All identified defects have been remediated with accompanying regression tests. Database transactions are strictly atomic, cursors are durable, rate limits are globally enforced, and memory usage is negligible.

**Recommendation:** Proceed to Phase 2.
