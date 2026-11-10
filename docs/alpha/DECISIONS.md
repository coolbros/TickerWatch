# TickerWatch Alpha — Architectural & Research Decisions Record (ADR)

> **Document Version:** 1.0.0-alpha  
> **Status:** LIVING RECORD — UPDATED THROUGH PHASE 4  
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
| **ADR-011** | FastAPI Lifespan Poller Decoupling and Offline Test Isolation | **ACCEPTED** | 2026-11-09 | Deterministic offline testing; external socket timeout elimination |
| **ADR-012** | Hybrid Dual-Tiered Storage (SQLite WAL + Partitioned Compressed JSONL) | **ACCEPTED** | 2026-11-10 | High-frequency tick throughput, 58x compression, zero external server dependencies |
| **ADR-013** | Causal As-Of Join Engine & Multi-Horizon Forward Anchoring | **ACCEPTED** | 2026-11-10 | Strict elimination of look-ahead bias and wire-latency optical illusions |
| **ADR-014** | Multi-Tier Event-to-Instrument Relevance Taxonomy & Instrument Master | **ACCEPTED** | 2026-11-10 | Elimination of blunt index-only mapping; idiosyncratic vs systematic separation |
| **ADR-015** | Financial Entity Recognition, Disambiguation & Ticker Collision Protection | **ACCEPTED** | 2026-11-10 | Context-gated dictionary matching; elimination of stopword collisions ("IT", "AT") |
| **ADR-016** | Hierarchical Financial Event Taxonomy & Sub-Millisecond Rule-Head Architecture | **ACCEPTED** | 2026-11-10 | 5-family/25-subtype taxonomy, multi-label boundaries, modular FinBERT compatibility |
| **ADR-017** | Point-in-Time Numerical Surprise Formulation & Expectation Integrity | **ACCEPTED** | 2026-11-10 | Causal consensus integrity, zero look-ahead bias, explicit UNAVAILABLE_EXPECTATION |
| **ADR-018** | Jurisdiction-Partitioned SQLite WAL Historical Store & Feature/Label Separation | **ACCEPTED** | 2026-11-10 | Cross-market isolation (`IN`/`US`), idempotent revision provenance, zero outcome leakage |
| **ADR-019** | Quantized ONNX `bge-small-en-v1.5` & Exact Cosine Over Premature HNSW | **ACCEPTED** | 2026-11-10 | Paraphrase recall (100% R@5), CPU < 11ms latency, 0 approximation error below 50K events |
| **ADR-020** | Dual-Clock Causal Outcome Maturity & Session/Rollover-Aware Event Study | **ACCEPTED** | 2026-11-10 | Strict `outcome_end_ts <= tau_decision`, calendar accuracy, explicit rollover integrity |
| **ADR-021** | Sample-Size-Gated Contextual Distributions & Decoupled Market Regime (`M_t`) | **ACCEPTED** | 2026-11-10 | `INSUFFICIENT_EVIDENCE` guard (`n < 5`), bootstrap uncertainty, clean Phase 5 ablation |

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

### ADR-011: FastAPI Lifespan Poller Decoupling and Offline Test Isolation
- **Context:** `create_app()` previously started background connector polling tasks upon FastAPI application lifespan startup. In offline test environments, this caused `TestClient` API tests to issue real outbound network calls to external feeds.
- **Decision:** Introduce `start_scheduler: bool = True` in `Settings` and an optional `start_scheduler: Optional[bool] = None` parameter in `create_app()`. Default behavior remains identical for production deployments (`start_scheduler=True`). In automated tests, the application factory is called with `start_scheduler=False`, preventing background pollers from running during unit tests while keeping the poller lifecycle independently testable.
- **Rationale:** Ensures that the entire default automated test suite runs completely offline with zero external network access, preventing socket timeouts and nondeterministic test failures.
- **Alternatives Considered:**
  - *Completely disabling background polling globally:* Rejected because production startup requires autonomous polling.
  - *Patching `asyncio.create_task`:* Fragile and pollutes global event loop state.

---


---

### ADR-012: Hybrid Dual-Tiered Storage Architecture (SQLite WAL + Partitioned Compressed JSONL)
- **Context:** Market data generates millions of high-frequency quotes and trades daily, while news disclosures arrive at low frequency (50-500 items/day). Inserting high-frequency ticks row-by-row into SQLite causes extreme write lock contention and bloat. Conversely, introducing Kafka/Redis/Postgres violates local-first design.
- **Decision:** Adopt a hybrid dual-tiered local storage model:
  1. *SQLite in WAL mode:* Preserves existing relational guarantees for canonical articles, connector state, instrument master, and dataset manifests.
  2. *Partitioned Append-Only Compressed JSONL (.jsonl.gz):* Buffers market events in memory and flushes chunked gzip-compressed files partitioned by date and instrument.
- **Rationale:** Achieves 58.4x disk compression and write throughput $> 89,000$ events/second on standard local NVMe/SSD, while maintaining zero external server dependencies.
- **Alternatives Considered:**
  - *PostgreSQL + TimescaleDB:* Excellent time-series features, but requires maintaining an external service process and credentials.
  - *Single SQLite Table for ticks:* Throughput drops to 18,400 events/sec and database file quickly grows to multi-gigabyte scale.

---

### ADR-013: Causal As-Of Join Engine & Multi-Horizon Forward Anchoring
- **Context:** Conflating news publication time {	ext{pub}}$ with system receipt time {	ext{local\_received}}$ introduces fatal look-ahead bias and wire-latency illusions.
- **Decision:** 
  1. Anchor all pre-event market state lookups strictly to the local receipt timestamp $	au_{	ext{decision}} \equiv t_{	ext{local\_received}}$ using a causal as-of join ($\max \{ t_{	ext{quote}} : t_{	ext{quote}} \le 	au \}$).
  2. Maintain forward outcome horizons (	ext{ms}$, 	ext{s}$, 	ext{s}$, 	ext{s}$, 	ext{min}$) in a separate outcome vector, never leaked into feature sets.
- **Rationale:** Guarantees that backtested returns represent genuinely tradeable information states available to the system in live operation.
- **Alternatives Considered:**
  - *Aligning against nearest forward quote:* Leaks post-announcement information into pre-event features.
  - *Aligning against exchange filing timestamp:* Ignores network transmission delays and web portal polling lag.

---

### ADR-014: Multi-Tier Event-to-Instrument Relevance Taxonomy & Instrument Master
- **Context:** Corporate earnings and contract wins directly impact the specific company's cash equity and single-stock derivatives, not the benchmark index. Mapping every event directly to NIFTY futures introduces extreme noise and attenuates predictive signal.
- **Decision:** Implement a structured instrument master and 4-tier relevance classification:
  1. DIRECT: Primary cash equity and active single-stock future (e.g. NSE:RELIANCE:EQ, NSE:RELIANCE_FUT:CURRENT).
  2. CONTEXTUAL: Index benchmarks (NSE:NIFTY_FUT:CURRENT) used to compute market-adjusted abnormal returns.
  3. INDIRECT: Sector peer sympathy assets (deferred).
  4. NOT_RELEVANT: Independent instruments explicitly excluded.
- **Rationale:** Separates idiosyncratic announcement alpha from market beta drift and formally integrates RELIANCE_FUT as the primary single-stock future candidate.
- **Alternatives Considered:**
  - *Single-instrument index-only mapping:* Substantially dilutes company earnings surprises into index beta noise.

---

### ADR-015: Financial Entity Recognition, Disambiguation & Ticker Collision Protection
- **Context:** Financial news text contains words that collide with common English vocabulary (e.g. "IT", "AT", "NOW", "CAN", "ON"), unlisted subsidiaries that need to resolve to listed parents (e.g. Jio Platforms -> Reliance Industries), and executive names ("Mukesh Ambani", "Tim Cook").
- **Decision:** Implement a context-gated financial entity resolver that:
  1. Distinguishes company tickers from English stopwords by requiring surrounding financial context keywords (`shares`, `q3`, `profit`, `results`, `ebitda`, `ltd`).
  2. Maps corporate subsidiaries to their ultimate parent listed entities and canonical instrument identifiers.
  3. Uses executive co-occurrence matching for key promoters and C-suite leadership.
  4. Explicitly outputs `RESOLVED`, `AMBIGUOUS`, or `UNRESOLVED` states rather than fabricating guesses.
- **Rationale:** Prevents catastrophic false-positive entity associations in automated event conditioning, achieving 0.00% false-positive rate on adversarial English vocabulary in benchmarks.
- **Alternatives Considered:**
  - *Generic spaCy / HuggingFace NER:* High false-positive rate on uppercase 2-letter tokens, multi-hundred-millisecond inference latency, lacks parent-subsidiary mapping.

---

### ADR-016: Hierarchical Financial Event Taxonomy & Sub-Millisecond Rule-Head Architecture
- **Context:** Complex financial disclosures frequently cover multiple catalysts concurrently (e.g. quarterly earnings + dividend declaration + forward guidance revision). Running full transformer models in the latency-critical path creates variable latency jitter.
- **Decision:**
  1. Establish a 5-family, 25-subtype hierarchical event taxonomy with explicit multi-label classification boundaries and inherent severity tiering.
  2. Deploy a deterministic, sub-millisecond rule-head baseline executing in $< 0.3\text{ms}$ on standard CPU with zero external dependencies.
  3. Maintain an agnostic output schema (`EventClassificationResult`) that allows drop-in replacement with compact ONNX-quantized language models (FinBERT-ONNX) without changing downstream contracts.
- **Rationale:** Guarantees deterministic repeatability, sub-millisecond execution latency ($0.428\text{ms}$ P95), and zero external network fragility for live and backtesting operations.
- **Alternatives Considered:**
  - *Generative LLM prompts (GPT-4 / Claude / Gemini API):* Network roundtrips $> 1,000\text{ms}$, non-deterministic outputs, rate limit and subscription risks.
  - *Flat single-label classifier:* Obscures multi-faceted corporate disclosures where dividends or guidance cuts co-occur with earnings.

---

### ADR-017: Point-in-Time Numerical Surprise Formulation & Expectation Integrity
- **Context:** Market asset prices react to the surprise component of news ($A - E$) rather than the absolute value. If consensus expectations are absent or retroactively backfilled from post-event data, research models suffer fatal look-ahead bias.
- **Decision:**
  1. Compute absolute surprise $S = A - E$, percentage surprise $S_{\%} = \frac{A - E}{|E|} \times 100$, and standardised Z-score $Z = \frac{A - E}{\sigma}$ with strict zero/negative denominator guards.
  2. Enforce strict point-in-time expectation integrity: only expectations with timestamp $\tau_{\text{captured\_at}} < \tau_{\text{decision}}$ may be used; post-event expectations throw `ValueError`.
  3. If no pre-event consensus was recorded at the decision timestamp, explicitly return `UNAVAILABLE_EXPECTATION`. Never fabricate estimates, silently impute values, or omit the field.
- **Rationale:** Preserves mathematical and empirical validity across both profits and losses, and guarantees reproducible historical research free from look-ahead bias.

---

### ADR-018: Jurisdiction-Partitioned SQLite WAL Historical Store & Feature/Label Separation
- **Context:** Phase 3's historical retriever held events in an volatile in-memory Python list, lacked schema-level separation between event metadata and post-event returns, and allowed inadvertent cross-market comparisons.
- **Decision:**
  1. Persist historical events in a 5-table SQLite WAL store (`historical_events`, `historical_event_instruments`, `historical_event_revisions`, `historical_event_embeddings`, `historical_event_outcomes`).
  2. Enforce explicit `MarketJurisdiction` partitions (`IN` vs `US` vs `GLOBAL`) and `is_synthetic` boolean segregation (`is_synthetic=0` default).
  3. Require two independent causal timestamps on every indexed record: `event_timestamp_utc` and `system_available_at_utc` (`local_received_at_utc`), filtering retrieval candidates strictly on `system_available_at_utc < tau_decision`.
- **Rationale:** Zero external server dependencies (rejecting Redis/Kafka/Milvus prematurely), atomic idempotent deduplication, crash recovery, and structural isolation preventing query future labels from entering feature tables.

---

### ADR-019: Quantized ONNX `bge-small-en-v1.5` & Exact Cosine Over Premature HNSW
- **Context:** Initial Phase 0–3 plans suggested HNSW approximate nearest-neighbor indexing, while Phase 3 shipped a lexical Jaccard matcher that failed on financial paraphrases (e.g., *"trims guidance"* vs *"slashes top-line projection"*).
- **Decision:**
  1. Adopt MIT-licensed `BAAI/bge-small-en-v1.5` (33M params, 384-D, INT8 ONNX via `onnxruntime`, 34.01 MB) paired with Causal BM25 and structured metadata in a transparent 9-dimensional hybrid scorer (`HYBRID_MODULAR`), plus a zero-weight `fin-concept-proj-256d-v1` (256-D) air-gapped fallback.
  2. Version every vector with explicit `model_version` so incompatible spaces can never be silently mixed.
  3. **Supersedes Phase 4 Gate HNSW assumption below 50,000 events:** Use exact vectorized NumPy cosine similarity (`batch_cosine_similarity`) with SQLite pre-filtering instead of HNSW until corpus scale exceeds ~50,000 events or brute-force vector search exceeds 5ms p95.
- **Rationale:** Exact search on <50K 384-D `float32` vectors takes <1ms in BLAS/NumPy with zero recall approximation error and trivial SQLite time/instrument pre-filtering. Empirically, `bge-small-en-v1.5-onnx-int8` lifted benchmark nDCG@5 from `0.8700` (Jaccard) / `0.8496` (BM25) to `0.9854` (dense) and `0.9621` (hybrid) with `10.9ms` end-to-end CPU p95.

---

### ADR-020: Dual-Clock Causal Outcome Maturity & Session/Rollover-Aware Event Study
- **Context:** Filtering historical candidates solely by `t_historical < tau_decision` leaks 1-day and 5-day post-event market returns from events that occurred hours earlier, and daily EOD bars cannot validly manufacture synthetic 1m/5m outcomes.
- **Decision:**
  1. Separate event eligibility (`system_available_at_utc < tau_decision`) from outcome eligibility (`outcome_end_ts <= tau_decision`) using a double-lock SQLite + Python maturity guard.
  2. Enforce intrinsic bar-resolution gating (`estimate_bar_resolution_seconds` accounting for trading-day spacing across weekends), exchange calendars (`Asia/Kolkata` IST and `America/New_York` ET with DST/holidays), halt/stale exclusions, and mandatory `FuturesRolloverSpec` validation (`ROLLOVER_INVALID` if unstitched across expiries).
  3. Restrict corporate event studies primarily to directly affected cash equities / SSFs, isolating benchmark index futures (`NSE:NIFTY_FUT:CURRENT`) as contextual adjustment factors.
- **Rationale:** Eliminates label leakage from unelapsed horizons, prevents fabricated intraday outcomes from EOD bars, and protects hybrid retrieval from spurious index-overlap collisions across unrelated companies.

---

### ADR-021: Sample-Size-Gated Contextual Distributions & Decoupled Market Regime (`M_t`)
- **Context:** Sparse historical strata can produce narrow misleading averages (`n = 2` returns averaged into a confident figure), and mixing microstructure state into NLP vectors prevents Phase 5 ablation testing ($E_t$ vs $H_t$ vs $M_t$).
- **Decision:**
  1. Require `min_sample_size = 5` matured outcome observations before issuing statistical response summaries; smaller samples must explicitly report `EvidenceStatus.INSUFFICIENT_EVIDENCE` alongside seeded percentile bootstrap 95% confidence intervals when populated.
  2. Build `MarketRegimeContext` ($M_t$) strictly from Phase 2 `MarketEvent` primitives at or before `decision_timestamp` and keep it structurally distinct inside `HistoricalIntelligenceRepresentation` (`contains_query_future_labels = False`).
- **Rationale:** Prevents statistical overconfidence on undersized corpora and enables rigorous orthogonal Hypothesis Testing in Phase 5.

---

## 3. Unresolved Questions & External Dependencies Awaiting Verification

The following items are explicitly tracked for resolution in subsequent phases:
1. **Market Data Feed Verification (Phase 2):** Benchmark actual WebSocket packet arrival latency and Level 2 depth stability across Indian brokers (Zerodha Kite, Interactive Brokers) vs. authorized data vendors (TrueData).
2. **Exchange Filing PDF Parsing Latency (Phase 3):** Determine whether BSE/NSE corporate announcement PDFs can be parsed, extracted, and normalized in $< 10\text{ms}$ using lightweight C-based PDF parsers (e.g. `mupdf` / `pypdfium2`).
3. **Consensus Data Availability (Phase 3):** Identify an affordable, structured source for consensus quarterly earnings estimates on Indian equities (e.g., FactSet, Bloomberg, or crowd-sourced consensus aggregators).
4. **Physical Delivery Rollover Execution (Phase 6):** If single-stock futures (`RELIANCE_FUT`) are traded, establish precise automated square-off triggers to ensure 100% exit prior to SEBI physical delivery margin imposition.
