# TickerWatch Alpha — 11-Phase Implementation Roadmap & Phase Gate Protocols

> **Document Version:** 1.0.0-alpha  
> **Status:** LIVING ROADMAP — UPDATED THROUGH PHASE 4  
> **Author:** Quantitative Governance & Engineering Architecture  
> **Mandate:** Strict Sequential Phase Progression & Premature Execution Prevention  

---

## 1. Governance Principles & Gate Enforcement

To prevent the catastrophic risks endemic to quantitative trading projects—such as prematurely training models on unvalidated data, overfitting to leaky backtests, or deploying capital before proving statistical edge—TickerWatch Alpha enforces **hard, immutable Phase Gates**.

### Gate Enforcement Rules:
1. **Zero Premature Progression:** No phase may commence until all verification criteria of the preceding phase are independently audited and formally signed off.
2. **Strict Prohibition on Live Execution:** Under no circumstances may live capital or broker order routing be activated prior to successful completion of Phase 8.
3. **Falsification-First Culture:** If any gate criterion reveals that a core hypothesis ($H1$–$H5$) is falsified or that net edge is wiped out by transaction costs, the project pauses for research pivot or graceful termination.

```
+====================================================================================================+
|                                    11-PHASE IMPLEMENTATION ROADMAP                                  |
+====================================================================================================+

   [ PHASE 0: Research Charter, Instrument Selection & Repository Baseline ] [SIGNED OFF]
                           | (Gate 0 Sign-Off)
                           v
   [ PHASE 1: Ingestion Foundation Audit & Sandbox-Safe Test Suite Reproduction ] [SIGNED OFF]
                           | (Gate 1 Sign-Off)
                           v
   [ PHASE 2: Market Data Stream Scaffolding & Sub-Second Alignment Engine ] [SIGNED OFF - REPLAY READY]
                           | (Gate 2 Sign-Off)
                           v
   [ PHASE 3: Financial Event NLP, Entity Disambiguation & Surprise Extraction ] [SIGNED OFF - BENCHMARK VERIFIED]
                           | (Gate 3 Sign-Off)
                           v
   [ PHASE 4: Historical Event Vector Retrieval & Contextual Intelligence ] [SIGNED OFF - GATE 4 PASSED]
                           | (Gate 4 Sign-Off)
                           v
   [ PHASE 5: Predictive Multi-Horizon Model Training & Probability Calibration ] <=== CURRENT PHASE
                           | (Gate 5 Sign-Off)
                           v
   [ PHASE 6: Realistic Execution Simulation, Indian Cost Modeling & Latency Stress ]
                           | (Gate 6 Sign-Off)
                           v
   [ PHASE 7: Real-Time Hot-Path Scaffolding & ONNX Runtime Engine Optimization ]
                           | (Gate 7 Sign-Off)
                           v
   [ PHASE 8: Real-Time Paper Trading & Out-of-Sample Drift Monitoring ]
                           | (Gate 8 Sign-Off)
                           v
   [ PHASE 9: Micro-Capital Pilot Execution & Live Slippage Verification ]
                           | (Gate 9 Sign-Off)
                           v
   [ PHASE 10: Multi-Asset Scale, Instrument Expansion & Optionality Evaluation ]
```

---

## 2. Phase-by-Phase Completion Requirements & Gate Criteria

### Phase 0: Research Charter, Instrument Selection & Repository Baseline (CURRENT)
- **Objective:** Establish the scientific thesis, testable hypotheses ($H1$–$H5$), provisional single-instrument decision, event taxonomy, mathematical targets, system architecture, engineering boundaries, and clean baseline audit.
- **Mandatory Deliverables:**
  - `docs/alpha/RESEARCH_CHARTER.md`
  - `docs/alpha/INSTRUMENT_DECISION.md`
  - `docs/alpha/EVENT_TAXONOMY.md`
  - `docs/alpha/PREDICTION_SPECIFICATION.md`
  - `docs/alpha/ARCHITECTURE.md`
  - `docs/alpha/ENGINEERING_DECISIONS.md`
  - `docs/alpha/DATA_REQUIREMENTS.md`
  - `docs/alpha/REPOSITORY_BASELINE.md`
  - `docs/alpha/EXPERIMENT_PROTOCOL.md`
  - `docs/alpha/FINANCE_PREREQUISITES.md`
  - `docs/alpha/PHASE_GATES.md`
  - `docs/alpha/DECISIONS.md`
- **Gate 0 Verification:**
  - [x] Branch `alpha-research` created cleanly from `main`.
  - [x] Working tree clean; zero existing code or tests broken.
  - [x] Hypotheses and null hypotheses formally specified.
  - [x] Exactly one provisional instrument selected (`NIFTY_FUT`).
  - [x] Primary 3 event families selected.
  - [x] Zero code implementation of models or live connectors in Phase 0.

---

### Phase 1: Ingestion Foundation Audit & Test Suite Reproduction (COMPLETED)
- **Objective:** Independently audit the existing Phase 1 news ingestion system, resolve sandbox/network test hanging hazards, and reproduce full test verification suite.
- **Mandatory Tasks:**
  - Harden `test_api.py` and `test_resilience.py` to prevent background polling socket timeouts in network-isolated environments.
  - Verify and reproduce passing test suite across contract, integration, and unit tests.
  - Validate SQLite WAL write-speed, crash durability, and cursor rollback under synthetic fault injection.
  - Separate live smoke tests behind opt-in `-m live` pytest marker.
- **Gate 1 Verification:**
  - [x] All 90 automated unit and integration tests execute cleanly offline without hanging or network timeouts.
  - [x] Zero data loss or cursor corruption verified under crash recovery and synthetic fault injection tests.
  - [x] Ingestion foundation verified and signed off as trustworthy data foundation for Phase 2.

---

### Phase 2: Market Data Architecture & Sub-Second Alignment Engine
- **Objective:** Build the market data ingestion adapter and synchronous order-book alignment layer.
- **Mandatory Tasks:**
  - Implement market data subscriber for Level 2 top-of-book quotes on `NIFTY_FUT`.
  - Build local ring-buffer order-book representation.
  - Implement the 10-stage timestamp tracker with monotonic clock anchoring.
  - Build the Causal Anchor synchronizer: map news arrival timestamps to prevailing market quotes without look-ahead bias.
- **Gate 2 Verification:**
  - Alignment engine accurately maps historical test events to quote states with zero look-ahead leakage.
  - Timestamp latency between socket read and buffer write profiled at $< 1\text{ms}$.

---

### Phase 3: Financial Event NLP, Entity Disambiguation & Surprise Extraction
- **Objective:** Build domain NLP pipeline for the 3 target event families (Earnings, Contracts, Macro).
- **Mandatory Tasks:**
  - Fine-tune or adapt a compact financial Transformer (e.g. FinBERT / ModernBERT) for multi-task event classification and entity resolution.
  - Build numerical extraction head for reported figures vs. consensus expectations ($\Delta_{\text{surprise}}$).
  - Benchmark Model B against rule-based Model A.
- **Gate 3 Verification:**
  - [x] Macro F1-score: **94.23%** on event classification (Target $> 0.85$).
  - [x] Entity Recognition F1: **93.33%** (Precision: 92.11%, Recall: 94.59%, Zero False Positives on English stopwords: 0.00%).
  - [x] Numerical Extraction Accuracy: **100.00%** on Crore/Lakh/Billion, ranges, losses, percentages, bps.
  - [x] Execution Latency P95: **0.428 ms** on standard CPU (Target $< 5.0\text{ ms}$).
  - [x] Process Memory RSS: **32.18 MB** (Target $< 200.0\text{ MB}$).
  - [x] Zero Look-Ahead Bias: Verified via causal assertions; post-event expectations throw `ValueError`; missing consensus explicitly yields `UNAVAILABLE_EXPECTATION`.
  - [x] Full offline regression suite: **138/138 tests passing** in 5.88s with zero regressions.
  - [x] Phase 3 signed off and certified for Phase 4 consumption.
  - [x] Baseline rule head established with drop-in modular FinBERT-ONNX interface for Phase 4.

---

### Phase 4: Historical Event Vector Retrieval & Contextual Intelligence (COMPLETED — SIGNED OFF)
- **Objective:** Remediate Phase 3 causal defects and build persistent historical event storage, ONNX semantic vector retrieval (exact cosine per ADR-019, HNSW deferred until >50K events), session-aware event-study outcome computation, and point-in-time contextual intelligence ($H_t, M_t$).
- **Mandatory Tasks:**
  - [x] Remediated Phase 3 A1–A7 (`local_received_at_utc` decision timestamp, outcome maturity guards, full-doc extraction, multi-entity preservation, currency/polarity isolation, SQLite persistence).
  - [x] Persistently indexed versioned event representations and `bge-small-en-v1.5-onnx-int8` (384-D) / `fin-concept-proj-256d-v1` (256-D) embeddings in SQLite WAL with strict `IN`/`US` jurisdiction isolation.
  - [x] Built calendar/rollover-aware event-study engine, sample-gated contextual feature synthesizer (`min_sample_size = 5`), and decoupled point-in-time market regime builder (`M_t`) with hard query-label separation (`contains_query_future_labels = False`).
- **Gate 4 Verification:**
  - [x] Zero look-ahead leakage verified: double-lock enforcement (`system_available_at_utc < decision_timestamp` and `outcome_end_ts <= decision_timestamp`) with explicit immature/resolution masking.
  - [x] Exact vector search scan $< 1\text{ms}$ on CPU; full end-to-end Top-5 CPU retrieval (incl. query ONNX INT8 encode + SQLite filtering) at **6.60–10.92 ms p95**, lifting nDCG@5 from `0.8700` (Jaccard) / `0.8496` (BM25) to **0.9854 (ONNX Dense)** and **0.9621 (ONNX Hybrid)** with **1.0000 MRR**.
  - [x] Integrated via Pull Request #2 normal merge commit (`66b9328`) and hardened in Phase 5 WP-A (`PHASE_5_PHASE_4_REMEDIATION.md`: IST/ET timezone localization, bitemporal revision reconstruction, BGE-small CLS-512 parity v2, REACTION vs EXECUTABLE separation, and pre-event rolling beta).

---

### Phase 5: Predictive Multi-Horizon Model Training & Probability Calibration
- **Objective:** Train the full multi-modal fusion model (Model D) predicting forward price distributions over horizons $h \in \{1\text{s}, 5\text{s}, 30\text{s}, 5\text{min}\}$.
- **Mandatory Tasks:**
  - Implement cross-attention or feature fusion combining text embeddings, historical context, and order-book microstructure.
  - Calibrate probability outputs using Platt scaling or isotonic regression to minimize Brier score and ECE.
  - Enforce explicit **NO TRADE** neutral class.
- **Gate 5 Verification:**
  - Expected Calibration Error (ECE) $< 0.05$.
  - Out-of-sample directional accuracy $> 56.0\%$ across active signals.
  - Falsification test: If directional accuracy $\le 52.0\%$ or Information Coefficient $\le 0.02$, halt model development.

---

### Phase 6: Realistic Execution Simulation, Indian Cost Modeling & Latency Stress
- **Objective:** Build the execution backtester incorporating empirical L2 depth, queue priority, statutory Indian fees, and latency degradation.
- **Mandatory Tasks:**
  - Implement statutory transaction costs (STT 0.02% sell, NSE turnover, GST 18%, stamp duty, brokerage).
  - Execute the 7 latency degradation scenarios ($\Delta t \in \{0\text{ms}, 20\text{ms}, 50\text{ms}, 100\text{ms}, 500\text{ms}, 1\text{s}, 5\text{s}\}$).
  - Profile maximum drawdown (MDD), profit factor, and alpha half-life ($\tau_{1/2}$).
- **Gate 6 Verification:**
  - Strategy generates net annualized Sharpe $> 1.5$ post-costs at $\Delta t = 20\text{ms}$ ($H5$ validation).
  - Monotonic alpha decay confirmed across latency increments ($H4$ validation).
  - Falsification test: If net returns are non-positive after statutory costs, project cannot progress.

---

### Phase 7: Real-Time Hot-Path Scaffolding & ONNX Runtime Engine
- **Objective:** Export trained models to ONNX and optimize the real-time inference hot path for low-latency execution.
- **Mandatory Tasks:**
  - Export PyTorch models to ONNX graph with INT8 VNNI quantization.
  - Measure end-to-end processing latency from raw text string to prediction vector on CPU.
  - Implement C++ / Rust bindings for zero-copy memory buffers if required.
- **Gate 7 Verification:**
  - Deterministic end-to-end CPU inference latency profiled and documented.
  - Quantization accuracy loss $< 1.0\%$ relative to FP32 baseline.

---

### Phase 8: Real-Time Paper Trading & Out-of-Sample Drift Monitoring
- **Objective:** Run continuous paper trading against live market feeds and live corporate disclosures.
- **Mandatory Tasks:**
  - Connect live BSE/NSE announcement feeds and broker WebSocket market quotes.
  - Execute paper trade signals without capital risk.
  - Monitor prediction drift, queue fill probabilities, and model calibration live for at least 30 consecutive trading sessions.
- **Gate 8 Verification:**
  - Paper trading live Sharpe within $20\%$ of backtested simulation.
  - Zero fatal unhandled exceptions, zero database lock freezes.
  - Minimum 50 live events processed and logged.

---

### Phase 9: Micro-Capital Pilot Execution & Live Slippage Verification
- **Objective:** Deploy minimal capital (single-contract lots) via authorized broker API to empirically measure live exchange slippage.
- **Mandatory Tasks:**
  - Deploy 1-contract positions on `NIFTY_FUT`.
  - Measure exact exchange acknowledgment times ($t_{\text{ack}}$) and fill prices ($t_{\text{fill}}$).
  - Compare realized live slippage against Phase 6 simulated slippage models.
- **Gate 9 Verification:**
  - Live slippage matches simulation within $\pm 0.5$ ticks.
  - Automated kill-switches and margin loss ceilings trigger instantaneously on breach.

---

### Phase 10: Multi-Asset Scale, Instrument Expansion & Optionality Evaluation
- **Objective:** Broaden instrument universe and evaluate advanced strategies.
- **Mandatory Tasks:**
  - Expand universe from `NIFTY_FUT` to liquid single-stock futures (`RELIANCE_FUT`, `HDFCBANK_FUT`).
  - Re-evaluate GIFT Nifty futures subject to regulatory access clearance.
  - Evaluate non-linear options strategies (IV crush, straddles) now that directional predictability is proven.
- **Gate 10 Verification:**
  - Positive cross-asset transferability of models without performance collapse.
