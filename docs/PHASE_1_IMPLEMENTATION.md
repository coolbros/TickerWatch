# Phase 1 Implementation Guide: News Ingestion & Normalisation

**Project:** TickerWatch  
**Phase:** 1 of 7  
**Status:** Completed and Verified

---

## 1. Architectural Highlights

Phase 1 provides the foundational data acquisition and normalization engine for TickerWatch. The architecture adheres strictly to:
- **Zero Heavy Infrastructure:** No Kafka, Redis, or external SQL servers. Runs entirely on an embedded, WAL-mode SQLite database with asynchronous Python (`httpx`, `asyncio`, `FastAPI`).
- **Low Memory Footprint:** Bounded queues, streaming deserialization, and bounded dead-letter storage keeping runtime RAM well under 100 MB.
- **Durable Idempotency:** Duplicate delivery from feeds or restarts never duplicates rows or corrupts database state.
- **Extensible Connectors:** Connectors share a clean interface and are scheduled independently.

```
                    ┌────────────────────────┐
                    │    External Sources    │
                    │ (RSS, SEC EDGAR, etc.) │
                    └───────────┬────────────┘
                                │ HTTP / JSON / XML
                                ▼
                    ┌────────────────────────┐
                    │     BaseConnector      │
                    │  (RSS / SEC Adapters)  │
                    └───────────┬────────────┘
                                │ Raw Records
                                ▼
                    ┌────────────────────────┐
                    │  Normalisation Service │
                    │ (HTML clean, UTC parse,│
                    │  deterministic SHA-256)│
                    └───────────┬────────────┘
                                │ Canonical SourceItem
                                ▼
                    ┌────────────────────────┐
                    │ Ingestion Orchestrator │
                    │ (Retry, Shield, Ack)   │
                    └───────────┬────────────┘
                                │ Atomic Transaction
                                ▼
       ┌──────────────────────────────────────────────────┐
       │             SQLite Database (WAL Mode)           │
       │  - articles (UNIQUE connector_id, source_id)     │
       │  - connector_state (durable cursors)             │
       │  - ingestion_attempts (diagnostics)              │
       │  - dead_letter_items (quarantined errors)        │
       │  - articles_fts (FTS5 search index)              │
       └──────────────────────────────────────────────────┘
```

---

## 2. Core Modules Implemented

| Module | Location | Purpose |
|---|---|---|
| **Configuration** | `backend/config.py` | Pydantic Settings, environment loading, structured JSON logging. |
| **Connector Models** | `backend/connectors/models.py` | `SourceItem`, `FetchBatch`, `ConnectorHealth`, `ConnectorStatus`. |
| **Base Connector** | `backend/connectors/base.py` | Abstract `BaseConnector` (`fetch`, `health`, `ack`). |
| **RSS Connector** | `backend/connectors/rss.py` | Generic RSS 2.0 / Atom reader with ETag and Last-Modified caching. |
| **SEC Connector** | `backend/connectors/sec.py` | Official EDGAR submissions API reader with rate pacing and CIK padding. |
| **Connector Registry** | `backend/connectors/registry.py` | Discovery and lifecycle management for active connectors. |
| **Normaliser** | `backend/ingestion/normalizer.py` | Whitespace cleaning, HTML stripping, UTC parsing, URL normalisation. |
| **Deduplication** | `backend/ingestion/deduplication.py` | Stable source item ID generation and deterministic SHA-256 hashing. |
| **Retry & Resilience** | `backend/ingestion/retry.py` | Exponential backoff, randomised jitter, Retry-After parsing. |
| **Orchestrator** | `backend/ingestion/orchestrator.py` | Coordinates atomic persistence, cursor commit, and ack. |
| **Scheduler** | `backend/ingestion/scheduler.py` | Autonomous polling loops with concurrency limits and graceful shutdown. |
| **Connection & Migrations** | `backend/database/connection.py` | SQLite connection pooling, WAL mode, versioned migrations. |
| **Repository** | `backend/database/repository.py` | Atomic transactions, duplicate suppression, query pagination. |
| **Health Monitoring** | `backend/monitoring/health.py` | Real-time health aggregation and staleness detection. |
| **API Endpoints** | `backend/api/routes.py` | REST API routes (`/health`, `/connectors`, `/articles`, `/stats`). |
| **Application Factory** | `backend/main.py` | FastAPI application lifecycle with lifespan management. |

---

## 3. Resilience and Graceful Shutdown

1. **Cancellation Safety (`asyncio.shield`):**
   When the application is interrupted (SIGINT, SIGTERM, or task cancellation), any in-progress database transaction (`save_batch_atomic`) is shielded, ensuring the batch finishes committing or safely rolls back without corrupting SQLite or leaving dangling cursors.
2. **Autonomous Connector Loops:**
   Each connector runs on its own schedule. If a feed times out or returns HTTP 500, only that connector's task backs off; other connectors continue polling without degradation.
3. **Graceful Shutdown (`IngestionScheduler.stop`):**
   Sets a shutdown event, signals loops to finish their current cycle, and waits up to 5 seconds before cancelling tasks.
