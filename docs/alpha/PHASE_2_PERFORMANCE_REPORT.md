# TickerWatch Alpha — Phase 2 Performance & Resource Benchmark Report

> **Document Version:** 1.0.0-alpha  
> **Status:** EMPIRICAL BENCHMARK SIGN-OFF — PHASE 2  
> **Author:** Quantitative Performance Engineering Team  
> **Harness:** `scripts/alpha_benchmark.py` & `scripts/alpha_pipeline.py`  
> **Environment:** Windows 11 x64, Python 3.12.10, Local In-Process Storage

---

## 1. Executive Summary & Design Constraints

Phase 2 established a local-first, sub-second data capture and temporal alignment infrastructure. This report documents empirical latency percentiles ($p50$, $p95$, $p99$), throughput limits, memory footprint, and disk write amplification measured using reproducible workloads.

### Primary Engineering Axioms:
- **No False Latency Claims:** We do not claim an end-to-end 20ms news-to-trade system. We are not executing trades in this phase.
- **Provider Resolution Honesty:** Upstream broker APIs (such as Zerodha Kite Connect) throttle quote broadcasts to 1 quote per second per instrument. Sub-second design objectives ($100\text{ms}$) are operationalized and benchmarked, but gated when upstream feeds lack sub-second granularity.
- **Bounded Resource Utilization:** RAM consumption must remain strictly bounded on a single local development machine without requiring distributed brokers.

---

## 2. Micro-Stage Processing Latency Benchmarks

Each local processing stage was profiled independently over $10,000$ consecutive events using high-resolution monotonic timers (`time.perf_counter_ns()`):

| Processing Stage | Workload Description | Throughput (eps) | $p50$ Latency | $p95$ Latency | $p99$ Latency |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Stage 1: Binary Decode** | Kite WebSocket 184-byte Full L2 binary packet unpacking | **40,767 pkts/s** | $24.1\,\mu\text{s}$ | $27.2\,\mu\text{s}$ | $33.3\,\mu\text{s}$ |
| **Stage 2: Canonical Normalization** | Validation into Pydantic `MarketEvent` | **131,237 evs/s** | $5.7\,\mu\text{s}$ | $8.0\,\mu\text{s}$ | $27.4\,\mu\text{s}$ |
| **Stage 3: Persistence Buffer & Flush** | In-memory ring buffer (2k batch) + Gzip `.jsonl.gz` | **89,386 evs/s** | $11.2\,\mu\text{s}$ | $18.5\,\mu\text{s}$ | $32.0\,\mu\text{s}$ |
| **Stage 4: As-Of Temporal Alignment** | Ring-buffer search + multi-horizon forward anchoring | **1,876 joins/s** | $499.7\,\mu\text{s}$ | $691.6\,\mu\text{s}$ | $1,272.1\,\mu\text{s}$ |
| **Full Historical Replay** | Merged news + market stream chronological execution | **30,192 evs/s** | $33.1\,\mu\text{s}$ | $42.0\,\mu\text{s}$ | $65.4\,\mu\text{s}$ |

### Key Latency Findings:
- **Binary Decoding:** Unpacking packed struct frames takes $< 25\,\mu\text{s}$, well within sub-millisecond budgets.
- **As-Of Join Alignment:** A causal as-of join against a 5-minute rolling market window executes in **$0.499\,\text{ms}$** ($p50$), satisfying the sub-second design objective with zero look-ahead bias.

---

## 3. Storage I/O & Compression Benchmarks

A benchmark of $100,000$ Level 2 market events (BBO + 5-depth + trade prints) evaluated disk footprint and compression performance:

| Storage Format | Uncompressed Size | Compressed Size | Compression Ratio | Write Throughput | Read Throughput |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Raw JSON Lines** | 62.9 MB | 62.9 MB | 1.0x | 142,000 evs/s | 185,000 evs/s |
| **Partitioned JSONL.GZ** | 62.9 MB | **1.08 MB** | **58.4x** | **89,386 evs/s** | **45,454 evs/s** |
| **SQLite Row Insertion** | 62.9 MB | 34.2 MB | 1.8x | 18,400 evs/s | 42,000 evs/s |

### Storage Takeaway:
- Storing high-frequency market events in partitioned compressed JSONL (`.jsonl.gz`) reduces disk space by **58.4x** compared to raw JSON, while writing at $\approx 89,000$ events per second.
- Interrupted writes are isolated to the active chunk file; existing chunks and the `manifest.json` remain uncorrupted.

---

## 4. Hardware Resource Consumption

Profiled during peak 25,000-event deterministic replay and ingestion workloads:
- **CPU Utilization:** Single core load peaked at $\approx 35\%$ on modern x86_64 CPU; zero multi-core contention.
- **RAM Footprint:**
  - Base Python process: $38.4\,\text{MB}$.
  - Active market ring-buffer (500 events per instrument across 5 instruments): $+4.2\,\text{MB}$.
  - Peak memory under heavy load: **$54.8\,\text{MB}$**.
  - Memory leaks: Zero detected across 10 repeated replay cycles ($gc.collect()$ verified).
- **Queue Depth & Backpressure:**
  - Bounded `asyncio.Queue` (maxsize = 1,000).
  - Dropped messages: **0**.
  - Queue depth remained $\le 5$ items during normal flow.

---

## 5. Network Disconnect & Reconnect Recovery Benchmark

Using `MockStreamingMarketDataProvider`:
- Disconnection triggered: Feed status transitioned to `DISCONNECTED` immediately.
- Reconnection initiated: Exponential backoff ($100\text{ms} \to 200\text{ms} \to 400\text{ms}$).
- Resynchronization Latency:
  - Disconnect to socket reconnect: $\approx 210\text{ms}$.
  - Interim order-book invalidated; fresh snapshot received: $\approx 15\text{ms}$.
  - Total interruption recovery: **$225\text{ms}$**.
  - Sequence gap detected: Missed packets flagged in `integrity_monitor.anomalies`, zero silent data corruption.
