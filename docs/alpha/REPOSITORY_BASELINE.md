# TickerWatch Alpha — Repository Baseline & Architecture Audit

> **Document Version:** 1.0.0-alpha  
> **Status:** EMPIRICAL REPOSITORY AUDIT — PHASE 0  
> **Author:** Core Architecture & Systems Engineering  
> **Repository:** `TickerWatch` (`coolbros/TickerWatch`)  
> **Current Branch:** `alpha-research` (branched cleanly from `main`)  

---

## 1. Actual Repository Structure

An exhaustive physical inspection of the working tree reveals the following structure:

```text
tickerwatch/
├── backend/
│   ├── __init__.py
│   ├── config.py                 # Pydantic Settings, JSON logging, Windows SSL context
│   ├── main.py                   # FastAPI app factory, lifespan, dependency injection
│   ├── api/
│   │   ├── __init__.py
│   │   ├── dashboard.py          # Embedded HTML/JS single-page web dashboard
│   │   ├── routes.py             # FastAPI APIRouter endpoints
│   │   └── schemas.py            # Pydantic request/response validation schemas
│   ├── connectors/
│   │   ├── __init__.py
│   │   ├── base.py               # Abstract BaseConnector (fetch, ack, health)
│   │   ├── models.py             # SourceItem, FetchBatch, ConnectorHealth, ConnectorStatus
│   │   ├── registry.py           # ConnectorRegistry dynamic loading from config
│   │   ├── rss.py                # Generic RSS/Atom connector with ETag/304 caching
│   │   └── sec.py                # SEC EDGAR JSON submissions connector with shared rate limiter
│   ├── database/
│   │   ├── __init__.py
│   │   ├── connection.py         # SQLite WAL connection manager, PRAGMA setup, migration runner
│   │   ├── models.py             # ArticleRow, ConnectorStateRow, DeadLetterRow dataclasses
│   │   ├── repository.py         # Thread-safe atomic batch persistence, deduplication, queries
│   │   └── migrations/
│   │       └── 001_initial_schema.sql  # DDL: articles, connector_state, dead_letter, FTS5
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── deduplication.py      # SHA-256 deterministic content hashing
│   │   ├── normalizer.py         # Clean HTML, parse RFC 822 / ISO 8601 timestamps, URL cleanup
│   │   ├── orchestrator.py       # Central pipeline coordinator (fetch -> normalize -> persist -> ack)
│   │   ├── retry.py              # Exponential backoff with jitter and Retry-After support
│   │   └── scheduler.py          # Asyncio background polling loops with graceful shutdown
│   └── monitoring/
│       ├── __init__.py
│       ├── health.py             # HealthService consolidating system and connector states
│       └── metrics.py            # Latency timers and stage performance accumulators
├── config/
│   ├── feeds.example.yaml        # Template RSS feed list
│   └── feeds.yaml                # Active RSS feed list (SEC, Yahoo, Moneycontrol India)
├── docs/
│   ├── CONNECTORS.md             # Connector interface & implementation guide
│   ├── DATABASE.md               # SQLite WAL schema, indexes, transaction guarantees
│   ├── PHASE_1_AUDIT.md          # Historical audit report
│   ├── PHASE_1_IMPLEMENTATION.md # Architecture walkthrough of news ingestion
│   ├── PHASE_1_TEST_REPORT.md    # Initial test report (57 tests)
│   ├── PHASE_1_1_AUDIT.md        # Independent engineering audit
│   ├── PHASE_1_1_REMEDIATION.md  # Detailed fixes applied in Phase 1.1
│   ├── PHASE_1_1_TEST_REPORT.md  # Acceptance test report (62 tests)
│   └── alpha/                    # Phase 0 TickerWatch Alpha Specifications
├── scripts/
│   ├── benchmark_resources.py    # Memory (RSS) and throughput benchmarking script
│   ├── check_connectors.py       # Live connector health probe CLI
│   ├── inspect_database.py       # Database inspection & article summary CLI
│   ├── live_smoke_test.py        # Live network smoke test (SEC EDGAR & RSS)
│   └── run_ingestion.py          # Single-pass manual ingestion runner CLI
├── tests/
│   ├── __init__.py
│   ├── contract/
│   │   ├── __init__.py
│   │   └── test_connector_contract.py  # BaseConnector contract tests (RSS, SEC)
│   ├── fixtures/
│   │   ├── empty_feed.xml
│   │   ├── missing_dates.xml
│   │   ├── relative_urls.xml
│   │   ├── sec_submissions_sample.json
│   │   ├── valid_atom.xml
│   │   └── valid_rss2.xml
│   ├── integration/
│   │   ├── __init__.py
│   │   ├── test_audit_regressions.py   # Regression tests for Phase 1.1 audit findings
│   │   ├── test_failure_isolation.py   # Error isolation across connectors
│   │   └── test_restart_durability.py  # Crash recovery & duplicate suppression
│   └── unit/
│       ├── __init__.py
│       ├── test_api.py                 # FastAPI route tests via TestClient
│       ├── test_database.py            # SQLite migration, rollback, idempotency tests
│       ├── test_normalizer.py          # Normalisation & hashing unit tests
│       ├── test_resilience.py          # Exponential backoff and jitter tests
│       ├── test_rss_connector.py       # RSS connector parsing & caching tests
│       └── test_sec_connector.py       # SEC connector submissions parsing tests
├── .env                          # Local environment variables
├── .env.example                  # Template configuration
├── .gitignore                    # Git ignore file
├── pyproject.toml                # Project metadata, dependencies, pytest configuration
├── requirements.lock             # Exact pinned dependency lockfile
└── tickerwatch.db                # SQLite database (contains 3,043 live records)
```

---

## 2. Existing Database Schema

Defined in `backend/database/migrations/001_initial_schema.sql` and verified live via `scripts/inspect_database.py`:

1. **`articles` Table:**
   - Columns: `id` (INTEGER PK AUTOINCREMENT), `connector_id` (TEXT), `source_item_id` (TEXT), `publisher` (TEXT), `source_type` (TEXT), `title` (TEXT), `url` (TEXT), `published_at_utc` (TEXT), `published_at_original` (TEXT), `observed_at_utc` (TEXT), `updated_at_utc` (TEXT), `content_hash` (TEXT), `snippet` (TEXT), `raw_metadata` (TEXT JSON), `language` (TEXT), `tickers` (TEXT JSON), `created_at_utc` (TEXT).
   - Constraint: `UNIQUE (connector_id, source_item_id)` ensures strict per-connector idempotency.
   - Indexes on: `(connector_id, source_item_id)`, `published_at_utc DESC`, `content_hash`, `observed_at_utc DESC`.
2. **`connector_state` Table:**
   - Columns: `connector_id` (TEXT PK), `cursor` (TEXT JSON), `last_successful_fetch` (TEXT), `last_attempted_fetch` (TEXT), `last_observed_item` (TEXT), `consecutive_failures` (INTEGER), `status` (TEXT), `last_error` (TEXT), `updated_at_utc` (TEXT).
3. **`ingestion_attempts` Table:**
   - Columns: `id` (INTEGER PK), `connector_id` (TEXT), `start_time_utc` (TEXT), `completion_time_utc` (TEXT), `number_fetched` (INTEGER), `number_inserted` (INTEGER), `number_skipped` (INTEGER), `number_failed` (INTEGER), `retry_count` (INTEGER), `error_details` (TEXT), `duration_ms` (REAL).
4. **`dead_letter_items` Table:**
   - Columns: `id` (INTEGER PK), `connector_id` (TEXT), `source_item_id` (TEXT), `error_category` (TEXT), `error_message` (TEXT), `raw_payload` (TEXT bounded to 64KB), `retry_count` (INTEGER), `first_failure_at_utc` (TEXT), `last_failure_at_utc` (TEXT), `retry_status` (TEXT).
5. **`articles_fts` (Full-Text Search):**
   - SQLite FTS5 virtual table indexing `title` and `snippet`, synced via triggers `articles_ai`, `articles_ad`, `articles_au`.

---

## 3. Existing API Endpoints

FastAPI router mounted at root in `backend/main.py`:
- `GET /` — Interactive web dashboard (HTML/JS) displaying live system metrics, articles, and connector statuses.
- `GET /health` — Consolidated health report (`HealthResponse`) including database, system status, and individual connector statuses.
- `GET /connectors` — List of all registered connectors with active cursors and health metrics.
- `GET /connectors/{connector_id}/health` — Deep diagnostic health report for a specific connector.
- `GET /articles` — Paginated article feed with optional `?ticker=...` and `?connector_id=...` query filters.
- `GET /articles/{id}` — Single canonical article retrieval.
- `GET /stats/ingestion` — Aggregate throughput metrics, counts, and dead-letter statistics.
- `POST /connectors/{connector_id}/trigger` — Immediate manual execution of an ingestion cycle.

---

## 4. Existing Connectors & Ingestion Pipeline

1. **`BaseConnector` (`backend/connectors/base.py`):**
   Abstract interface defining `fetch(cursor) -> FetchBatch`, `ack(cursor) -> None`, and `health() -> ConnectorHealth`. Separates transport health from content freshness (handles HTTP 304 without corrupting observation timestamps).
2. **`RSSConnector` (`backend/connectors/rss.py`):**
   Ingests RSS 2.0 and Atom feeds. Implements HTTP 304 conditional polling using `ETag` and `If-Modified-Since` headers. Maintains a rolling lookback window of 100 seen IDs in the cursor for feeds with unstable identifiers.
3. **`SECConnector` (`backend/connectors/sec.py`):**
   Ingests official company filings from `data.sec.gov/submissions/`. Enforces SEC-mandated User-Agent headers, 10-digit CIK zero-padding, archive URL reconstruction, and a process-wide shared rate limiter (`SharedRateLimiter` $< 10\text{ req/s}$).
4. **`ConnectorRegistry` (`backend/connectors/registry.py`):**
   Dynamically instantiates connectors declared in `config/feeds.yaml` and SEC settings from environment variables.
5. **`IngestionOrchestrator` (`backend/ingestion/orchestrator.py`):**
   Coordinates atomic ingestion cycles: executes fetch, normalizes items, persists new records and updated cursor within a single atomic SQLite transaction (`BEGIN IMMEDIATE`), and calls `connector.ack()`. Quarantines malformed items to `dead_letter_items`.
6. **`IngestionScheduler` (`backend/ingestion/scheduler.py`):**
   Asyncio background polling loop managing concurrency with a bounded semaphore (`max_connector_concurrency=2`) and graceful two-stage shutdown.

---

## 5. Current Dependencies and Environment Configuration

### Dependencies (`pyproject.toml` & `requirements.lock`):
- **Runtime:** `fastapi>=0.115.0`, `uvicorn>=0.30.0`, `pydantic>=2.8.0`, `pydantic-settings>=2.4.0`, `httpx>=0.27.0`, `feedparser>=6.0.11`, `pyyaml>=6.0.2`, `python-dateutil>=2.9.0`.
- **Development/Test:** `pytest>=8.0.0`, `pytest-asyncio>=0.23.0`, `respx>=0.21.0`.

### Environment Configuration (`backend/config.py`):
- `DATABASE_URL`: `sqlite:///tickerwatch.db` (WAL mode enabled by default).
- `LOG_LEVEL`: `INFO` (outputs structured JSON logs via `JSONLogFormatter`).
- `MAX_CONNECTOR_CONCURRENCY`: `2`.
- `MAX_RETRIES`: `5`, `BACKOFF_BASE_SECONDS`: `2.0`, `BACKOFF_MAX_SECONDS`: `60.0`.
- `RSS_ENABLED`: `True`, `RSS_FEEDS_CONFIG`: `config/feeds.yaml`.
- `SEC_ENABLED`: `True`, `SEC_USER_AGENT`: `TickerWatch/0.1 admin@example.com`.

---

## 6. Empirical Test Verification & Architectural Findings

During our Phase 0 audit, automated test execution was directly evaluated:

### 6.1 Passing Isolated Unit Tests
- `tests/unit/test_normalizer.py`: **13 passed in 0.13s** (100% pass rate). Validates HTML stripping, RFC 822 / ISO 8601 parsing, URL normalization, SHA-256 content hashing, and stable source ID generation.
- `tests/unit/test_database.py`: **7 passed in 0.32s** (100% pass rate). Validates migrations, atomic batch persistence, deduplication suppression, transaction rollback, and dead-letter quarantine.

### 6.2 Architectural Findings & Sandbox Constraints
1. **Lifespan Network Polling in `test_api.py`:**  
   In `backend/main.py`, `create_app()` initializes background polling tasks during lifespan startup for all enabled connectors in `config/feeds.yaml` (`yahoo_finance_top`, `moneycontrol_market_reports`). When `TestClient` initializes in a sandboxed or network-isolated environment, background tasks attempt live outbound HTTP connections, leading to socket timeout delays. In Phase 1, `test_app` fixtures must explicitly disable RSS/SEC polling or mock transport.
2. **Windows CryptoAPI / Root Certificates:**  
   `backend/config.py` contains `get_ssl_context()`, which enumerates Windows root certificates via `ssl.enum_certificates("ROOT")`. Under sandboxed execution policies, Win32 CryptoAPI access requires elevated privileges or fallback to standard certifi bundles.
3. **SQLite Concurrency & Multi-Process Locks:**  
   SQLite WAL mode allows multiple concurrent readers and one writer. While highly performant for Phase 1 news ingestion (benchmarked at $>15,000\text{ items/s}$), high-frequency tick market data requires separate dedicated memory buffers to avoid write-lock contention.

---

## 7. Proposed Logical Repository Separation

To allow **TickerWatch Alpha** to evolve into a high-performance research engine without destabilizing or rewriting the original ingestion pipeline, we propose a clean logical separation into three distinct packages:

```text
tickerwatch/
├── backend/                       # LEGACY / SHARED NEWS INGESTION
│   ├── connectors/               # Ingestion connectors (RSS, SEC, BSE/NSE)
│   ├── database/                 # SQLite persistence & migrations
│   ├── ingestion/                # Orchestrator, normalizer, scheduler
│   └── monitoring/               # Health and ingestion telemetry
│
├── alpha/                         # TICKERWATCH ALPHA PREDICTIVE SYSTEM
│   ├── data/                     # Market data adapters, L2 order book feeds, tick ring-buffers
│   ├── features/                 # Microstructure features (OFI, spread, book imbalance)
│   ├── models/                   # Financial NLP (FinBERT/ModernBERT), ONNX runtime export
│   ├── retrieval/                # HNSW historical event retrieval & prior distributions
│   ├── prediction/               # Multi-horizon forecast engine P(Delta F | E, H, M)
│   ├── simulation/               # Execution simulator, cost models, slippage & latency ablation
│   └── research/                 # Walk-forward cross-validation, notebooks, experiment tracking
│
├── shared/                        # COMMON CROSS-CUTTING UTILITIES
│   ├── clock.py                  # High-resolution monotonic and UTC timestamping
│   ├── schemas.py                # Unified event and market data dataclasses
│   └── logging.py                # Structured JSON logging
│
├── docs/
│   ├── alpha/                    # Alpha research specifications (Phase 0)
│   └── ...                       # Original Phase 1 documentation
```

### Transition Rule:
This separation is a **future organizational roadmap**, NOT an immediate refactor. In Phase 0, zero legacy imports or files have been modified. The existing ingestion foundation remains 100% intact.
