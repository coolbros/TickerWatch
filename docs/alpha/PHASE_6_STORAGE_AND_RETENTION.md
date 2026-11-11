# Phase 6 Persistent Live Data Capture, Storage & Retention Architecture (`PHASE_6_STORAGE_AND_RETENTION`)

**Module:** [`backend/alpha/live_data_capture.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/live_data_capture.py) (`CAPTURE_SCHEMA_VERSION = "6.0.0-alpha"`)

---

## 1. Dual-Tier Local-First Storage Design

Phase 6 extends Phase 2's SQLite WAL + chunked `.jsonl.gz` + raw payload store without adding Kafka, Redis, or external database clusters:

| Tier | Format | Captured Artifacts | Immutability Guarantee |
|---|---|---|---|
| **Transactional Index & Audit DB** | SQLite WAL (`data/alpha_live_capture.db`) | `live_news_events`, `live_news_revisions`, `live_market_snapshots`, `live_connection_events`, `live_feed_incidents`, `live_checkpoints`, `live_quality_reports` | SQLite `BEFORE UPDATE` / `BEFORE DELETE` triggers block mutating historical `live_news_revisions` or `live_market_snapshots` |
| **Time-Series Tick / L2 Archive** | Date- & instrument-partitioned gzipped JSONL (`data/live_market_events/YYYY-MM-DD/<INST>/events_*.jsonl.gz`) | Full `MarketEvent` objects with 5-level `depth_bids` / `depth_asks` | Append-only sequential chunk rotation + per-directory `manifest.json` |
| **Raw Wire Payload Vault** | Content-addressed `.bin` (`data/raw_payloads/`) | Unparsed provider XML / JSON / binary payloads referenced by `raw_payload_ref` | Written once by `(date, provider_id, content_hash)` |

---

## 2. Evaluation of Columnar Parquet vs. SQLite + Gzip JSONL

- **Measured Dependency Status:** Neither `pyarrow` nor `fastparquet` is present in the lean repository `.venv` (`numpy`, `onnxruntime`, `httpx`, `feedparser`, `pydantic`, `uvicorn`, `fastapi`, `psutil`).
- **Existing Throughput & Footprint:** Phase 2 `PartitionedMarketDataWriter` sustains high-throughput buffered writes with compressed storage via `events_*.jsonl.gz`, while SQLite WAL provides millisecond range queries via `(instrument_id, local_received_at_utc)` indexes.
- **Explicit Columnar Migration Trigger:** Adopting Apache Parquet (e.g., daily ZSTD-compressed Arrow batches) is deferred cleanly until live L1/L2 capture scales past **~1,000,000 tick snapshots/day** or multi-month vectorized analytical scans exceed local SQLite index latency (`> 500 ms`).

---

## 3. Causal Reconstruction (`reconstruct_state_as_of`)

Every persisted record stores UTC timestamps alongside original timezone interpretation (`published_ts_interpretation`), `local_received_at_utc`, monotonic nanosecond sequence (`local_monotonic_ns`), `schema_version`, and `instrument_master_version`. `reconstruct_state_as_of(decision_timestamp)` uses SQL window ranking (`ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY revision_number DESC)`) constrained strictly by `local_received_at_utc <= decision_timestamp`, preventing future revisions or subsequent ticks from ever leaking into historical replays.
