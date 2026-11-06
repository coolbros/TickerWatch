# TickerWatch — Financial Intelligence Engine

> **Local-First, Embedded Financial Information Ingestion & Intelligence**  
> **Phase 1 of 7: News Ingestion & Normalisation**

---

## Overview

TickerWatch is a privacy-preserving, local-first financial intelligence engine designed to continuously monitor public companies, ingest financial news, announcements, and regulatory filings, normalize disparate data streams into a canonical representation, and reliably store them for contextual intelligence and event classification.

### Phase 1 Deliverables
- **Generic RSS/Atom Connector:** Ingests RSS 2.0 and Atom feeds with conditional HTTP caching (`ETag`, `Last-Modified`, `HTTP 304`), rolling lookback deduplication, and relative URL resolution.
- **SEC EDGAR Submissions Connector:** Directly ingests official company filings from `data.sec.gov/submissions/`, formats 10-digit CIKs, constructs archive URLs, and enforces SEC rate limits (<10 req/s) and User-Agent policies.
- **Indian Market Ingestion:** Configurable financial market RSS ingestion (Moneycontrol, Economic Times, BSE/NSE compatible).
- **Canonical Schema & Normalisation:** Standardised `SourceItem` schema with UTC timestamp normalization, HTML entity decoding, whitespace stripping, and deterministic SHA-256 content hashing.
- **SQLite WAL Persistence:** Embedded SQLite database operating in Write-Ahead Logging (WAL) mode with immediate transactions guaranteeing zero cursor loss and strict idempotency on `(connector_id, source_item_id)`.
- **Fault-Tolerant Orchestrator:** Exponential backoff with randomised jitter, `Retry-After` header support, dead-letter quarantine (bounded to 64KB), and cancellation-safe transactions.
- **REST API & Telemetry:** FastAPI service exposing endpoints for health checks, connector monitoring, paginated article queries, and ingestion diagnostics.

---

## Directory Structure

```text
tickerwatch/
│
├── backend/
│   ├── main.py                  # FastAPI app factory & lifespan
│   ├── config.py                # Pydantic Settings & structured JSON logging
│   │
│   ├── connectors/
│   │   ├── base.py              # Abstract BaseConnector (fetch, health, ack)
│   │   ├── rss.py               # Generic RSS/Atom connector
│   │   ├── sec.py               # SEC EDGAR submissions connector
│   │   ├── models.py            # SourceItem, FetchBatch, ConnectorHealth
│   │   └── registry.py          # Dynamic connector discovery & registry
│   │
│   ├── ingestion/
│   │   ├── orchestrator.py      # Core transaction coordinator
│   │   ├── normalizer.py        # Timestamp, URL, and HTML normalisation
│   │   ├── deduplication.py     # Deterministic SHA-256 content hashing
│   │   ├── retry.py             # Exponential backoff with jitter
│   │   └── scheduler.py         # Autonomous polling loops & graceful shutdown
│   │
│   ├── database/
│   │   ├── connection.py        # SQLite WAL connection management
│   │   ├── models.py            # Database row dataclasses
│   │   ├── repository.py        # Atomic persistence & idempotency
│   │   └── migrations/
│   │       └── 001_initial_schema.sql  # DDL tables, indexes, FTS5
│   │
│   ├── monitoring/
│   │   ├── health.py            # System & connector health monitor
│   │   └── metrics.py           # Pipeline stage latency timers
│   │
│   └── api/
│       ├── routes.py            # REST endpoints
│       └── schemas.py           # API request & response schemas
│
├── config/
│   ├── feeds.example.yaml       # Example RSS configuration
│   └── feeds.yaml               # Active RSS feeds config
│
├── tests/
│   ├── fixtures/                # Mock XML and JSON responses
│   ├── unit/                    # Unit test suites (normalizer, DB, SEC, RSS, API)
│   ├── integration/             # Restart durability & error isolation tests
│   └── contract/                # Common connector contract tests
│
├── scripts/
│   ├── run_ingestion.py         # Immediate CLI ingestion runner
│   ├── inspect_database.py      # Database & cursor inspector
│   └── check_connectors.py      # Real-time connector health probe
│
├── docs/
│   ├── PHASE_1_AUDIT.md         # Initial workspace audit
│   ├── PHASE_1_IMPLEMENTATION.md # Architecture walkthrough
│   ├── CONNECTORS.md            # Connector guide & interface reference
│   ├── DATABASE.md              # Database schemas, WAL, & transactions
│   └── PHASE_1_TEST_REPORT.md   # Verification & acceptance test report
│
├── .env.example                 # Template environment variables
├── .gitignore                   # Git ignore patterns
├── pyproject.toml               # Build configuration & pytest settings
└── requirements.lock            # Pinned dependency versions
```

---

## Quickstart

### 1. Prerequisites
- Python 3.11+ (tested on Python 3.12.10)
- `uv` (recommended) or standard `pip`

### 2. Setup Virtual Environment & Dependencies
```powershell
# Navigate into project directory
cd tickerwatch

# Create virtual environment
& "$HOME\.local\bin\uv.exe" venv --python 3.12

# Install pinned dependencies
& "$HOME\.local\bin\uv.exe" pip install --system-certs -r requirements.lock
```

### 3. Environment Configuration
Copy `.env.example` to `.env`:
```powershell
Copy-Item .env.example .env
```
Ensure `SEC_USER_AGENT` contains an identifiable contact string per SEC policy:
```env
SEC_USER_AGENT=TickerWatch/0.1 admin@example.com
```

### 4. Run Automated Test Suite
```powershell
.\.venv\Scripts\pytest.exe -v
```
All 57 automated tests should pass.

### 5. Start the REST API Service
```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```
API docs available at: `http://127.0.0.1:8000/docs`

---

## CLI Utilities

### Manual Ingestion Pass
Execute an immediate ingestion cycle for all active connectors or a specific target:
```powershell
.\.venv\Scripts\python.exe scripts/run_ingestion.py
# Or for a specific connector:
.\.venv\Scripts\python.exe scripts/run_ingestion.py --connector sec_edgar
```

### Inspect Stored Articles and Cursors
```powershell
.\.venv\Scripts\python.exe scripts/inspect_database.py
```

### Check Connector Health & Freshness
```powershell
.\.venv\Scripts\python.exe scripts/check_connectors.py
```

---

## REST Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Consolidated system health and connector freshness states. |
| `GET` | `/connectors` | List of all registered connectors and committed cursors. |
| `GET` | `/connectors/{id}/health` | Detailed health and failure metrics for a specific connector. |
| `GET` | `/articles` | Query paginated articles with optional `?ticker=AAPL` or `?connector_id=...` |
| `GET` | `/articles/{id}` | Retrieve individual canonical article by integer ID. |
| `GET` | `/stats/ingestion` | Aggregate ingestion performance metrics and dead-letter count. |
| `POST` | `/connectors/{id}/trigger` | Manually trigger a single ingestion pass for a connector. |

---

## Transactional Integrity & Idempotency Guarantee

TickerWatch guarantees that an item will never be lost due to process crashes or network interruptions:
1. Incoming batches are validated and normalised into canonical `SourceItem` instances.
2. In `save_batch_atomic(...)`, all articles and the connector's updated cursor are written **in the same SQLite transaction** (`BEGIN IMMEDIATE`).
3. If the process is halted before the transaction commits, the cursor is not advanced. On restart, the connector re-fetches the batch and the database idempotency constraint `UNIQUE (connector_id, source_item_id)` suppresses duplicate rows.
4. Connectors only acknowledge cursors (`ack(cursor)`) after durable database persistence is confirmed.
