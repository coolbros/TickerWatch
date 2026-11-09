# TickerWatch Alpha — Architectural & Research Decisions Record (ADR)

> **Document Version:** 1.0.0-alpha  
> **Status:** APPROVED BASELINE — PHASE 0  
> **Author:** Quantitative Governance, Infrastructure & ML Architecture  
> **Repository:** `TickerWatch` (`coolbros/TickerWatch`)  
> **Branch:** `alpha-research`  

---

## 1. Decision Log Summary

| ADR ID | Decision Title | Status | Date | Primary Driver |
|---|---|---|---|---|
| **ADR-001** | Focus Exclusively on Directional Futures Over Options in Early Phases | **ACCEPTED** | 2026-11-09 | Confounding variable elimination (linear vs non-linear pricing) |
| **ADR-002** | Select NSE NIFTY 50 Futures as Provisional Research Instrument | **ACCEPTED** | 2026-11-09 | Deepest liquidity, 1-tick spread, cash settlement, verified legal eligibility |
| **ADR-003** | Restrict Initial Event Scope to 3 Families (Earnings, Contracts, Macro) | **ACCEPTED** | 2026-11-09 | Measurable consensus baselines, clear timestamps, causal attribution |
| **ADR-004** | Formulate Prediction as Conditional Distribution with Explicit NO_TRADE | **ACCEPTED** | 2026-11-09 | Risk-controlled selective trading over continuous forced forecasting |
| **ADR-005** | Strict Separation of Hot Path (ONNX/C++) and Research Path (Python/PyTorch) | **ACCEPTED** | 2026-11-09 | Latency determinism vs research velocity |
| **ADR-006** | Ban Heavyweight Generative LLMs from Hot-Path Inference Loop | **ACCEPTED** | 2026-11-09 | Multi-second token latency, variable jitter, and API failure risks |
| **ADR-007** | Strict 10-Stage Timestamp Sequence with Universal UTC & Monotonic Clocks | **ACCEPTED** | 2026-11-09 | Elimination of look-ahead bias and wire-latency optical illusions |
| **ADR-008** | CPU-First Optimization; Defer FPGA Acceleration to Empirical Bottleneck | **ACCEPTED** | 2026-11-09 | Rapid iteration speed, maintainability, cost efficiency |
| **ADR-009** | Preserve Existing Ingestion Foundation with Zero Destructive Refactoring | **ACCEPTED** | 2026-11-09 | Architectural stability, code reuse, backwards compatibility |
| **ADR-010** | Enforce Purged Walk-Forward Splits; Ban Random Cross-Validation | **ACCEPTED** | 2026-11-09 | Elimination of temporal information leakage |

---

## 2. In-Depth Architectural Decision Records

### ADR-001: Directional Futures vs. Non-Linear Options
- **Context:** The project aims to prove whether AI can extract a predictive edge from financial disclosures. Options provide non-linear leverage, but their price formation is governed by implied volatility (IV) crush, theta decay, strike selection, and widening spreads across the option chain.
- **Decision:** Mandate directional futures contracts exclusively for Phases 0–5. Options strategies are strictly deferred until linear directional predictability is empirically established.
- **Rationale:** If a model cannot reliably predict the direction of linear price changes, adding non-linear option surfaces merely obscures whether failure stems from direction, volatility mispricing, or execution drag.
- **Alternatives Considered:** 
  - *Option straddles/strangles:* Captures volatility spikes around earnings without needing directional accuracy. Rejected because pre-earnings implied volatility is typically bid up by market makers, leading to massive post-announcement IV collapse that erodes edge.
- **Unresolved Questions:** When transitioning to single-stock futures in Phase 10, how will physical delivery margin escalation in expiry week be managed? (Resolved provisionally: auto-square off 7 days prior to expiry).

---

### ADR-002: Provisional Instrument Selection (`NIFTY_FUT`)
- **Context:** An instrument is required for initial market data alignment and latency profiling. Candidates evaluated: NSE NIFTY 50 Futures (`NIFTY_FUT`), Liquid Single-Stock Futures (`RELIANCE_FUT`), and GIFT Nifty Futures (`GIFT_NIFTY_FUT`).
- **Decision:** Select **NSE NIFTY 50 Near-Month Index Futures (`NIFTY_FUT`)** as the single provisional research instrument. Retain `RELIANCE_FUT` as an uncommitted secondary benchmark for corporate earnings studies.
- **Rationale:** `NIFTY_FUT` has the highest liquidity in the Indian financial market, the tightest bid-ask spread (typically 1 tick = 0.05 index points), cash settlement (zero physical delivery hazard), verified domestic broker access, and affordable historical tick data. GIFT Nifty has restrictive regulatory eligibility for resident Indians under RBI LRS rules.
- **Alternatives Considered:**
  - *GIFT Nifty Futures:* Traded 21 hours/day, capturing after-hours earnings. Rejected due to regulatory uncertainty regarding resident Indian trading eligibility and expensive market data feeds.
  - *Single-Stock Futures (`RELIANCE_FUT`):* Tighter causal link to corporate earnings, but carries SEBI-mandated physical delivery rules on expiry.
- **Unresolved Questions:** Which commercial data vendor (TrueData vs. GlobalDataFeeds vs. Zerodha Kite Connect WebSocket) provides the lowest packet jitter for live Level 2 quote capture?

---

### ADR-003: Selection of Initial Event Subset (Earnings, Contracts, Macro)
- **Context:** Attempting to train an NLP model on all 10 corporate event classes simultaneously dilutes training data and complicates evaluation.
- **Decision:** Restrict Phase 3–5 model development to **three event families**:
  1. Corporate Quarterly Earnings & Results
  2. Major Corporate Contract and Order Inflow Wins
  3. High-Impact Macroeconomic Announcements (RBI Rate Decisions & CPI)
- **Rationale:** Earnings have well-documented numerical consensus estimates, enabling rigorous testing of surprise detection ($H1$). Contracts occur unexpectedly during market hours, providing clean event windows for latency decay testing ($H4$). Macro releases directly drive benchmark index movements (`NIFTY_FUT`).
- **Alternatives Considered:**
  - *Litigation & Court Orders:* High market impact, but legal disclosures are unstructured, language is highly idiosyncratic, and consensus estimates do not exist.
  - *Management Changes:* Infrequent occurrence makes statistical significance difficult to achieve on small samples.
- **Unresolved Questions:** What is the average publication lag of BSE/NSE stock announcement PDFs relative to commercial news wires (Moneycontrol/PTI)?

---

### ADR-004: Conditional Distribution Formulation & Explicit NO_TRADE Class
- **Context:** Traditional regression models attempt to predict an exact numerical price change for every input, forcing trading activity even on ambiguous or low-conviction news.
- **Decision:** Formulate the prediction target as a conditional probability distribution over return buckets $P(\Delta F_{t+h} \in \mathcal{Y} \mid E_t, H_t, M_t)$ with an explicit **NO TRADE** neutral state and entropy thresholding.
- **Rationale:** In financial markets, selective participation is the cornerstone of risk management. A model outputting NO TRADE when uncertainty is high ($\mathcal{H} > \eta$) preserves capital.
- **Alternatives Considered:**
  - *Point-estimate regression (predicting continuous return $\Delta F$ directly):* Highly susceptible to outliers and provides no measure of prediction confidence.
- **Unresolved Questions:** What is the optimal confidence threshold $\theta$ that maximizes the out-of-sample Sharpe ratio without starving the strategy of trading opportunities?

---

### ADR-005: Hot Path vs. Research Path Decoupling
- **Context:** Research velocity requires flexible Python libraries (PyTorch, Pandas, Hugging Face, Jupyter), whereas low-latency execution requires deterministic runtime budgets ($< 25\text{ms}$).
- **Decision:** Enforce a strict architectural boundary between the **Research Path** (Python 3.12+, offline walk-forward backtesting, deep exploratory modeling) and the **Hot Path** (ONNX Runtime, minimal allocations, C++ where justified).
- **Rationale:** Attempting to run unoptimized Python code with heavy dynamic memory allocations in production causes latency spikes, while rewriting all research code into C++ cripples experimental agility.
- **Alternatives Considered:**
  - *Pure Python end-to-end:* Unacceptable latency jitter (Python garbage collection pauses can exceed 50–100 ms).
  - *Pure C++ end-to-end:* Severe friction during feature engineering and model training.
- **Unresolved Questions:** Does ONNX Runtime running on Python bindings provide sufficient throughput, or is a pure C++ inference daemon required for the sub-20ms scenario?

---

### ADR-006: Ban on Heavyweight Generative LLMs in Hot-Path Inference
- **Context:** Generative foundation models (e.g. GPT-4, Claude 3.5, Gemini 1.5, or local Llama 70B) demonstrate impressive reasoning, prompting inquiries into using them for live trading.
- **Decision:** Strictly bar heavyweight generative LLMs from the real-time inference loop. Hot-path text analysis must execute via compact discriminative Transformers (e.g., FinBERT, ModernBERT, or distilled models) running on ONNX Runtime.
- **Rationale:** Generative LLMs exhibit multi-second token generation latencies (500ms to 3000ms), API network roundtrips, rate limiting, non-deterministic token lengths, and high operational costs. At $t = 1\text{s}$, the information advantage of an event is already substantially degraded.
- **Permitted Use of Generative LLMs:** Restricted to the offline Research Path for dataset synthesis, complex document parsing, edge-case annotation, and trade post-mortems.
- **Alternatives Considered:**
  - *Quantized local 7B models (e.g. Mistral 7B INT4):* Latency remains $> 150\text{ms}$ on top-tier GPUs, and $> 1000\text{ms}$ on CPU. Still too slow for high-frequency event capture.

---

### ADR-007: 10-Stage Timestamp Sequence & Monotonic Clocks
- **Context:** Backtests frequently report illusory alpha because they assume trades occur at the stated news publication timestamp ($t_{\text{pub}}$), ignoring wire transit lag and processing delay.
- **Decision:** Formally define and track 10 distinct timestamps ($t_{\text{pub}}, t_{\text{provider}}, t_{\text{local\_recv}}, t_{\text{ingest}}, t_{\text{feat}}, t_{\text{pred}}, t_{\text{signal}}, t_{\text{submit}}, t_{\text{ack}}, t_{\text{fill}}$). Store all persisted timestamps in UTC ISO 8601. Measure intra-process latency using high-resolution monotonic clocks (`time.perf_counter_ns()`).
- **Rationale:** Eliminates look-ahead bias and allows precise measurement of the latency decay curve ($H4$).
- **Alternatives Considered:**
  - *Single timestamp per record:* Conceals where latency bottlenecks occur (wire vs. ingestion vs. inference).

---

### ADR-008: CPU-First Optimization; Deferral of Custom Silicon / FPGAs
- **Context:** High-frequency trading firms utilize FPGA NICs and custom ASICs to achieve sub-microsecond execution.
- **Decision:** Prioritize vectorized SIMD (AVX-512) and thread-affinity optimizations on standard x86-64 / ARM64 CPUs. FPGAs are explicitly deferred until empirical profiling confirms software latency is the binding bottleneck.
- **Rationale:** News text ingestion and Transformer inference are bound by algorithmic complexity and memory bandwidth, not raw network packet serialization. Developing custom FPGA logic in Phase 0 would consume excessive engineering resources without proving the core AI thesis.
- **Alternatives Considered:**
  - *Immediate FPGA acceleration:* Premature optimization; no hardware infrastructure or verified L3 feed currently exists in the project.

---

### ADR-009: Preservation of Existing Phase 1 Ingestion Foundation
- **Context:** TickerWatch already contains a functioning, audited Phase 1 news ingestion engine with SQLite WAL persistence, normalisation, deduplication, and connectors (RSS, SEC EDGAR).
- **Decision:** Preserve the entire existing Phase 1 codebase intact. Do not rewrite connectors or restructure directories destructively. Scaffold Phase 0 documentation cleanly in `docs/alpha/`.
- **Rationale:** Rewriting working ingestion infrastructure wastes verified engineering progress and introduces regression risks. The existing engine will serve as the news feed substrate for Alpha.
- **Alternatives Considered:**
  - *Complete codebase refactor into separate microservices:* High risk of breaking working features before establishing predictive proof-of-concept.

---

### ADR-010: Purged Walk-Forward Cross-Validation
- **Context:** Evaluating financial models with standard $k$-fold cross-validation results in severe data leakage due to temporal autocorrelation and overlapping post-event price trends.
- **Decision:** Enforce purged, embargoed walk-forward cross-validation for all model benchmarks. Chronologically restrict nearest-neighbour historical retrieval to events strictly preceding the training cutoff ($\tau \le t_{\text{train\_cutoff}}$).
- **Rationale:** Guarantees that backtested performance reflects real-world operational conditions where future events are completely unknown.
- **Alternatives Considered:**
  - *Standard out-of-bag train/test split:* Leaks regime shifts and macroeconomic trends across partitions.

---

## 3. Unresolved Questions & External Dependencies Awaiting Verification

The following items are explicitly tracked for resolution in subsequent phases:
1. **Market Data Feed Verification (Phase 2):** Benchmark actual WebSocket packet arrival latency and Level 2 depth stability across Indian brokers (Zerodha Kite, Interactive Brokers) vs. authorized data vendors (TrueData).
2. **Exchange Filing PDF Parsing Latency (Phase 3):** Determine whether BSE/NSE corporate announcement PDFs can be parsed, extracted, and normalized in $< 10\text{ms}$ using lightweight C-based PDF parsers (e.g. `mupdf` / `pypdfium2`).
3. **Consensus Data Availability (Phase 3):** Identify an affordable, structured source for consensus quarterly earnings estimates on Indian equities (e.g., FactSet, Bloomberg, or crowd-sourced consensus aggregators).
4. **Physical Delivery Rollover Execution (Phase 6):** If single-stock futures (`RELIANCE_FUT`) are traded, establish precise automated square-off triggers to ensure 100% exit prior to SEBI physical delivery margin imposition.
