# Phase 4 Persistent Historical Event Store Architecture

**Document Version:** 1.0.0-alpha  
**Date:** 2026-11-10  
**Phase:** Phase 4 — Historical Event Vector Retrieval & Contextual Intelligence  
**Author:** Agastya Varshney <agastyavarshney@gmail.com>  
**Status:** Canonical Storage Specification  

---

## 1. Executive Summary

To support reproducible historical intelligence and prevent state loss across restarts, Work Package C establishes a persistent, local-first relational and vector store implemented in `backend/alpha/historical_store.py` (`PersistentHistoricalEventStore`).

Designed around SQLite Write-Ahead Logging (WAL), the store cleanly segregates:
1. **Canonical Event Metadata & NLP Extractions** (`historical_events`)
2. **Multi-Entity Instrument Associations** (`historical_event_instruments`)
3. **Immutable Revision Audit Logs** (`historical_event_revisions`)
4. **Model-Versioned Dense Vector Embeddings** (`historical_event_embeddings`)
5. **Matured Future Market Outcomes** (`historical_event_outcomes`)

This architecture strictly guarantees zero look-ahead leakage at the SQL query boundary while eliminating unnecessary external server daemons.

---

## 2. Storage Option Evaluation

Before selecting SQLite WAL, candidate local-first and external infrastructure options were benchmarked against TickerWatch Alpha requirements:

| Architecture Option | Operational Complexity | ACID / Crash Durability | Causal SQL Filtering | Vector Search Speed (<100K events) | Decision |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **SQLite WAL + Packed Float32 BLOBs** | Zero (single embedded file) | Full ACID transactions; crash-safe | Native indexed B-Trees (`< 1 ms`) | Exact Cosine Scan in NumPy (`~1.2 ms` for 10K) | **SELECTED** |
| External Vector DB (Qdrant / Milvus) | High (Docker/Daemon required) | Eventual consistency risks | Requires double-sync across DBs | Sub-millisecond ANN | Rejected (Over-engineered) |
| Redis / Kafka Event Bus | High (RAM-bound / Broker config) | Complex checkpointing | Poor ad-hoc time-range queries | Not native | Rejected (Prohibited without need) |
| DuckDB + Parquet | Low-Medium | Optimized for OLAP analytics, weak row-level upserts | Fast columnar scan | Vectorized dot product | Deferred for offline bulk export |

**Architectural Conclusion:** Exact similarity scanning over dense vectors stored as packed 32-bit floats in SQLite BLOBs delivers $< 2\text{ ms}$ query latency for corpora up to $50,000+$ events with zero auxiliary daemon deployment and ACID revision control.

---

## 3. Relational Schema & Physical Separation

```
+---------------------------------------------------------------------------------------------------+
|                                   PERSISTENT STORE SCHEMA ARCHITECTURE                             |
+---------------------------------------------------------------------------------------------------+

  +------------------------------------------+       +------------------------------------------+
  |            historical_events             | 1   N |      historical_event_instruments        |
  |------------------------------------------|-------|------------------------------------------|
  | PK event_id                              |       | PK (event_id, instrument_id)             |
  |    source_article_id, revision_number    |       |    relevance_tier (DIRECT / CONTEXTUAL)  |
  |    content_hash (SHA-256)                |       +------------------------------------------+
  |    jurisdiction (IN / US / GLOBAL)       |
  |    headline, body_snippet, metrics_json  |       +------------------------------------------+
  |    event_timestamp_utc                   | 1   N |       historical_event_revisions         |
  |    system_available_at_utc (INDEXED)     |-------|------------------------------------------|
  |    schema_version, pipeline_version      |       | PK revision_id                           |
  +------------------------------------------+       |    event_id, revision_number             |
         |                           |               |    content_hash, headline, recorded_at   |
         | 1                         | 1             +------------------------------------------+
         |                           |
         v N                         v N
  +----------------------------------+       +--------------------------------------------------+
  |    historical_event_embeddings   |       |            historical_event_outcomes             |
  |----------------------------------|       |--------------------------------------------------|
  | PK (event_id, model_version)     |       | PK (event_id, instrument_id, horizon_label)    |
  |    embedding_dim (e.g., 384)     |       |    outcome_start_ts, outcome_end_ts (INDEXED)    |
  |    vector_blob (Float32 IEEE754) |       |    return_simple, return_log, benchmark_adjusted |
  |    created_at_utc                |       |    realized_vol, mfe, mae, data_quality_status   |
  +----------------------------------+       +--------------------------------------------------+
```

### Physical Isolation Rules:
1. **Metadata vs. Outcomes:** Future post-event returns reside solely in `historical_event_outcomes`. Querying an event's text or metadata never exposes future price behavior unless explicitly requested via the causal maturity filter.
2. **Model-Versioned Embeddings:** Dense semantic vectors are composite-keyed by `(event_id, model_version)`. Switching embedding models never silently mixes vector spaces.

---

## 4. Causal Invariants & Operational Contracts

### 4.1 Decision Timestamp Isolation
All event candidate queries executed via `query_events(decision_timestamp=...)` inject an unbypassable predicate at the database cursor layer:
$$\texttt{system\_available\_at\_utc} < \tau_{\text{decision}}$$
Both dates are serialized as fixed-precision zero-padded ISO-8601 UTC strings (`YYYY-MM-DDTHH:MM:SS.ffffffZ`), preserving exact lexicographical and temporal ordering inside SQLite B-Tree indexes.

### 4.2 Outcome Maturity Guard
Outcome retrieval executed via `get_matured_outcomes(..., decision_timestamp=...)` strictly filters:
$$\texttt{outcome\_end\_ts} \le \tau_{\text{decision}}$$
If a 5-day return window ends 1 second after $\tau_{\text{decision}}$, the SQL query returns empty for that horizon while still surfacing valid matured 1-hour or 1-day horizons.

### 4.3 Idempotency, Deduplication, and Revision Tracking
Every `upsert_event()` execution resolves into one of three deterministic outcomes:
1. **`INSERTED`:** Novel canonical event ID and novel `content_hash`. Inserts event row, instrument mapping rows, and logs revision 1.
2. **`SKIPPED_DUPLICATE`:** Either the exact `(event_id, content_hash)` already exists, OR a distinct `event_id` within the same jurisdiction carries an identical `content_hash`. Prevents double-indexing duplicate wire releases.
3. **`UPDATED_REVISION`:** Existing `event_id` submitted with modified payload text or classifications. Increments `revision_number`, updates primary record, refreshes instrument mappings, and appends a full historical snapshot to `historical_event_revisions`.

### 4.4 Crash Recovery & Binary Footprint
- Configured with `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, `PRAGMA foreign_keys=ON`, and `PRAGMA busy_timeout=10000`.
- Float32 embeddings encoded via Python `struct.pack(f'{dim}f', *vector)`:
  - A 384-dimensional vector occupies exactly **1,536 bytes** ($1.5\text{ KB}$).
  - Indexing 100,000 events requires $\approx 153\text{ MB}$ for raw vector storage—eliminating Pickle deserialization vulnerabilities and minimizing RAM overhead.
