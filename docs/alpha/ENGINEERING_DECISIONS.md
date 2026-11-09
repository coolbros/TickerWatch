# TickerWatch Alpha — Technology, Hardware & Model Engineering Decisions

> **Document Version:** 1.0.0-alpha  
> **Status:** APPROVED BASELINE — PHASE 0  
> **Author:** High-Performance Computing & Machine Learning Systems  
> **Mandate:** Principled Technology Selection & Experimental Latency Treatment  

---

## 1. Core Engineering Principles

1. **No Premature Architecture Commitments:** Model architectures and hardware stacks are evaluated on measured benchmarks rather than theoretical popularity.
2. **Latency is an Experimental Variable, Not a Guarantee:** We do not claim an arbitrary "20 ms end-to-end guarantee." Low latency is an empirical variable to be profiled, isolated, and quantified against execution slippage.
3. **CPU-First Optimization Before Dedicated Accelerators:** Maximize cache locality, vectorized execution (AVX2 / AVX-512), multi-threaded work stealing, and memory alignment on standard x86-64 / ARM64 server CPUs before introducing specialized accelerators.
4. **FPGA / Custom Silicon Deferred:** FPGA acceleration introduces high firmware development cycles, synthesis bottlenecks, and rigid logic pipelines. FPGAs are explicitly deferred until CPU profiling confirms that hardware execution represents the dominant bottleneck compared to wire delay and queueing.

---

## 2. Natural Language Processing Model Evaluation Criteria

In selecting the primary financial NLP model for event understanding, candidate architectures will be formally evaluated across eight quantitative dimensions:

```
+----------------------------------------------------------------------------------------------------+
|                                      MODEL SELECTION RADAR                                         |
|                                                                                                    |
|                             [ Financial Domain Accuracy ]                                          |
|                                         /     \                                                    |
|                                        /       \                                                   |
|                        [ ONNX Export ]           [ Inference Latency ]                             |
|                              |                         |                                           |
|                              |                         |                                           |
|                     [ Quantization (INT8) ]       [ CPU Efficiency ]                               |
|                              \                         /                                           |
|                               \                       /                                            |
|                           [ Tokenizer Speed ]---[ Memory / Size ]                                  |
+----------------------------------------------------------------------------------------------------+
```

### 2.1 The 8 Formal Model Selection Criteria
1. **Financial Domain Performance:** Macro F1-score on corporate event classification, entity recognition, and numerical relation extraction on financial disclosures.
2. **Inference Latency:** Pure forward-pass latency on standard CPU threads (target budget $< 15\text{ms}$ at batch size 1, sequence length 128).
3. **Memory Consumption:** Peak working set memory footprint during continuous inference (target $< 500\text{MB}$).
4. **Quantization Compatibility:** Support for INT8 (VNNI) and FP16 quantization without catastrophic degradation of accuracy ($< 1\%$ F1 loss).
5. **ONNX Export Compatibility:** Clean conversion to ONNX graph representation without unsupported custom PyTorch ops.
6. **Tokenisation Overhead:** Latency of the tokenizer itself. Byte-Pair Encoding (BPE) or WordPiece tokenizers implemented in pure Python often take 5–10 ms just to segment text, consuming half the latency budget before neural computation starts. Fast Rust-backed tokenizers (`tokenizers` library) are mandatory.
7. **CPU Execution Efficiency:** Thread scalability, vectorized SIMD instruction utilization, and minimal thread-switching overhead under concurrent load.
8. **Model Parameter Size:** Compact size ($< 150\text{M}$ parameters) to fit within CPU L3 cache structures during continuous execution.

---

## 3. Preliminary Candidate Model Comparison

| Model Architecture | Parameters | Architecture Type | Financial Specialisation | Expected CPU Latency (FP32) | Expected CPU Latency (INT8 ONNX) | Tokenizer Overhead | Evaluation Verdict |
|---|---|---|---|---|---|---|---|
| **FinBERT (Prosus / Araci)** | 110M | Standard BERT-Base | High (Pretrained on Financial Corpus) | 25–40 ms | 8–15 ms | Fast (Rust WordPiece) | **Primary Benchmark Candidate** |
| **DistilRoBERTa-Financial** | 82M | Distilled RoBERTa | Moderate (Requires fine-tuning) | 15–25 ms | 5–10 ms | Fast (Rust BPE) | **Strong Hot-Path Candidate** |
| **ModernBERT-Base** | 149M | Modern Transformer (FlashAttn, RoPE, Unpadding) | General (Requires financial domain tuning) | 20–30 ms | 6–12 ms | Extremely Fast | **High-Potential Research Candidate** |
| **DeBERTa-v3-Small** | 44M | Disentangled Attention | General | 18–28 ms | 7–14 ms | Fast (SentencePiece) | **Ablation Candidate** |
| **Llama-3.2-1B / 3B** | 1B–3B | Autoregressive Decoder | Broad General Knowledge | 150–600 ms | 60–180 ms | Moderate | **Barred from Hot Path** (Research/Data Gen Only) |
| **Gemini 1.5 / Flash API** | Unknown (Cloud) | Multimodal Foundation | Broad General Knowledge | 300–1200 ms | N/A (Cloud Network Bound) | Network Bound | **Strictly Barred from Hot Path** |

---

## 4. Historical Event Retrieval Engine Decisions

Historical contextual intelligence requires retrieving $K$ relevant prior disclosures in $< 5\text{ms}$.

### 4.1 Vector Indexing: HNSW vs. Flat Index
- **Flat Index (Exact L2/Cosine):** Exact nearest neighbors, zero indexing overhead. At $N = 10,000$ disclosures, search time is $< 2\text{ms}$ on multi-core CPU.
- **Hierarchical Navigable Small World (HNSW):** Logarithmic query scaling ($O(\log N)$). Search time remains $< 1\text{ms}$ even at $N = 1,000,000$ records.
- **Decision:** Implement **HNSW** via an embedded in-process library (e.g., `sqlite-vec` or `hnswlib`). Avoid external network-attached vector databases (Pinecone, Weaviate, Milvus) which introduce 10–30 ms network round-trip latency.

### 4.2 Dense vs. Hybrid Retrieval
Pure dense embeddings can miss exact corporate entity names or specific numerical thresholds.
- **Decision:** Utilize **Hybrid Retrieval**: Combine dense semantic embeddings with the existing SQLite **FTS5** (Full-Text Search) BM25 index already implemented in Phase 1 (`articles_fts`). A Reciprocal Rank Fusion (RRF) step blends lexical and dense results offline.

---

## 5. Technology Stack & Language Boundaries

```
+----------------------------------------------------------------------------------------------------+
|                                    TECHNOLOGY BOUNDARY MATRIX                                      |
|                                                                                                    |
|   DOMAIN                  RESEARCH & BACKTESTING              PRODUCTION HOT PATH                  |
|   --------------------    ---------------------------------   ----------------------------------   |
|   Core Language           Python 3.12+                        C++20 or Rust / Cython               |
|   Deep Learning           PyTorch 2.4+ / Hugging Face         ONNX Runtime C++ API                 |
|   Data Manipulation       Polars / Pandas / DuckDB            Columnar FlatBuffers / Ring Buffers  |
|   Vector Index            Chroma / FAISS / In-Memory          Embedded HNSWlib (zero copy)         |
|   Persistence             SQLite (WAL) + Parquet              Lock-free Mapped Memory (shm)        |
|   Inter-Process Comms     Python Multiprocessing / asyncio    ZeroMQ / Shared Memory IPC           |
+----------------------------------------------------------------------------------------------------+
```

### 5.1 Python for Research & Backtesting
- Fast experimentation, rich quantitative ecosystem (PyTorch, Polars, SciPy, Statsmodels).
- Manages dataset partitioning, historical walk-forward cross-validation, and performance reporting.

### 5.2 ONNX Runtime for Low-Latency Inference
- Hardware-optimized graph execution engine developed by Microsoft.
- Supports operator fusion, memory reuse, and architecture-specific optimizations (AVX-512 VNNI for INT8 inference on Intel/AMD CPUs).
- Direct export from PyTorch via `torch.onnx.export`.

### 5.3 C++ for Latency-Sensitive Infrastructure (When Justified)
- Used for high-frequency order-book maintenance, ring-buffer WebSocket feed handling, and pre-trade risk checks.
- Bound to Python research harnesses via `pybind11` or `ctypes`.

---

## 6. Hardware Strategy & Acceleration Roadmap

1. **Phase 0–2 (Current):** Single multi-core developer workstation (Windows 11 / x86-64, multi-core CPU, local SSD). Profiling Python asyncio, SQLite WAL throughput, and baseline ONNX inference latency.
2. **Phase 3–5:** Dedicated Linux research node (Ubuntu 24.04 LTS, AMD EPYC or Intel Xeon with AVX-512, 64GB DDR5, NVMe RAID). Direct profiling of CPU thread affinity (`isolcpus`), memory pinning, and SIMD INT8 quantization.
3. **Phase 6+ (Conditional):** If and only if profiling reveals that model forward pass latency is the primary barrier to alpha capture (and market data network latency is sub-millisecond), evaluate GPU tensor cores (NVIDIA TensorRT) or FPGA NICs.
