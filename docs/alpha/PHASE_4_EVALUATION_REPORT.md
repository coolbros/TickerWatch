# TickerWatch Alpha — Phase 4 Historical Retrieval & Event-Study Evaluation Report (`PHASE_4_EVALUATION_REPORT`)

> **Phase:** Phase 4 — Work Package I  
> **Run Timestamp:** `2026-11-10T11:46:31Z` (`benchmark_historical_results.json`)  
> **Hardware / Environment:** Windows 11 (AMD64), Python 3.12.10, CPU-only ONNX Runtime + NumPy  
> **Corpus Disclosure:** The benchmark corpus is an explicitly labeled inspectable synthetic set (`is_synthetic=True`) designed for controlled IR ablations across paraphrased vs lexical queries; zero real research databases were mixed or modified.

---

## 1. Benchmark Methodology & Leakage Safeguards

- **Separated Jurisdiction Partitions:** Indian equity/macro events (`IN`: 19 historical events, 10 chronological queries) and US market events (`US`: 11 historical events, 5 chronological queries) are stored and evaluated in physically separate SQLite WAL stores (`IN_store`, `US_store`).
- **Strata Covered Across Both Markets:**
  1. Earnings
  2. Guidance revisions
  3. Contract wins (including lexical distractor trap: contract termination / AGM notice sharing company name)
  4. Regulatory actions
  5. Macroeconomic releases (rates & CPI)
- **Zero Leakage Guarantee:** Chronological query cutoffs strictly follow all indexed items (`decision_timestamp > max(hist)`), and normalized SHA-256 headline deduplication confirmed **`split_leakage_duplicates = 0`**.
- **Paraphrase Challenge:** Queries include low-token-overlap paraphrases (e.g., *"trims annual revenue guidance"* vs *"slashes full-year top-line projection"*, *"central bank enforcement restriction"* vs *"RBI supervisory directive"*, *"selected as technology vendor"* vs *"inks modernization agreement"*).

---

## 2. Combined Ablation Results (`k = 5`, All 15 Queries)

| Configuration | Embedding / Scorer | Precision@5 | Recall@5 | MRR | nDCG@5 | Query p95 (ms) |
|---|---|---|---|---|---|---|
| `1_JACCARD_BASELINE` | Token Jaccard | 0.2800 | 0.8222 | 0.9667 | 0.8700 | 8.186 |
| `2_CAUSAL_BM25` | Causal Okapi BM25 | 0.2933 | 0.8555 | 0.9333 | 0.8496 | **7.108** |
| `3A_DENSE_CONCEPT_PROJ` | `fin-concept-proj-256d-v1` (No ONNX) | 0.2533 | 0.7555 | 0.9000 | 0.7971 | 7.406 |
| `4A_HYBRID_CONCEPT_PROJ` | Concept Proj + BM25 + Structured | 0.3067 | 0.8889 | **1.0000** | 0.9484 | 7.891 |
| **`3B_DENSE_ONNX_BGE_SMALL`** | **`bge-small-en-v1.5-onnx-int8`** | **0.3600** | **1.0000** | **1.0000** | **0.9854** | 10.917 |
| **`4B_HYBRID_ONNX_BGE_SMALL`** | **`bge-small-en-v1.5-onnx-int8` + BM25 + Struct** | 0.3333 | 0.9445 | **1.0000** | 0.9621 | 10.557 |

*(Note: Because average relevant analogues per query in this compact 19-doc corpus is $1.8$, upper bound Precision@5 across 5 returned candidates is $\approx 0.36$; **100% Recall@5 = 0.3600 P@5**.)*

---

## 3. Market-Separated Breakdown (`IN` vs `US`)

### 3.1 Indian Market (`IN`: 10 Queries Across 5 Strata)
| Mode | P@5 | R@5 | MRR | nDCG@5 | Coverage | Query p50 / p95 (ms) | Regulatory MRR | Macro MRR |
|---|---|---|---|---|---|---|---|---|
| `1_JACCARD_BASELINE` | 0.30 | 0.8333 | 0.9500 | 0.8601 | 1.00 | 6.73 / 8.19 | 0.75 | 1.00 |
| `2_CAUSAL_BM25` | 0.30 | 0.8333 | 0.9500 | 0.8444 | 1.00 | 6.60 / 7.11 | 0.75 | 1.00 |
| `3A_DENSE_CONCEPT_PROJ` | 0.24 | 0.6833 | 0.9000 | 0.7539 | 1.00 | 6.71 / 7.41 | 1.00 | 0.50 |
| `4A_HYBRID_CONCEPT_PROJ` | 0.32 | 0.8833 | **1.0000** | 0.9436 | 1.00 | 6.67 / 7.89 | 1.00 | 1.00 |
| `3B_DENSE_ONNX_BGE_SMALL` | **0.38** | **1.0000** | **1.0000** | **0.9892** | 1.00 | 10.45 / 10.92 | 1.00 | 1.00 |
| `4B_HYBRID_ONNX_BGE_SMALL` | 0.36 | 0.9667 | **1.0000** | 0.9678 | 1.00 | 10.23 / 10.56 | 1.00 | 1.00 |

### 3.2 US Market (`US`: 5 Queries Across 5 Strata)
| Mode | P@5 | R@5 | MRR | nDCG@5 | Coverage | Query p50 / p95 (ms) | Contract Win MRR | Earnings MRR |
|---|---|---|---|---|---|---|---|---|
| `1_JACCARD_BASELINE` | 0.24 | 0.8000 | **1.0000** | 0.8898 | 1.00 | 6.38 / 6.57 | 1.00 | 1.00 |
| `2_CAUSAL_BM25` | 0.28 | 0.9000 | 0.9000 | 0.8601 | 1.00 | 6.30 / 6.89 | 0.50 | 1.00 |
| `3A_DENSE_CONCEPT_PROJ` | 0.28 | 0.9000 | 0.9000 | 0.8836 | 1.00 | 6.36 / 7.01 | 1.00 | 0.50 |
| `4A_HYBRID_CONCEPT_PROJ` | 0.28 | 0.9000 | **1.0000** | 0.9580 | 1.00 | 6.54 / 6.85 | 1.00 | 1.00 |
| `3B_DENSE_ONNX_BGE_SMALL` | **0.32** | **1.0000** | **1.0000** | **0.9779** | 1.00 | 9.41 / 9.43 | 1.00 | 1.00 |
| `4B_HYBRID_ONNX_BGE_SMALL` | 0.28 | 0.9000 | **1.0000** | 0.9508 | 1.00 | 9.01 / 9.22 | 1.00 | 1.00 |

---

## 4. Honest Ablation Findings & Failure Analysis

1. **Pretrained Quantized Transformer Embeddings (`bge-small-en-v1.5-onnx-int8`) Genuinely Outperform Lexical Baselines:**
   - Pure ONNX dense semantic retrieval (`3B`) lifted combined **Recall@5 from 0.8222 (Jaccard) / 0.8555 (BM25) to 1.0000** and **nDCG@5 from 0.8700 / 0.8496 to 0.9854**, resolving paraphrase queries (e.g., `IN_Q_REG_1` where Jaccard/BM25 slipped to `0.75` MRR).
2. **Negative/Mixed Result on Hash-Projected Fallback (`3A_DENSE_CONCEPT_PROJ`) Standalone:**
   - Used alone without ONNX weights, `fin-concept-proj-256d-v1` **underperformed both Jaccard (`0.7971` vs `0.8700` nDCG) and BM25 (`0.8496`)**, dropping MRR on macro CPI and earnings phrasing.
   - **Recovery via Hybrid Fusion (`4A_HYBRID_CONCEPT_PROJ`):** Combining `fin-concept-proj` with BM25 and structured entity/subtype features recovered **MRR = 1.0000** and lifted nDCG@5 to **0.9484**, outperforming lexical-only baselines in air-gapped environments.
3. **Why Pure ONNX Dense (`0.9854` nDCG) Slightly Edge-Outweighted Hybrid ONNX (`0.9621` nDCG):**
   - Look closely at `3B` vs `4B`: both achieved **MRR = 1.0000** (top rank #1 always correct), yet on multi-analogue Recall@5 / nDCG@5, adding fixed heuristic weights for `inst_match` (`0.12`) and `bm25` (`0.14`) slightly boosted same-issuer distractor events (e.g., an L&T routine AGM notice or opposite-direction contract dispute sharing the ticker) above valid sector peers (e.g., Infosys digital deal ranked #2/#3 for a TCS banking deal).
   - **Engineering Remediation Discovered During Benchmarking:** An initial ablation run exposed that index benchmarks (`NSE:NIFTY_FUT:CURRENT`) present on all corporate events were falsely triggering `inst_match = 1.0` across unrelated Indian companies, dragging `4B` hybrid nDCG down to `0.8113`. Filtering `benchmark_ids` out of corporate direct-instrument overlap immediately restored hybrid nDCG from `0.8113` to **`0.9621`** and MRR from `0.9333` to **`1.0000`**.
4. **Why Lexical Baselines Already Score High (~0.85–0.87 nDCG) Here:**
   - Company names/tickers (e.g., `TCS`, `RBI`, `SEBI`, `Apple`) still appear verbatim in many financial disclosures; on larger multi-thousand-article real corpora, semantic disambiguation becomes even more critical when the same issuer issues dozens of disparate filings.

---

## 5. Systems Resource & Latency Profile

| Metric | Measured Value | Target / Budget |
|---|---|---|
| Embedding Latency (`FinConceptProj-256D`) | **0.781 ms / event** | $< 2\text{ ms}$ |
| Embedding Latency (`BGE-Small-ONNX-INT8`) | **3.282 ms / event** | $< 15\text{ ms}$ |
| Indexing Throughput (Full SQLite + BGE-Small ONNX) | **65.5 – 76.7 events/sec** | Local batch / realtime ready |
| Query Retrieval Latency p95 (end-to-end incl. query encode + SQL) | **7.11 – 10.92 ms** | $< 25\text{ ms}$ |
| Process RSS Memory (`psutil`, Python + SQLite + ONNX Runtime) | **42.85 MB → 137.29 MB** (+94.44 MB) | $< 250\text{ MB}$ |
| SQLite Storage Footprint (`IN` 19 ev / `US` 11 ev incl. WAL overhead) | **225.3 KB** / **151.6 KB** | Local-first compact footprint |

---

## 6. Event-Study Engine Verification Suite

All 6 automated invariance checks inside `verify_event_study_suite()` **PASSED (6/6)**:

| Verification Check | Result | Verified Behaviour |
|---|---|---|
| `outcome_label_correctness` | **PASS** | Exact simple & log return computation across intraday path |
| `benchmark_adjustment_correctness` | **PASS** | Exact $R_{\text{inst}} - \beta R_{\text{bench}}$ excess return subtraction |
| `horizon_maturity_guard` | **PASS** | Masks unelapsed horizons to `IMMATURE` & `None` before endpoint |
| `missing_and_resolution_handling` | **PASS** | Refuses intraday horizons on daily EOD bars (`INSUFFICIENT_RESOLUTION`) |
| `session_and_overnight_returns` | **PASS** | Captures Fri-close -> Mon-close after-hours gap with `overnight_gap_included=True` |
| `futures_rollover_handling` | **PASS** | Rejects silent contract concatenation (`ROLLOVER_INVALID`); adjusts accurately with `FuturesRolloverSpec` (`ROLLOVER_ADJUSTED`) |
