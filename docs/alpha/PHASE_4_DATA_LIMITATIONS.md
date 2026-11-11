# Phase 4 Data Limitations, Historical Coverage Gaps & Causality Risks

**Document Version:** 1.0.0-alpha  
**Date:** 2026-11-10  
**Phase:** Phase 4 — Historical Event Vector Retrieval & Contextual Intelligence  
**Author:** Agastya Varshney <agastyavarshney@gmail.com>  
**Status:** Canonical Data Risk & Coverage Disclosure  

---

## 1. Executive Summary

Phase 4 engineered the complete infrastructure for persistent vector retrieval, session-aware multi-horizon event studies, and contextual intelligence (`backend/alpha/historical_store.py`, `event_embeddings.py`, `event_retrieval.py`, `event_study.py`, `contextual_features.py`, `market_context.py`, `historical_intelligence.py`).

However, rigorous quantitative governance requires stating plainly **what data currently exists vs. what data must still be acquired via authorized channels before Phase 5 predictive training can claim statistical validity**.

---

## 2. Actual Historical Text Corpus Inventory

As verified in [`PHASE_4_CORPUS_COVERAGE.md`](PHASE_4_CORPUS_COVERAGE.md), the existing local repository SQLite database (`tickerwatch.db`) holds **3,043 total articles**:

| Source Connector | Jurisdiction | Count | Entity Scope | Structural Limitation |
|---|---|---:|---|---|
| `sec_edgar` | `US` | 3,013 (99.01%) | `AAPL`, `AMZN`, `MSFT` only | Heavy Form 4 / SEC filing slant; zero broader US sector breadth |
| `sec_news_press` | `US` | 25 (0.82%) | SEC Enforcement Releases | Narrow enforcement text |
| `moneycontrol_market_reports` | `IN` | 4 (0.13%) | General market wrap-ups | Zero company-specific NSE/BSE filings |
| `test_api_conn` | `GLOBAL` | 1 (0.04%) | Synthetic test fixture | Excluded via `is_synthetic` quarantine |
| **Total** | **Mixed** | **3,043** | **3 US issuers, 0 IN issuers** | **Cannot train/validate Indian equity predictors** |

### Synthetic Evaluation Data Disclosure
- All Phase 4 retrieval ablation results (`docs/alpha/benchmark_historical_results.json`: 19 `IN` + 11 `US` historical events, 15 queries) and unit verification suites (`tests/alpha/test_alpha_historical.py`) run against **explicitly tagged synthetic corpora (`is_synthetic = True`)**.
- Synthetic fixtures verify mathematical invariants, causal time-gates, rollover math, and relative IR ranking behavior under controlled paraphrasing; they **must never be conflated with real historical market predictability or profitability**.

---

## 3. Critical Historical Market-Data & Consensus Gaps

1. **Zero Point-in-Time Consensus Expectations (`0.0%` Coverage):**
   - Public RSS and regulatory exchange announcements contain reported actuals ($A$), not sell-side analyst forecasts ($E$) or consensus standard deviation ($\sigma$).
   - Phase 4's surprise engine and contextual slices handle this safely via `UNAVAILABLE_EXPECTATION` and separate non-surprise conditional slices (`MATERIALITY_MATCHED`), rather than fabricating estimates. Until institutional point-in-time consensus snapshots (e.g., FactSet, Bloomberg, I/B/E/S) are licensed, `SURPRISE_MATCHED` slices remain sparsely populated in live history.
2. **Absence of Bundled Multi-Year Indian Intraday / L2 Market History:**
   - While Phase 2 and Phase 4 provide full L1/L2 alignment, Zerodha Kite binary decoders, calendar rules, and event-study computation, historical minute-bar and tick-level L2 archives for NIFTY futures and NIFTY 50 single stocks require authorized commercial subscriptions (TrueData / GlobalDataFeeds / NSE Data & Analytics / Kite Historical API) plus exchange data-retention clearance.
   - Using daily EOD bars to synthesize `1m`, `5m`, `30m`, or `1h` outcomes is strictly blocked (`OutcomeQualityStatus.INSUFFICIENT_RESOLUTION`), ensuring missing resolution cannot silently poison contextual return distributions.
3. **Cross-Market & Currency Non-Substitution Risk:**
   - US SEC EDGAR data (`USD`, New York Eastern session 09:30–16:00 ET, SEC Form 8-K item taxonomy) cannot be pooled into Indian `IN` retrieval namespaces (`INR` Crore/Lakh scale, NSE 09:15–15:30 IST, SEBI LODR Reg 30 clauses). `PersistentHistoricalEventStore` enforces mandatory `MarketJurisdiction` separation, and `surprise.py` enforces `(entity_id, CurrencyUnit)` baseline isolation.
4. **Futures Expiry & Corporate Action Provenance Dependency:**
   - Historical futures price series stitched naively across monthly expiry rolls introduce artificial price jumps unrelated to news catalysts. Phase 4 `EventStudyEngine` explicitly returns `ROLLOVER_INVALID` whenever `contract_id` changes without an audited `FuturesRolloverSpec`, and depends on corporate action multiplier tables (`splits`, `bonuses`, `extraordinary dividends`) when evaluating long `5d` cash-equity windows.
5. **Provider Receipt Time (`local_received_at_utc`) Backfill Constraint:**
   - Legacy bulk historical archives (e.g., NSE/BSE PDF zip archives or old SEC EDGAR index dumps) often record exchange-local wall-clock times (`Asia/Kolkata` for NSE/BSE/RBI; DST-aware `America/New_York` for SEC EDGAR) rather than explicit UTC offsets, and omit socket arrival telemetry (`local_received_at_utc`). Phase 5 Work Package A (`A1`) remediated `backend/alpha/historical_import.py` to enforce explicit source-timezone localization before UTC conversion and retain full `_timestamp_provenance` metadata; delayed archival ingestion must never be confused with sub-millisecond live wire arrival.
6. **Reaction vs. Executable Return Gap:**
   - Pre-event reaction anchors (`PRE_EVENT_AS_OF`) capture total economic repricing including overnight or wire-delay gaps, NOT achievable post-receipt trading P&L. Downstream modeling strictly separates `REACTION` from `POST_DECISION_EXECUTABLE` outcomes (`A4`) and uses strictly pre-event rolling beta or labeled `SIMPLE_MARKET_ADJUSTED` differences (`A5`).

---

## 4. Minimum Data Prerequisites Prior to Phase 5 Training

Before executing Phase 5 ($P(\Delta F_{t+h} \mid E_t, H_t, M_t)$ calibration and Model C vs Model B statistical testing):
1. Acquire $\ge 2$ years of authorized **NSE/BSE historical corporate disclosures** ($\ge 5,000$ events across NIFTY 50 constituents) and RBI/MOSPI macro bulletins ($\ge 150$ releases) via `backend/alpha/historical_import.py`.
2. Acquire matching authorized **1-minute OHLCV / L1 quote histories** (and L2 snapshots where licensed) with verified split/bonus adjustment factors and futures rollover ledgers.
3. Persist matured event-study outcome labels separately into `historical_event_outcomes` with verified `outcome_end_ts` and `data_quality` audit status before slicing purged walk-forward folds.
