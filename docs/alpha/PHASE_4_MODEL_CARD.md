# Model Card — TickerWatch Alpha Historical Event Vector Retrieval & Contextual Engine (v4.0.0-alpha)

Following standard ML model reporting guidelines (Mitchell et al., *Model Cards for Model Reporting*, 2019).

---

## 1. Model Details

- **System / Pipeline Name:** TickerWatch Alpha Phase 4 Historical Event Vector Retrieval & Contextual Intelligence Engine (`HistoricalIntelligencePipeline`)
- **Representation Schema Version:** `4.0.0-alpha` (`HistoricalIntelligenceRepresentation`)
- **SQLite Persistent Store Schema:** `4.0.0` (`PersistentHistoricalEventStore`)
- **Date:** 2026-11-10
- **Primary Dense Embedding Model:** `Xenova/bge-small-en-v1.5` (INT8 ONNX quantized export of `BAAI/bge-small-en-v1.5`, 33M parameters, 384 dimensions, L2-normalized mean/CLS pooling via ONNX Runtime CPU)
  - **Canonical Version Tag:** `bge-small-en-v1.5-onnx-int8`
  - **Model Artifact Size:** `34.01 MB` (offline cached locally; zero runtime internet access)
  - **License:** **MIT License**
- **Air-Gapped Zero-Weight Fallback Encoder:** `FinConceptProjectionEncoder`
  - **Canonical Version Tag:** `fin-concept-proj-256d-v1` (256 dimensions, signed feature hashing + curated financial concept synonyms)
- **Lexical Retrieval Scorers:** `CausalBM25Scorer` (Okapi BM25 over causally eligible pre-decision candidates) and Token Jaccard baseline (`EventClassifier` / Phase 3 baseline)
- **Repository:** `TickerWatch` (`coolbros/TickerWatch`)

---

## 2. Intended Use

### In-Scope Uses
- Indexing structured `FinancialEventRepresentation` records persistently into jurisdiction-partitioned SQLite WAL stores (`IN`, `US`, `GLOBAL`).
- Retrieving causally eligible ($t_{\text{avail}} < \tau_{\text{decision}}$), duplicate-free historical event analogues using exact vectorized cosine similarity (`DENSE_SEMANTIC`), lexical scoring (`JACCARD`, `BM25`), or 9-component interpretable fusion (`HYBRID_MODULAR`).
- Associating eligible historical peers with matured, resolution-verified, calendar-aware post-event market responses across `{1m, 5m, 30m, 1h, 1d, 5d}`.
- Synthesizing sample-size-gated response distributions (mean, median, quantiles, bootstrap 95% confidence intervals, conditional slices) and separated point-in-time market regime context ($M_t$) as upstream research features ($H_t$) for Phase 5 predictive modeling.

### Out-of-Scope / Prohibited Uses
- Direct automated trading, broker order routing, position sizing, or portfolio allocation.
- Computing predictive return forecasts directly from mean historical peer returns (mean analogue return $\neq$ causal treatment effect or trade signal).
- Mixing query event future labels into feature tensors or substituting US equity analogues for Indian corporate catalysts.
- Hot-path generative LLM decoding (strictly excluded per ADR-006; only compact discriminative ONNX encoding is permitted).

---

## 3. Input Formatting & Versioning Safeguards

- **Canonical Event Embedding Text:** Structured tag prefixing combining event subtype, primary entity, headline, and bounded body excerpt:
  `[Event Subtype] [Primary Entity] <headline> <body_snippet[:350]>`
- **Strict Embedding Space Isolation:** Every vector persisted in `historical_event_embeddings` carries explicit `model_version` and `dimension` metadata. Cosine queries strictly filter by matching `model_version`, preventing cross-model vector mixing (`bge-small-en-v1.5-onnx-int8` 384-D vs `fin-concept-proj-256d-v1` 256-D).
- **Exact Search Scaling Decision (ADR-019):** Brute-force vectorized BLAS/NumPy dot product (`batch_cosine_similarity`) over SQLite-filtered candidates executes in `< 1 ms` with zero recall approximation error below 50,000 events. Approximate indexing (HNSW) is intentionally deferred until empirical corpus scale justifies it.

---

## 4. Quantitative Benchmark Performance (CPU Only)

Measured on Windows 11 (`Windows-11-10.0.26200-SP0`), `Intel64 Family 6 Model 186 Stepping 3` (12 logical CPUs, 23.65 GB RAM), Python 3.12.10, ONNX Runtime `1.31.1`, NumPy `2.5.4`, evaluated across a manually inspectable, zero-leakage synthetic multi-jurisdiction benchmark (`IN`: 19 historical / 10 chronological queries; `US`: 11 historical / 5 chronological queries; `split_leakage_duplicates = 0`):

| Configuration | Model Version | P@5 | R@5 | MRR | nDCG@5 | Query p95 (ms) |
|---|---|---|---|---|---|---|
| `1_JACCARD_BASELINE` | Token Jaccard | 0.2800 | 0.8222 | 0.9667 | 0.8700 | 8.186 |
| `2_CAUSAL_BM25` | Causal Okapi BM25 | 0.2933 | 0.8555 | 0.9333 | 0.8496 | **7.108** |
| `3A_DENSE_CONCEPT_PROJ` | `fin-concept-proj-256d-v1` | 0.2533 | 0.7555 | 0.9000 | 0.7971 | 7.406 |
| `4A_HYBRID_CONCEPT_PROJ` | ConceptProj + BM25 + Struct | 0.3067 | 0.8889 | **1.0000** | 0.9484 | 7.891 |
| **`3B_DENSE_ONNX_BGE_SMALL`** | **`bge-small-en-v1.5-onnx-int8`** | **0.3600** | **1.0000** | **1.0000** | **0.9854** | 10.917 |
| **`4B_HYBRID_ONNX_BGE_SMALL`** | **`bge-small-en-v1.5-onnx-int8` + BM25 + Struct** | 0.3333 | 0.9445 | **1.0000** | 0.9621 | 10.557 |

### System Resource & Event-Study Verification Profile
- **Mean Single-Event Encode Latency:** `3.282 ms` (`bge-small-en-v1.5-onnx-int8`), `0.781 ms` (`fin-concept-proj-256d-v1`).
- **End-to-End Indexing Throughput:** `65.54 events/sec` (IN) / `76.71 events/sec` (US) including NLP + ONNX encode + SQLite WAL persistence.
- **Process Memory RSS:** `42.85 MB` baseline → `137.29 MB` post-ONNX session initialization (`+94.44 MB`).
- **Event-Study Invariance Suite:** **6/6 checks passed** (`outcome_label_correctness`, `benchmark_adjustment_correctness`, `horizon_maturity_guard`, `missing_and_resolution_handling`, `session_and_overnight_returns`, `futures_rollover_handling`).

---

## 5. Honest Failure Analysis, Negative Results & Guardrails

1. **Standalone Concept-Projection Negative Result (`3A_DENSE_CONCEPT_PROJ`):** Without pretrained ONNX weights, `fin-concept-proj-256d-v1` scored `0.7971` nDCG@5—**worse than both Jaccard (`0.8700`) and BM25 (`0.8496`)**—failing on short paraphrase pairs (e.g., IN Macro MRR `0.50`). It is retained purely as an air-gap fallback paired inside `4A_HYBRID_CONCEPT_PROJ` (`0.9484` nDCG@5, `1.0000` MRR).
2. **Pure ONNX Dense (`3B`) vs. Fixed-Weight ONNX Hybrid (`4B`):** While both achieve `1.0000` MRR, `3B_DENSE_ONNX_BGE_SMALL` (`0.9854` nDCG@5) slightly outperformed `4B_HYBRID_ONNX_BGE_SMALL` (`0.9621` nDCG@5) on secondary peer ranking. Fixed heuristic boosts (`inst_match=0.12`, `bm25=0.14`) slightly favor same-issuer distractors over cross-issuer sector peers when multiple valid sector analogues exist. Learned rank calibration belongs in Phase 5 after large-scale corpus ingestion.
3. **Contextual Benchmark Collision Remediation:** Benchmarking exposed that shared contextual benchmarks (`NSE:NIFTY_FUT:CURRENT`) previously polluted corporate `inst_match` across unrelated Indian equities (initially depressing `4B` nDCG@5 to `0.8113`). Filtering contextual benchmark IDs out of corporate instrument similarity lifted `4B` nDCG@5 to `0.9621`.
4. **Domain Adaptation Limitation:** `bge-small-en-v1.5` is a general English retrieval transformer, not fine-tuned specifically on Indian SEBI/RBI legal boilerplate (`SEBI (LODR) Regulation 30`, Hindi-transliterated financial entities, Crore/Lakh phrasing), though formatting subtype/entity tags in brackets provides strong conditioning.
5. **Dual-Clock Causal Safeguards:** Candidate eligibility requires `system_available_at_utc < decision_timestamp`, and outcome eligibility separately requires `outcome_end_ts <= decision_timestamp`. Any stratum with `n < 5` matured outcomes explicitly returns `EvidenceStatus.INSUFFICIENT_EVIDENCE`.
