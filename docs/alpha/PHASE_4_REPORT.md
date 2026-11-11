# Phase 4 Implementation and Verification Report

**Project:** TickerWatch Alpha  
**Phase:** Phase 4 — Historical Event Vector Retrieval & Contextual Intelligence  
**Document Version:** 1.0.0-alpha  
**Date:** 2026-11-10  
**Author:** Agastya Varshney (`agastyavarshney@gmail.com`)  
**Branch:** `phase/4-historical-intelligence` → `main`  
**GitHub Pull Request:** [`coolbros/TickerWatch#2`](https://github.com/coolbros/TickerWatch/pull/2)  
**Status:** MERGED INTO `main` (`66b9328`) — REMEDIATED IN PHASE 5 WP-A (`4.1.0-alpha`)  

---

## 1. Executive Summary

Phase 4 elevates TickerWatch Alpha from structured single-event NLP comprehension (Phase 3) into a **persistent, causally-safe historical event retrieval, calendar-aware event-study, and contextual intelligence engine** supporting downstream Phase 5 research ($P(\Delta F_{t+h} \mid E_t, H_t, M_t)$).

Before constructing semantic retrieval, all seven Phase 3 causal and representation defects (**Work Package A**) were remediated and locked down with tests. Across Work Packages B–K, the system gained:
- Provider-neutral historical importers with strict `IN`/`US` jurisdiction isolation.
- A persistent 5-table SQLite WAL store (`schema_version = "4.0.0"`) enforcing structural separation between event metadata, versioned embeddings, and future outcome labels.
- Local CPU quantized ONNX semantic embeddings (`bge-small-en-v1.5-onnx-int8`, MIT license) paired with Causal Okapi BM25 and structured financial similarity.
- An exchange-calendar-aware (`Asia/Kolkata` / `America/New_York`), resolution-gated, rollover-validated event-study engine across `{1m, 5m, 30m, 1h, 1d, 5d}`.
- Sample-size-gated contextual distributions (`min_sample_size = 5`, percentile bootstrap 95% CIs) and a completely decoupled point-in-time market regime context ($M_t$).

---

## 2. Work Packages & Exact Commit Provenance

All commits are authored incrementally on `phase/4-historical-intelligence` (branched cleanly from `origin/main` `ebcdf67` / Phase 3 merge `701e5eb`) without rewriting history:

| Work Package | Commit | Delivered Components | Summary of Technical Capability |
|---|---|---|---|
| **A: Phase 3 Remediation** | `2157b5e` | `nlp_pipeline.py`, `event_extraction.py`, `event_retrieval.py`, `surprise.py`, `materiality.py`, `test_alpha_nlp.py`, `PHASE_4_PHASE_3_REMEDIATION.md` | Enforced `local_received_at_utc` decision timestamp + 5-stage provenance; added `1d`/`5d` outcome maturity masking; enabled full-doc extraction + dedup + per-share; preserved multi-entity/subsidiary/Phase-2 mappings; isolated `(entity, currency)` surprise + cost/debt polarity inversion; added SQLite persistence; reconciled doc discrepancies. |
| **B: Corpus & Data Research** | `71e3107` | `backend/alpha/historical_import.py`, `tests/alpha/test_alpha_historical_import.py`, `PHASE_4_HISTORICAL_DATA_RESEARCH.md`, `PHASE_4_CORPUS_COVERAGE.md` | Audited 3,043 legacy articles (`99.8%` US); implemented causal `NSE`, `BSE`, `RBI`, `SEC`, and `JSONL` importers with strict `IN`/`US` partitioning and SHA-256 deduplication. |
| **C: Persistent Store** | `061c6eb` | `backend/alpha/historical_store.py`, `tests/alpha/test_alpha_historical_store.py`, `PHASE_4_HISTORICAL_STORE.md` | Built SQLite WAL store (`v4.0.0`) separating `historical_events`, `historical_event_instruments`, `historical_event_revisions`, `historical_event_embeddings` (`float32` BLOBs), and `historical_event_outcomes`. |
| **D: Vector Retrieval** | `0800550` | `backend/alpha/event_embeddings.py`, `backend/alpha/event_retrieval.py`, `tests/alpha/test_alpha_vector_retrieval.py`, `PHASE_4_VECTOR_RETRIEVAL.md` | Integrated offline INT8 ONNX `Xenova/bge-small-en-v1.5` (384-D, MIT), `FinConceptProjectionEncoder` (256-D fallback), `CausalBM25Scorer`, exact NumPy cosine similarity, and 9-dimensional `HYBRID_MODULAR` ranking. |
| **E: Event-Study Engine** | `8c86caa` | `backend/alpha/outcomes.py`, `backend/alpha/event_study.py`, `tests/alpha/test_alpha_event_study.py`, `PHASE_4_EVENT_STUDY.md` | Built IST/ET calendar-aware outcome calculator across 6 horizons with resolution gating, simple/log/benchmark-adjusted returns, MFE/MAE, halt/gap guards, corporate action multipliers, and explicit `FuturesRolloverSpec` enforcement. |
| **F: Contextual Intelligence** | `0691e74` | `backend/alpha/contextual_features.py`, `tests/alpha/test_alpha_contextual_features.py`, `PHASE_4_CONTEXTUAL_INTELLIGENCE.md` | Implemented `ContextualFeatureSynthesizer` with `min_sample_size = 5` gating `INSUFFICIENT_EVIDENCE`, quantiles, seeded 1,000-resample bootstrap 95% CIs, and conditional slices. |
| **G: Market Regime Context** | `94e1ef4` | `backend/alpha/market_context.py`, `tests/alpha/test_alpha_market_context.py`, `PHASE_4_MARKET_CONTEXT.md` | Integrated Phase 2 `MarketEvent` & `TemporalAlignmentEngine` strictly at `<= decision_timestamp` (`excluded_future_events_count`), producing isolated $M_t$ microstructure/volatility/liquidity state. |
| **H: Unified Pipeline** | `495c5f9` | `backend/alpha/historical_intelligence.py`, `nlp_pipeline.py`, `entities.py`, `event_classifier.py`, `PHASE_4_PIPELINE_DESIGN.md`, `test_alpha_historical_pipeline.py` | Created `HistoricalIntelligencePipeline` & `HistoricalIntelligenceRepresentation` (`v4.0.0-alpha`, `contains_query_future_labels = False`), exposed `FinancialNLPPipeline.build_historical_intelligence()`, and preserved `direct_instruments` in `entities.py`. |
| **I: Evaluation & Benchmarks** | `06d27d5` | `backend/alpha/historical_evaluation.py`, `scripts/alpha_historical_benchmark.py`, `benchmark_historical_results.json`, `PHASE_4_EVALUATION_REPORT.md` | Built chronological zero-leakage evaluator (`split_leakage_duplicates = 0`), fixed weekend trading-day resolution check and contextual benchmark `inst_match` pollution, and benchmarked 6 retrieval configs + 6 event-study checks. |
| **J: Comprehensive Tests** | `223714c` | `tests/alpha/test_alpha_historical.py` | Added full Phase 4 invariance and end-to-end verification tests covering persistence, dedup, maturity, calendar boundaries, rollover, regime joins, and label isolation. |
| **K: Governance & Docs** | `c9eb350` + final commit | `ARCHITECTURE.md`, `DATA_REQUIREMENTS.md`, `DECISIONS.md` (ADR-018..021), `PHASE_GATES.md`, `PHASE_4_VECTOR_RETRIEVAL.md`, `PHASE_4_MODEL_CARD.md`, `PHASE_4_DATA_LIMITATIONS.md`, `PHASE_4_REPORT.md` | Completed architecture diagrams, ADR revisions, Phase Gate 4 sign-off, model card, data limitation audit, and final report. |

---

## 3. Dataset Inventory, Coverage & Missing Historical Data

### 3.1 Existing Operational Corpus (`tickerwatch.db`)
- **Total Records:** **3,043**
  - `sec_edgar` (`US`): `3,013` (`99.01%`) — concentrated strictly in `AAPL`, `AMZN`, and `MSFT`.
  - `sec_news_press` (`US`): `25` (`0.82%`) — SEC enforcement releases.
  - `moneycontrol_market_reports` (`IN`): `4` (`0.13%`) — general daily wrap-ups (**0 Indian company-specific filings**).
  - `test_api_conn` (`GLOBAL`, synthetic fixture): `1` (`0.04%`).

### 3.2 Benchmark & Verification Corpus Disclosure
- Retrieval benchmarking in Work Package I evaluated **19 `IN` historical events / 10 queries** and **11 `US` historical events / 5 queries** across 5 catalyst strata (`EARNINGS`, `GUIDANCE_REVISION`, `CONTRACT_WIN`, `REGULATORY_ACTION`, `MACRO_EVENT`).
- **Explicit Labeling:** All evaluation and unit-test fixtures are explicitly marked `is_synthetic = True` and strictly excluded from default `is_synthetic = 0` research/production queries.

### 3.3 Missing Historical Data Documented in `PHASE_4_DATA_LIMITATIONS.md`
1. **Point-in-Time Consensus Expectations:** `0.0%` coverage in raw exchange feeds; `UNAVAILABLE_EXPECTATION` protocol remains active until institutional consensus data is licensed.
2. **Multi-Year Indian Corporate Filings:** No authorized historical archive ($\ge 5,000$ NIFTY 50 NSE/BSE disclosures) is bundled in the repository.
3. **Intraday & L2 Tick Market History for Indian Equities:** Commercial historical 1-minute / Level 2 order-book archives are absent; daily EOD bars are blocked from manufacturing intraday outcomes (`INSUFFICIENT_RESOLUTION`).
4. **Live Socket Timestamps on Legacy Archives:** Historical dump importers lack microsecond NIC socket `local_received_at_utc` telemetry and must preserve conservative archival availability bounds.

---

## 4. Embedding Model, Licence & Vector Indexing Design

- **Primary Semantic Model:** `Xenova/bge-small-en-v1.5` (INT8 ONNX quantized export of `BAAI/bge-small-en-v1.5`).
  - **Parameters / Dim / Footprint:** 33M parameters, 384-D normalized vector, `34.01 MB` cached offline for CPU ONNX Runtime inference (`onnxruntime==1.31.1`).
  - **Licence:** **MIT License**.
  - **Canonical Version Tag:** `bge-small-en-v1.5-onnx-int8`.
- **Zero-Weight Air-Gapped Fallback:** `FinConceptProjectionEncoder` (`fin-concept-proj-256d-v1`, 256-D deterministic feature hashing + financial concept synonyms).
- **Version Isolation:** Vectors carry immutable `model_version` & `dimension` metadata; queries filter strictly by matching `model_version`.
- **Exact Cosine vs. HNSW (ADR-019):** Exact vectorized BLAS/NumPy cosine over SQLite-filtered candidates executes in `< 1 ms` with zero recall approximation error below ~50,000 events; HNSW is deferred until empirical scale requires approximate search.

---

## 5. Benchmark Methodology & Machine Specifications

- **Generated At:** `2026-11-10T11:46:31.410362Z` (`docs/alpha/benchmark_historical_results.json`)
- **Host Specifications:**
  - **OS:** `Windows-11-10.0.26200-SP0`
  - **Processor:** `Intel64 Family 6 Model 186 Stepping 3, GenuineIntel`
  - **Logical CPUs:** `12`
  - **Physical Memory:** `23.65 GB RAM`
  - **Python / Runtime:** Python `3.12.10` (`numpy==2.5.4`, `onnxruntime==1.31.1`, `tokenizers==0.23.3`)
- **Leakage & Split Hygiene:** Strict chronological cutoffs (`decision_timestamp > max(hist)`), jurisdiction partition separation (`IN_store` vs. `US_store`), SHA-256 duplicate audit confirming **`split_leakage_duplicates = 0`**, and paraphrased low-token-overlap query evaluation.

### 5.1 Retrieval Evaluation Results (`k = 5`, All 15 Queries Combined)

| Mode Configuration | Scorer / Model Version | P@5 | R@5 | MRR | nDCG@5 | Query p95 (ms) |
|---|---|---|---|---|---|---|
| `1_JACCARD_BASELINE` | Token Jaccard | 0.2800 | 0.8222 | 0.9667 | 0.8700 | 8.186 |
| `2_CAUSAL_BM25` | Causal Okapi BM25 | 0.2933 | 0.8555 | 0.9333 | 0.8496 | **7.108** |
| `3A_DENSE_CONCEPT_PROJ` | `fin-concept-proj-256d-v1` | 0.2533 | 0.7555 | 0.9000 | 0.7971 | 7.406 |
| `4A_HYBRID_CONCEPT_PROJ` | ConceptProj + BM25 + Struct | 0.3067 | 0.8889 | **1.0000** | 0.9484 | 7.891 |
| **`3B_DENSE_ONNX_BGE_SMALL`** | **`bge-small-en-v1.5-onnx-int8`** | **0.3600** | **1.0000** | **1.0000** | **0.9854** | 10.917 |
| **`4B_HYBRID_ONNX_BGE_SMALL`** | **`bge-small-en-v1.5-onnx-int8` + BM25 + Struct** | 0.3333 | 0.9445 | **1.0000** | 0.9621 | 10.557 |

- **Separated Jurisdiction nDCG@5 (`3B` / `4B` vs `1_JACCARD`):**
  - **IN (10 queries):** `0.9892` (`3B`) / `0.9678` (`4B`) vs `0.8601` (`Jaccard`) / `0.8444` (`BM25`).
  - **US (5 queries):** `0.9779` (`3B`) / `0.9508` (`4B`) vs `0.8898` (`Jaccard`) / `0.8601` (`BM25`).
- **System Footprint & Latency:**
  - Mean Single-Event Embedding: `0.781 ms` (`FinConceptProj`), `3.282 ms` (`BGE-Small-ONNX-INT8`).
  - Full Indexing Throughput: `65.54 ev/s` (`IN`), `76.71 ev/s` (`US`).
  - Process RSS (`psutil`): `42.85 MB` → `137.29 MB`.
  - SQLite File Footprint: `225,280 bytes` (`IN`), `151,552 bytes` (`US`).

---

## 6. Event-Study Coverage & Outcome Maturity Safeguards

### 6.1 Event-Study Verification Suite (`6/6 PASSED`)
1. **`outcome_label_correctness`:** Verified exact simple return $(P_{\text{end}} - P_{\text{start}})/P_{\text{start}}$ and $\ln(P_{\text{end}}/P_{\text{start}})$ calculations across intraday series.
2. **`benchmark_adjustment_correctness`:** Verified $R_{\text{excess}} = R_{\text{inst}} - \beta R_{\text{bench}}$ subtraction.
3. **`horizon_maturity_guard`:** Verified masking of unelapsed horizons to `OutcomeQualityStatus.IMMATURE` (`return = None`) whenever `outcome_end_ts > decision_timestamp`.
4. **`missing_and_resolution_handling`:** Verified blocking intraday (`1m`–`1h`) calculations on daily EOD bars (`OutcomeQualityStatus.INSUFFICIENT_RESOLUTION`).
5. **`session_and_overnight_returns`:** Verified Friday-close → Monday-close capture across weekend market closure with `overnight_gap_included = True` and `session_boundary_crossed = True`.
6. **`futures_rollover_handling`:** Verified rejection of naive expiry stitching (`ROLLOVER_INVALID`) vs. adjusted calculation when explicit `FuturesRolloverSpec` is provided (`ROLLOVER_ADJUSTED`).

### 6.2 Point-in-Time & Feature/Label Separation Safeguards
- **Dual-Clock SQLite Query Filtering:** Candidate selection mandates `system_available_at_utc < tau_decision`; outcome retrieval separately enforces `outcome_end_ts <= tau_decision`.
- **Small-Sample Evidence Gate:** Any contextual slice with fewer than `min_sample_size = 5` matured outcomes explicitly sets `EvidenceStatus.INSUFFICIENT_EVIDENCE`.
- **Query Label Isolation:** `HistoricalIntelligenceRepresentation` enforces `contains_query_future_labels = False`, storing zero forward outcome values for the incoming query event and excluding the query event / revisions during retrieval.
- **Market Regime Decoupling:** `MarketRegimeContext` ($M_t$) consumes only Phase 2 ticks/quotes with `local_received_at_utc <= decision_timestamp`, counting any skipped post-event ticks in `excluded_future_events_count`.

---

## 7. Honest Negative Findings & Iterative Engineering Fixes

Reporting unsuccessful configurations and caught bugs transparently is mandatory:

1. **Standalone Concept-Projection Negative Result (`3A_DENSE_CONCEPT_PROJ`):** Without pretrained transformer weights, the 256-D concept-hashing projection scored `0.7971` nDCG@5—**underperforming both Token Jaccard (`0.8700`) and Causal BM25 (`0.8496`)** (notably slipping to `0.50` MRR on Indian Macro paraphrases). Combining it in `4A_HYBRID_CONCEPT_PROJ` recovered `1.0000` MRR and `0.9484` nDCG@5.
2. **Fixed Hybrid (`4B`) Marginally Trailing Pure ONNX Dense (`3B`) on Secondary Peers:** While both `3B` and `4B` reached `1.0000` MRR, `3B_DENSE_ONNX_BGE_SMALL` (`0.9854` nDCG@5, `1.0000` R@5) outperformed fixed-weight `4B_HYBRID_ONNX_BGE_SMALL` (`0.9621` nDCG@5, `0.9445` R@5). Heuristic weights on `inst_match` (`0.12`) and `bm25` (`0.14`) slightly pull same-issuer lexical distractors ahead of valid cross-company sector analogues below rank 1.
3. **Caught Contextual Benchmark Collision Bug:** Initial ablation revealed that including `NSE:NIFTY_FUT:CURRENT` (contextual Tier-2 benchmark) inside corporate `inst_match` caused spurious cross-company matches, dragging `4B` nDCG@5 to `0.8113`. Excluding contextual benchmarks from corporate direct-instrument overlap immediately raised `4B` to `0.9621`.
4. **Caught Weekend Daily Bar Spacing Defect:** Initial `estimate_bar_resolution_seconds()` measured Friday-close to Monday-close in raw wall-clock seconds (`259,200 s`), failing daily resolution validation against an `86,400 s` `1d` bar gate. Converting multi-hour gaps into trading-day gaps (`count_trading_days_between`) resolved session-aware daily event studies cleanly.
5. **Caught `entities.py` Direct-Instrument Pass-Through Bug:** `_derive_instruments()` previously ignored `EntityDefinition.direct_instruments` and collapsed major Indian equities such as `LT` purely to contextual index futures until remediated in Work Package H (`495c5f9`).

---

## 8. Full Test Suite Results

Executed offline via `.venv\Scripts\python.exe` with `pytest` over the complete repository:

- **Total Tests Collected:** 174
- **Offline Tests Executed & Passing:** **172 passed** in `5.95s` (0 failures, 0 errors, 0 warnings)
- **Live Network Smoke Tests Deselected:** 2 (`-m live` opt-in policy preserved)
- **Breakdown by Phase:** Phase 1 (`90` unit/integration/contract) + Phase 2 (`23`) + Phase 3 & WP-A Remediation (`31` across `test_alpha_nlp.py`) + Phase 4 (`28` across 8 test modules including `test_alpha_historical.py`) = **`172` offline passing tests**.

---

## 9. GitHub Pull Request & Integration Status

- **Pull Request:** [`https://github.com/coolbros/TickerWatch/pull/2`](https://github.com/coolbros/TickerWatch/pull/2)
- **Source → Target:** `phase/4-historical-intelligence` → `main`
- **Status at Integration:** **MERGED** via GitHub Pull Request #2 into `main` on 2026-11-10 using normal merge commit `66b9328ec67695f96f0e2155d989e3c1194d0ae7` (`parents: ebcdf67, 17f9072`, zero squashing or rebasing). Follow-up Phase 4 correctness remediations (`A1`–`A7`, `4.1.0-alpha`) are documented in `docs/alpha/PHASE_5_PHASE_4_REMEDIATION.md`.
