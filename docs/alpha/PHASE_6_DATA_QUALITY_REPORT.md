# Phase 6 Live Data Quality Report (`PHASE_6_DATA_QUALITY_REPORT`)

**Audit Date:** 2026-11-11  
**Workspace Databases Inspected:**
- Existing Phase 1 legacy DB (`tickerwatch.db`): `3,042` real ingested news records (`3,038` US + `4` general Moneycontrol Indian market wrap-ups; `1` synthetic excluded) with `0` real market bars (`PHASE_5_DATA_READINESS.md`).
- Phase 6 Live Authorized RBI Probe Exercise (ephemeral run `2026-11-11T13:57Z`): **`10` real RBI press releases fetched live** (`https://www.rbi.org.in/pressreleases_rss.xml`), `0` quarantined.

---

## 1. Live Provider Feed Quality Findings

### 1.1 Indian Central Bank Feed (`rbi_press_rss`) — **VERIFIED CLEAN AFTER IST FIX**
- **Sample Captured Live:** `10` releases spanning publication window **`2026-11-09T10:40:00Z`** through **`2026-11-11T12:40:00Z`** (`0` missing titles, `0` quarantine drops).
- **Timestamp Anomaly Resolved:** RBI's XML `<pubDate>` emits naive local IST without timezone offset (e.g. `Wed, 11 Nov 2026 18:10:00` while HTTP `Last-Modified` reads `Wed, 11 Nov 2026 12:51:05 GMT`). `RBIPressReleaseAdapter` resolves this cleanly to UTC (`12:40:00Z`) and records `LOCALIZED_IST(Asia/Kolkata)` provenance.
- **Conditional Polling:** `ETag` (`W/"..."`) + `Last-Modified` verified clean `304 Not Modified` suppression on subsequent poll (`test_live_rbi_press_releases_rss` PASSED).

### 1.2 US Regulatory Feeds (`sec_edgar`, `sec_news_press`) — **VERIFIED ACTIVE**
- Both `SEC EDGAR Submissions JSON` (`AAPL` smoke check) and `SEC Press Releases RSS` passed opt-in live smoke checks cleanly (`test_live_smoke.py`: 3/3 passed in `3.40s`).

### 1.3 Live Data Gaps Blocking Empirical Strategy Validation
During live RBI ingestion through `LiveResearchPipeline`, the persisted ledger recorded exact data-quality flags honestly:
- `EMPTY_HISTORICAL_CORPUS`: `10 / 10` (clean Phase 4 historical store had zero pre-indexed matched Indian RBI peers)
- `MISSING_LIVE_MARKET_STATE`: `10 / 10` (no authorized real-time Indian broker/vendor market feed active)
- `CONSENSUS_UNAVAILABLE`: `10 / 10` (no consensus macro expectation database configured)
- `MARKET_REGIME_MISSING_MARKET_DATA` (`7`) / `MARKET_REGIME_UNAVAILABLE` (`3`)
- Entity Resolution: `7 / 10` mapped directly via RBI/issuer context; `3 / 10` general banking notices flagged `entity_resolution_failure_count = 3` (`UNRESOLVED`) without guessing tickers.

---

## 2. Offline Quality & Integrity Test Coverage

Mocked & fault-injected test verification in `test_alpha_phase6_adapters.py` and `test_alpha_phase6_shadow.py` confirmed zero silent corruption across:
- Duplicate RSS repolls (`304` & SHA-256 skip)
- Corrigendum / `/A` amendment revision linkage preserving earlier snapshots
- Sequence gaps (`gap_count` + `on_resync_required`), heartbeat timeouts, and queue overflow drop telemetry
- SQLite immutability triggers (`IMMUTABLE_LEDGER` / `IMMUTABLE_SHADOW_LEDGER`) and deterministic `0.0`-diff disk replay parity
