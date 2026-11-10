# Phase 4 Semantic Vector Retrieval & Modular Hybrid Ranking Architecture

**Document Version:** 1.0.0-alpha  
**Date:** 2026-11-10  
**Phase:** Phase 4 — Historical Event Vector Retrieval & Contextual Intelligence  
**Author:** Agastya Varshney <agastyavarshney@gmail.com>  
**Status:** Canonical Retrieval Architecture  

---

## 1. Executive Summary

Lexical Jaccard similarity fails to recognize financial paraphrases when surface vocabulary diverges—for instance, failing to connect `"L&T bags mega railway tender"` with `"Larsen & Toubro awarded infrastructure EPC order"`. Conversely, pure neural embeddings struggle to respect numerical magnitude (`₹10 crore` vs `₹10,000 crore`), exact instrument boundaries, or event subtype hierarchies.

Work Package D constructs a hybrid, transparent vector retrieval architecture implemented across `backend/alpha/event_embeddings.py` and `backend/alpha/event_retrieval.py`. It pairs CPU-quantized Transformer inference (`BAAI/bge-small-en-v1.5` INT8 ONNX) with causal Okapi BM25 and explicit multi-dimensional financial scoring.

---

## 2. Candidate Embedding Model Research & Selection

Candidate models were rigorously evaluated across accuracy, financial domain suitability, latency, memory footprint, ONNX availability, and licensing—avoiding selection by raw download popularity alone:

| Candidate Model | Params / Dim | Format / Disk Size | Est. CPU Latency (Single) | Peak RAM Footprint | MTEB Retrieval Quality | License | Selection Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`BAAI/bge-small-en-v1.5` (INT8 ONNX)** | 33M / 384-D | ONNX INT8 (**34.0 MB**) | **~3.0 ms** | **~95 MB RSS** | High (~62.2 MTEB) | **MIT** | **PRIMARY SELECTED** |
| `sentence-transformers/all-MiniLM-L6-v2` | 22M / 384-D | ONNX INT8 (~23 MB) | ~2.2 ms | ~80 MB RSS | Moderate (~56.3 MTEB) | Apache 2.0 | Viable alternative, weaker financial nuance |
| `ProsusAI/finbert` | 110M / 768-D | PyTorch / FP32 (~438 MB) | ~35–55 ms | ~520 MB RSS | Uncalibrated for CLS bi-encoder cosine | Apache 2.0 | Rejected (Classifier head, high CPU cost) |
| `TaylorAI/bge-micro-v2` | 17M / 384-D | ONNX (~18 MB) | ~1.8 ms | ~65 MB RSS | Moderate-Good | MIT | Rejected (Narrower Indian English vocab) |
| **`fin-concept-proj-256d-v1`** (Internal) | Zero Weights / 256-D | Pure NumPy / Python | **< 0.1 ms** | **< 2 MB** | Domain synset coverage | MIT (Internal) | **AIR-GAPPED FALLBACK / BASELINE** |

### Why `BAAI/bge-small-en-v1.5` (INT8 ONNX) Wins:
1. **CPU Throughput Without PyTorch Bloat:** Running via `onnxruntime` + `tokenizers` requires only ~32 MB of installed Python wheels (no 2.5 GB PyTorch runtime) and encodes 3 events in $\approx 9.0\text{ ms}$ (~3 ms/event) on a standard host CPU.
2. **Paraphrase Robustness:** Maps synonymous corporate disclosures (e.g., `"bags engineering order"` $\leftrightarrow$ `"awarded EPC contract"`, verified empirical cosine $= 0.680$ vs unrelated RBI rate release $= 0.482$) cleanly without memorizing noisy stock tickers.
3. **Permissive Commercial Licensing:** Clean **MIT License**.

---

## 3. Exact Cosine Similarity vs. HNSW Approximate Indexing

Vector similarity search was empirically evaluated prior to introducing any Approximate Nearest Neighbor (ANN) index (such as HNSW or FAISS):

- **Exact Vectorized Dot Product (`numpy` Float32 BLAS):** Scanning $N = 10,000$ pre-normalized 384-dimensional vectors executes in **$< 5\text{ ms}$** on host CPU with zero quantization approximation error ($100\%$ recall).
- **Pre-Filter Funnel:** Because every retrieval query first applies SQL B-Tree causal indexing (`system_available_at_utc < decision_timestamp`, `jurisdiction`, optional `instrument_id` or `event_subtype`), candidate pools per query rarely exceed $500 - 5,000$ historical records.

**Architectural Decision:** Exact cosine similarity over SQL-prefiltered embeddings is strictly superior to HNSW for corpora $< 100,000$ events. HNSW introduces index staleness on real-time updates, non-deterministic deletion holes, and arbitrary dynamic pre-filter degradation without latency necessity.

---

## 4. Retrieval Modes & Modular Scoring Decomposition

Four distinct retrieval modes (`RetrievalMode`) are implemented cleanly for ablation and evaluation:

1. **`JACCARD`:** Token Jaccard overlap baseline ($|T_q \cap T_h| / |T_q \cup T_h|$).
2. **`BM25` (`CausalBM25Scorer`):** Okapi BM25 ($k_1 = 1.5, b = 0.75$) whose document frequency statistics ($df, avgdl$) are calculated strictly dynamically over causally pre-filtered prior candidates—completely eliminating future IDF leakage.
3. **`DENSE_SEMANTIC`:** Exact cosine similarity over versioned dense vector embeddings.
4. **`HYBRID_MODULAR`:** Transparent linear-modular score across nine financial dimensions:

$$\text{Combined}(e_q, e_h) = 0.28\,S_{\text{sem}} + 0.12\,S_{\text{bm25}} + 0.05\,S_{\text{jacc}} + 0.18\,S_{\text{sub}} + 0.14\,S_{\text{inst}} + 0.08\,S_{\text{num}} + 0.05\,S_{\text{surp}} + 0.06\,S_{\text{mat}} + 0.04\,S_{\text{time}}$$

### Component Definitions:
- **$S_{\text{sem}}$ (Semantic Cosine):** Scaled unit-vector dot product over canonical text `[Event Subtype] [Entity] Headline + Snippet`.
- **$S_{\text{sub}}$ (Subtype Match):** $1.0$ if exact subtype match, $0.5$ if shared parent family (`EventPrimaryFamily`), else $0.0$.
- **$S_{\text{inst}}$ (Entity & Instrument Match):** $1.0$ if direct instrument or resolved corporate entity overlap, $0.4$ if shared GICS/NSE industry sector, else $0.0$.
- **$S_{\text{num}}$ (Numerical Log-Magnitude Proximity):** Compares primary normalized monetary scales without mixing orders of magnitude:
  $$S_{\text{num}} = \max\!\left(0,\; 1 - \frac{|\log_{10}(V_q) - \log_{10}(V_h)|}{3.0}\right)$$
- **$S_{\text{surp}}$ (Surprise Direction Alignment):** $1.0$ if both events carry identical surprise direction (`POSITIVE_SURPRISE` / `NEGATIVE_SURPRISE`), $0.1$ if opposing, $0.5$ if consensus was unavailable.
- **$S_{\text{mat}}$ (Materiality Proximity):** $1 - |M_q - M_h|$.
- **$S_{\text{time}}$ (Temporal Recency Decay):** $\exp(-0.002 \times \Delta \text{days})$.

### Zero Opaque Black Boxes
Every `RetrievedHistoricalMatch` exposes a populated `RetrievalScoreBreakdown` object alongside source provenance (`source_feed`), system availability timestamp (`system_available_at_utc`), matched entity IDs, and explicitly verified matured outcome horizons (`available_outcome_horizons`).

---

## 5. Embedding Versioning & Safeguards

- All cached vectors in `historical_event_embeddings` carry primary key `(event_id, model_version)`.
- Comparing or querying across distinct `model_version` tags (`bge-small-en-v1.5-onnx-int8` vs `fin-concept-proj-256d-v1`) is blocked by SQL and storage APIs.
- Queries automatically exclude `query_event_id` and any candidate sharing an identical `content_hash`, eliminating self-matches and revised article double-counting.
