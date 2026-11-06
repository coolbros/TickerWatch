# Phase 1 Audit: TickerWatch News Ingestion & Normalisation

**Date:** 2026-11-06  
**Auditor:** Senior Backend & Data Infrastructure Architect  
**Project:** TickerWatch (Phase 1 of 7)  
**Status:** Audit Complete — Implementation Starting

---

## 1. Existing Architecture & Repository State

An audit of the workspace (`c:\Users\Agastya\Downloads`) and surrounding environment was conducted prior to code implementation:

- **Repository Root:** No pre-existing TickerWatch repository or codebase was found in the workspace. An empty git repository was initialized in `tickerwatch/` to establish a clean, modular Python backend conforming to the specification.
- **Specification Document:** `TickerWatch_30_Page_Project_and_Implementation_Plan.docx` was not present in the workspace. The architecture and requirements are therefore derived strictly from the comprehensive Phase 1 Master Prompt specification.
- **Runtime Environment:**
  - Operating System: Windows 11 (x86_64)
  - Python Environment: Python 3.12.10 (installed via winget into AppData Local Programs)
  - Package Management: `uv` 0.13.7 with Windows System Certificates enabled for reliable package resolution.
  - Virtual Environment: `.venv` created targeting Python 3.12.

---

## 2. Components That Can Be Reused

Because this is a greenfield initialization for TickerWatch:
- Standard library components (`asyncio`, `sqlite3`, `hashlib`, `logging`, `datetime`, `urllib`) are leveraged directly.
- Standard high-performance ecosystem libraries (`fastapi`, `pydantic` v2, `pydantic-settings`, `httpx`, `feedparser`, `python-dateutil`, `pyyaml`, `pytest`, `pytest-asyncio`, `respx`) provide the core primitives without bloated external services.

---

## 3. Missing Components (To Be Implemented in Phase 1)

1. **Configuration & Logging:**
   - Type-safe Pydantic Settings reading from `.env` and environment variables.
   - Structured JSON logging preserving audit trails and latency instrumentation.
2. **Canonical Data Models & Normalisation:**
   - `SourceItem` schema with UTC timestamps, provenance, content hashing, and raw metadata.
   - `FetchBatch`, cursor state models, and connector health metrics.
   - Resilient normalisation pipeline handling missing dates, relative URLs, HTML entities, and malformed fields.
3. **Storage & Persistence:**
   - SQLite with Write-Ahead Logging (WAL) and synchronous=NORMAL.
   - Schema tables: `articles`, `connector_state`, `ingestion_attempts`, `dead_letter_items`.
   - Migration management framework for reproducible versioned DDL schemas.
   - Transactional repository guaranteeing that cursor progression is atomic with record persistence.
4. **Connectors:**
   - `BaseConnector` abstract base class defining `fetch(cursor)`, `health()`, and `ack(cursor)`.
   - `RSSConnector`: Generic RSS 2.0 / Atom reader with HTTP ETag / Last-Modified caching and lookback deduplication.
   - `SECConnector`: EDGAR JSON company submissions ingestion with CIK padding, rate limiting, and required User-Agent validation.
   - Indian market RSS integration config and endpoint verification guide (NSE/BSE).
5. **Orchestration & Fault Tolerance:**
   - Independent connector scheduling with non-blocking async execution.
   - Exponential backoff with randomised jitter for transient errors (429, 5xx, timeouts).
   - Dead-letter queue tracking permanent failures without crashing batches.
   - Graceful shutdown handlers and restart-recovery durability.
6. **Observability & REST API:**
   - Health check endpoints (`/health`, `/connectors`, `/connectors/{id}/health`).
   - Query endpoints (`/articles`, `/articles/{id}`, `/stats/ingestion`).
7. **Verification & Testing:**
   - Fixture-driven unit tests, mock HTTP tests (`respx`), connector contract tests, and end-to-end restart/idempotency tests.

---

## 4. Conflicts With Intended Architecture

No conflicts were detected. The design adheres strictly to the local-first, low-resource profile:
- **No Heavy Infrastructure:** Strictly no Redis, Kafka, PostgreSQL, or Kubernetes.
- **Embedded Engine:** SQLite with WAL mode satisfies the high write throughput and zero-configuration requirement.
- **Strict Scope Boundary:** Generative AI, embeddings, mini-models, and sentiment analysis are explicitly excluded from Phase 1.

---

## 5. Proposed Implementation Changes & Structure

The repository structure is established as specified:

```text
tickerwatch/
├── backend/
│   ├── main.py
│   ├── config.py
│   ├── connectors/
│   │   ├── base.py
│   │   ├── rss.py
│   │   ├── sec.py
│   │   ├── models.py
│   │   └── registry.py
│   ├── ingestion/
│   │   ├── orchestrator.py
│   │   ├── normalizer.py
│   │   ├── deduplication.py
│   │   ├── retry.py
│   │   └── scheduler.py
│   ├── database/
│   │   ├── connection.py
│   │   ├── models.py
│   │   ├── repository.py
│   │   └── migrations/
│   ├── monitoring/
│   │   ├── health.py
│   │   └── metrics.py
│   └── api/
│       ├── routes.py
│       └── schemas.py
├── config/
│   ├── feeds.example.yaml
│   └── feeds.yaml
├── tests/
│   ├── fixtures/
│   ├── unit/
│   ├── integration/
│   └── contract/
├── scripts/
│   ├── run_ingestion.py
│   ├── inspect_database.py
│   └── check_connectors.py
├── docs/
│   ├── PHASE_1_AUDIT.md
│   ├── PHASE_1_IMPLEMENTATION.md
│   ├── CONNECTORS.md
│   ├── DATABASE.md
│   └── PHASE_1_TEST_REPORT.md
├── .env.example
├── .gitignore
├── pyproject.toml
├── README.md
└── requirements.lock
```

---

## 6. External Dependencies & Credentials

- **External Credentials:** No paid API keys are required.
- **SEC User-Agent Policy:** Requires an identifiable `User-Agent` containing application name and an admin email (e.g., `TickerWatch/0.1 admin@example.com`). This is enforced via configuration validation (`SEC_USER_AGENT`).
- **Python Libraries:** `fastapi`, `uvicorn`, `pydantic`, `pydantic-settings`, `httpx`, `feedparser`, `python-dateutil`, `pyyaml`, `pytest`, `pytest-asyncio`, `respx`. All locked and installed.
