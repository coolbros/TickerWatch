# TickerWatch Alpha — System Architecture & Latency Decomposition

> **Document Version:** 1.0.0-alpha  
> **Status:** PHASE 3 COMPLETE — FINANCIAL NLP & EVENT LAYER INTEGRATED  
> **Author:** Distributed Systems & High-Frequency Architecture  
> **Classification:** Research Pipeline vs. Real-Time Hot Path  

---

## 1. High-Level System Architecture

TickerWatch Alpha is partitioned into two distinct operational regimes:
1. **The Research Path (Offline / Asynchronous):** Optimized for scientific flexibility, heavy model exploration (Python, PyTorch, Hugging Face), dense retrieval indexing, backtesting, and causal verification.
2. **The Hot Path (Real-Time / Synchronous):** Optimized for low latency, deterministic execution budgets, zero memory allocations, compact Transformer inference via ONNX Runtime, and minimal pipeline jitter.

```
+====================================================================================================+
|                                    TICKERWATCH ALPHA ARCHITECTURE                                   |
+====================================================================================================+

   [ NEWS SOURCES ]                   [ MARKET FEEDS ]
 (BSE/NSE, RSS, SEC)                (L1/L2 WebSocket/Ticks)
          |                                    |
          v                                    v
+-----------------------+            +-----------------------+
| News Ingestion Engine |            | Market Data Feed Hub  |  <--- (Shared Foundation)
| (HTTP/SSL/Parse/WAL)  |            | (Ring Buffer/Normalize|
+-----------+-----------+            +-----------+-----------+
            |                                    |
            v                                    v
+-----------------------+            +-----------------------+
| News Normalisation    |            | Order-Book State      |
| Canonical SourceItem  |            | L2 Quotes, Mid, Depth |
+-----------+-----------+            +-----------+-----------+
            |                                    |
            v                                    |
+-----------------------------------+            |
| FINANCIAL NLP & EVENT ENGINE      |            |
| * Entity Disambiguation           |            |
| * Event Classifier (10 classes)   |            |
| * Surprise Extraction Head        |            |
| * Materiality Estimator           |            |
+-----------------+-----------------+            |
                  |                              |
                  v                              |
+-----------------------------------+            |
| HISTORICAL CONTEXT RETRIEVER      |            |
| * Vector Search (Top-K Analogues) |            |
| * Historical Return Drift Prior   |            |
+-----------------+-----------------+            |
                  |                              |
                  +---------------+--------------+
                                  |
                                  v
                  +-------------------------------+
                  | PREDICTIVE FUSION LAYER       |
                  | News Rep + Context + Mkt State|
                  | ONNX Runtime Compact Engine   |
                  | Output: P(Delta F) + Calib H  |
                  +---------------+---------------+
                                  |
                                  v
                  +-------------------------------+
                  | EXECUTION & RISK ENGINE       |
                  | * Expected Net PnL Calculator |
                  | * Dynamic Position Sizer      |
                  | * Strict Pre-Trade Risk Checks|
                  | * Simulated Order Router      |
                  +-------------------------------+
```

---

## 2. Core Information Streams

### 2.1 News Intelligence Stream
Processes incoming textual and semi-structured disclosures:
$$\text{News Source} \longrightarrow \text{Ingestion} \longrightarrow \text{Normalisation} \longrightarrow \text{Financial NLP} \longrightarrow \text{Entity Resolution} \longrightarrow \text{Event Extraction} \longrightarrow \text{Surprise Head} \longrightarrow \text{Historical Retrieval}$$

1. **Ingestion & Normalisation:** Handled by existing TickerWatch Phase 1 components (`backend/connectors/`, `backend/ingestion/`). Yields canonical, hash-deduplicated `SourceItem` with UTC timestamps.
2. **Financial NLP & Entity Resolution:** Matches reported company mentions against an indexed equity master database (NSE Tickers, ISINs, subsidiaries).
3. **Event Extraction:** Multi-head classifier assigning disclosure to one of the 10 event classes.
4. **Surprise Representation:** Extracts key reported figures ($X_{\text{reported}}$) and computes deviation from consensus stored in the local repository.
5. **Historical Context Retrieval:** Queries a vector index of historical disclosures to retrieve $K=5$ nearest prior analogues and their historical post-event price distributions.

### 2.2 Market Intelligence Stream
Continuously maintains contemporaneous microstructure state:
$$\text{Market Feed} \longrightarrow \text{Normalisation} \longrightarrow \text{Order-Book Representation} \longrightarrow \text{Trade-Flow Features} \longrightarrow \text{Volatility \& Liquidity Metrics}$$

1. **Order-Book Representation:** Maintains the top 5 bids and asks, calculating dynamic spread $S(t)$, mid-price $P_{\text{mid}}(t)$, and book depth imbalance:
   $$\text{Imbalance}_t = \frac{\sum_{i=1}^5 Q_{\text{bid}, i} - \sum_{i=1}^5 Q_{\text{ask}, i}}{\sum_{i=1}^5 Q_{\text{bid}, i} + \sum_{i=1}^5 Q_{\text{ask}, i}}$$
2. **Trade-Flow Features:** Tracks short-term Order Flow Imbalance (OFI) and volume-weighted trade delta over sliding rolling windows ($1\text{s}, 5\text{s}, 30\text{s}$).
3. **Volatility & Liquidity:** Computes trailing realized variance and flags liquidity degradation or spread widening events.

### 2.3 Predictive Layer
Combines text embeddings, historical context, and microstructure vectors:
$$[E_t \oplus H_t \oplus M_t] \longrightarrow \text{Event-Conditioned Predictive Model} \longrightarrow P\left(\Delta F_{t+h}\right), \;\mathcal{H}_{\text{uncertainty}}$$
- Evaluates whether the predicted return exceeds the required hurdle rate (spread + fees + slippage buffer).
- If entropy $\mathcal{H}$ is elevated, emits **NO TRADE**.

### 2.4 Execution and Risk Layer
Simulates trade execution under realistic market microstructure friction:
$$\text{Expected Net P\&L} \longrightarrow \text{Position Sizing} \longrightarrow \text{Pre-Trade Risk Checks} \longrightarrow \text{Simulated Fill Engine}$$
- Enforces hard stop-losses based on Maximum Adverse Excursion (MAE) priors.
- Evaluates queue position and fill probability against historical L2 depth.

---

## 3. Strict 10-Stage Timestamp Taxonomy

To eliminate temporal ambiguity and ensure rigorous latency profiling, the system defines and tracks **10 distinct timestamps** across the lifecycle of every event:

```
+----------------------------------------------------------------------------------------------------+
|                                    10-STAGE TIMESTAMP SEQUENCE                                     |
|                                                                                                    |
| 1. t_pub: Stated news publication time (wire clock)                                                |
|    |                                                                                               |
| 2. t_provider: Timestamp news packet arrived at provider gateway                                   |
|    |                                                                                               |
| 3. t_local_recv: Network socket receipt at local NIC (first byte parsed)                           |
|    |                                                                                               |
| 4. t_ingest: Ingestion pipeline completion (parsing, deduplication, SQLite write)                  |
|    |                                                                                               |
| 5. t_feat: Feature generation complete (NLP inference + market state extraction)                   |
|    |                                                                                               |
| 6. t_pred: Model prediction forward pass finished (distribution evaluated)                         |
|    |                                                                                               |
| 7. t_signal: Signal emitted by trading logic (threshold passed or NO_TRADE)                        |
|    |                                                                                               |
| 8. t_submit: Order packet serialized and dispatched to network buffer                              |
|    |                                                                                               |
| 9. t_ack: Exchange gateway order receipt acknowledgement                                           |
|    |                                                                                               |
| 10. t_fill: Exchange trade execution confirmation                                                  |
+----------------------------------------------------------------------------------------------------+
```

### Clock Discipline Rules:
1. **Universal UTC Persistence:** Every timestamp stored in database tables or logs MUST use UTC in ISO 8601 string format (`YYYY-MM-DDTHH:MM:SS.ffffff+00:00`).
2. **Monotonic Latency Measurement:** All in-process interval measurements ($\Delta t$) MUST use high-resolution monotonic clocks (`time.perf_counter_ns()` in Python or `std::chrono::steady_clock` in C++) to prevent clock-skew distortion from NTP adjustments.
3. **Causal Anchor:** Forward backtest returns MUST strictly measure from quote state at $t_{\text{pred}}$ (or $t_{\text{submit}} + \Delta_{\text{network}}$ in execution simulations), NEVER from $t_{\text{pub}}$.

---

## 4. Hot Path vs. Research Path Separation

| Feature / Dimension | Research Path (Offline Exploration) | Hot Path (Real-Time Production Inference) |
|---|---|---|
| **Primary Language** | Python 3.12+ (PyTorch, Pandas, Polars) | C++20 / Rust or Optimized Python with C-bindings |
| **Model Runtime** | PyTorch CUDA / MPS / CPU (full FP32/BF16) | ONNX Runtime (Quantized INT8 / FP16) |
| **Model Architecture** | Exploration of BERT, ModernBERT, DeBERTa | Compact DistilRoBERTa / Quantized Transformer |
| **Historical Retrieval** | Full dense embedding search via Chroma / FAISS | In-memory pre-compiled HNSW or SQLite-vec |
| **Generative LLMs** | Heavy RAG (Gemini / Claude / local 7B-70B models) | **STRICTLY EXCLUDED** from hot inference path |
| **Memory Management** | Dynamic allocation, Python GC | Zero dynamic allocations in critical loop, ring buffers |
| **Data Ingestion** | Historical batch replays, bulk JSON/Parquet | Lock-free IPC, ZeroMQ / Shared Memory queues |
| **Target Latency Budget** | Unconstrained (minutes/hours per epoch) | $< 25\text{ms}$ (Feature Generation + Model Inference) |

> **Architectural Guardrail:** Heavyweight generative LLMs (GPT-4, Gemini Pro, Claude 3.5, or local 70B parameters) are **strictly barred from the real-time hot path**. Generative models introduce unpredictable multi-second token generation latency, non-deterministic token latencies, and high inference costs. In the hot path, all natural language inference must execute via compact, discriminative Transformers running on ONNX Runtime. Generative models may be utilized in the Research Path for automated dataset synthesis, synthetic scenario generation, and post-mortem trade critique.

---

## 5. Architectural Scaffolding & Component Interfaces

The following lightweight, reusable interfaces define the boundary contracts between components (to be implemented in future phases without breaking existing Phase 1 ingestion):

```python
# docs/alpha/interfaces_preview.py (Architectural Contract)
from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime

@dataclass(frozen=True)
class EventFeatures:
    event_id: str
    entity_ticker: str
    event_class: str
    surprise_delta: Optional[float]
    sentiment_score: float
    materiality_score: float
    embedding: List[float]
    t_feat_utc: str
    monotonic_feat_ns: int

@dataclass(frozen=True)
class MarketState:
    instrument_id: str
    bid_price: float
    ask_price: float
    mid_price: float
    spread: float
    book_imbalance: float
    realized_vol_5m: float
    t_market_utc: str
    monotonic_mkt_ns: int

@dataclass(frozen=True)
class PredictionDistribution:
    prediction_id: str
    event_id: str
    instrument_id: str
    horizon_seconds: float
    expected_return: float
    p_long: float
    p_short: float
    p_neutral: float
    confidence: float
    entropy: float
    is_actionable: bool
    t_pred_utc: str
    monotonic_pred_ns: int
```

---

## 7. Phase 2 Implementation Architecture: Data Capture, Storage & Temporal Alignment

Phase 2 constructed the foundational data capture and alignment layer in ackend/alpha/:

`
+====================================================================================================+
|                                  PHASE 2 DATA SUBSYSTEM ARCHITECTURE                               |
+====================================================================================================+

   [ INCOMING NEWS DISCLOSURE ]                [ MARKET DATA TICKS & QUOTES ]
   (NSE/BSE Filings, SEC EDGAR)                 (Zerodha Kite Binary, TruData, Replay)
                |                                                 |
                v                                                 v
   [ BaseNewsAdapter / NSEAdapter ]             [ BaseMarketDataProvider / KiteDecoder ]
   - Canonical Normalization                    - Binary Frame Decoding (LTP / 5-Depth)
   - Multi-Tier Instrument Mapping              - FeedIntegrityMonitor (Gaps, Dups, Staleness)
   - Non-Overwriting Revision Tracking          - Bounded Ingress Queue (Backpressure)
                |                                                 |
                v                                                 v
   [ SQLite Relational Tier ]                   [ Partitioned Append-Only Store ]
   - articles table (Phase 1 compat)            - Partitioned by Date & Instrument
   - connector_state & schema migrations        - Chunked .jsonl.gz (58x compression)
   - RawPayloadStore (content-hashed)           - Manifest per partition
                |                                                 |
                +-----------------------+-------------------------+
                                        |
                                        v
                       [ TEMPORAL ALIGNMENT ENGINE ]
                       - As-Of Join: max{t_quote : t_quote <= t_local_received}
                       - Strict Zero Look-Ahead Invariant Enforcement
                       - Micro-volatility & Spread & Depth Extraction
                       - Multi-Horizon Forward Outcome Anchoring (100ms - 5m)
                                        |
                                        v
                       [ RESEARCH DATASET EXPORTER & MANIFEST ]
                       - Reproducible JSONL / CSV Export
                       - Cryptographic Dataset SHA-256 Checksum
`

### Module Directory:
- ackend/alpha/instruments.py: Instrument Master & Multi-Tier Relevance Taxonomy.
- ackend/alpha/schemas.py: Versioned Canonical Pydantic Schemas (NewsEvent, MarketEvent, DepthLevel).
- ackend/alpha/timestamps.py: Multi-Stage Timestamp Attribution & Clock Discipline.
- ackend/alpha/storage.py: Dual-Tiered Storage Engine (PartitionedMarketDataWriter & RawPayloadStore).
- ackend/alpha/news_capture.py: News Capture Abstraction, Revision Tracking & Quarantine.
- ackend/alpha/market_data.py: Provider-Neutral Market Data Scaffolding & Feed Integrity Monitor.
- ackend/alpha/alignment.py: Temporal Alignment Engine executing Causal As-Of Joins.
- ackend/alpha/replay.py: Deterministic Historical Replay Engine.
- ackend/alpha/quality.py: Data Quality Engine & Anomaly Auditor.
- ackend/alpha/dataset.py: Research Dataset Exporter & Manifest Generator.
- ackend/alpha/cli.py & scripts/alpha_pipeline.py: User-Facing CLI Tooling.
