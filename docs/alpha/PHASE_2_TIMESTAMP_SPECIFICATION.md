# TickerWatch Alpha — Phase 2 Timestamp Specification & Clock Discipline

> **Document Version:** 1.0.0-alpha  
> **Status:** APPROVED ENGINEERING SPECIFICATION — PHASE 2  
> **Authors:** Quantitative Engineering & Execution Architecture  
> **Mandate:** Zero Look-Ahead Leakage, Monotonic Latency Attribution, and Rigorous Clock Auditing

---

## 1. Research Axiom: The Causal Timeline

In quantitative financial research, temporal conflation is the single most common cause of spurious predictive backtests. If a system records an announcement as "known" at its reported publication time ($t_{\text{pub}}$), but the packet was actually received by the local server 4.2 seconds later ($t_{\text{local}}$), any trading simulation operating between $t_{\text{pub}}$ and $t_{\text{local}}$ is **leaking future information**.

**Central Principle:**  
*For any decision or prediction timestamp $\tau$, the model may only observe information that was genuinely received and decoded by the local system at or before $\tau$.*

$$\mathcal{F}_{\tau} = \sigma\left(\{ X_i : t_{\text{local\_received}}(i) \le \tau \}\right)$$

---

## 2. Multi-Stage Timestamp Attribution

Every event in TickerWatch Alpha carries an explicit vector of stage timestamps:

```
+==================================================================================================+
|                                    EVENT TIMELINE & LATENCY STAGES                               |
+==================================================================================================+

   [ Disclosed Time ] ----> [ Provider Observed ] ----> [ Local Ingress ] ----> [ Ingestion/Commit ]
       published_at           provider_observed_at       local_received_at        persisted_at
                                                              |
                                                              +--> local_monotonic_ns (perf_counter_ns)
```

### 2.1 News Event Timestamps
1. **`published_at` (UTC):** The timestamp reported on the face of the disclosure or filing (e.g. board meeting conclusion time, SEBI filing time).
2. **`provider_first_observed_at` (UTC):** The timestamp when the external data vendor or RSS feed published or syndicated the item.
3. **`local_received_at` (UTC):** The wall-clock UTC timestamp when the first byte of the news payload was read from the network socket by TickerWatch.
4. **`local_monotonic_ns` (int):** High-resolution monotonic clock tick (`time.perf_counter_ns()`). Immune to NTP slew, step adjustments, or leap seconds.
5. **`ingestion_completed_at` (UTC):** Wall-clock timestamp when normalization, entity mapping, and atomic SQLite/disk commit finished.

### 2.2 Market Event Timestamps
1. **`exchange_event_at` (UTC):** The match-engine timestamp assigned by the exchange matching engine (NSE/BSE).
2. **`provider_event_at` (UTC):** The timestamp when the market data vendor (e.g. TruData, Kite) packaged the tick.
3. **`local_received_at` (UTC):** The local wall-clock UTC arrival time at the local socket.
4. **`local_monotonic_ns` (int):** Monotonic arrival timestamp for microsecond-level intra-process elapsed time tracking.
5. **`persisted_at` (UTC):** Timestamp when the market event was flushed to append-only storage.

---

## 3. Measurable Latency Intervals

With this multi-stage timestamp vector, TickerWatch Alpha calculates explicit pipeline latencies:

$$\Delta t_{\text{discovery}} = t_{\text{local\_received}} - t_{\text{published}} \quad (\text{External news transmission delay})$$
$$\Delta t_{\text{feed}} = t_{\text{local\_received}} - t_{\text{exchange\_event}} \quad (\text{Exchange-to-local market data delay})$$
$$\Delta t_{\text{decode}} = t_{\text{decoded}} - t_{\text{local\_received}} \quad (\text{Local deserialization latency})$$
$$\Delta t_{\text{norm}} = t_{\text{normalized}} - t_{\text{decoded}} \quad (\text{Canonical schema mapping latency})$$
$$\Delta t_{\text{persist}} = t_{\text{persisted}} - t_{\text{normalized}} \quad (\text{Disk write and buffer flush latency})$$
$$\Delta t_{\text{align}} = t_{\text{aligned}} - t_{\text{local\_received}} \quad (\text{As-of order-book search latency})$$

---

## 4. Host Clock Discipline & Clock Drift Auditing

### 4.1 Clock Architecture
- Wall-clock timestamps are strictly converted to **UTC ISO-8601** strings (`YYYY-MM-DDTHH:MM:SS.ffffffZ`).
- Interval measurements within a single operating process **must never** subtract wall-clock datetimes (`datetime.now()`), which can jump backwards due to NTP adjustments. All durations are computed via `time.perf_counter_ns()` or `time.monotonic_ns()`.

### 4.2 Windows & POSIX Synchronization Protocol
- On Windows systems, host clock synchronization is audited using Windows Time Service (`w32tm /query /status`) or NTP daemon checks.
- When NTP synchronization is unreachable or clock offset uncertainty exceeds $\pm 50\text{ms}$:
  - The system records a `CLOCK_UNCERTAINTY_FLAG`.
  - Sub-second alignment windows ($100\text{ms}$) are marked provisional or downgraded to $1\text{s}$ resolution.
  - The system never pretends to possess millisecond precision if the host clock offset is unverified.
