# TickerWatch Alpha — Phase 2 Sub-Second News & Market Alignment Engine Design

> **Document Version:** 1.0.0-alpha  
> **Status:** APPROVED SYSTEM DESIGN — PHASE 2  
> **Authors:** Quantitative Microstructure & Mathematical Modeling Team  
> **Mandate:** Zero Look-Ahead Bias, Causal As-Of Join Integrity, and Multi-Horizon Forward Anchoring

---

## 1. The Causal Alignment Problem

The purpose of the Temporal Alignment Engine is to reconstruct the exact information and market microstructure state genuinely available to the system at each historical or real-time decision timestamp $\tau_{\text{decision}}$.

In live deployment, the earliest timestamp at which an algorithm can initiate action upon a news event is **$t_{\text{local\_received}}$** (the local network socket arrival time), NOT the publication time $t_{\text{pub}}$ reported by the exchange. If $t_{\text{pub}}$ is 10:00:00.000 UTC, but the network packet arrives at the local machine at 10:00:03.450 UTC:
- Any market price change between 10:00:00 and 10:00:03 has already occurred before the system learned of the event.
- Aligning against the 10:00:00 market quote and claiming the subsequent return as actionable alpha is **fatal look-ahead leakage**.

$$\tau_{\text{decision}} \equiv t_{\text{local\_received}}$$

---

## 2. Temporal Architecture & The Causal As-Of Join

The alignment engine performs a mathematically rigorous **as-of join**:

```
                                      AS-OF JOIN BOUNDARY
                                       \tau_{decision}
                                              |
MARKET DATA STREAM:  ---(M_1)-----(M_2)-------|--------(M_3)-----(M_4)-----> (Time t)
                                    ^         |          ^         ^
                                    |         |          |         |
                               Pre-Event State|     +100ms     +1.0s
                             (Latest <= \tau) |     Forward Horizons
                                              |
NEWS ARRIVAL:        --------------------(NEWS_EVENT)---------------------->
                                       t_{local_received}
```

### 2.1 Pre-Event Market Context $\mathcal{M}_{\text{pre}}(\tau)$
For each affected instrument $I$, the pre-event context is defined as the unique latest market event $M^*$ satisfying:

$$M^*(I, \tau) = \arg\max_{M \in \mathcal{S}(I)} \{ t_{\text{local\_received}}(M) : t_{\text{local\_received}}(M) \le \tau \}$$

**Strict Invariant:**  
$$\forall M \in \text{PreEventFeatures}(\tau), \quad t_{\text{local\_received}}(M) \le \tau$$

### Pre-Event State Variables Captured:
- `bid_price`, `ask_price`: Top of book quote.
- `mid_price`: $\frac{P_{\text{bid}} + P_{\text{ask}}}{2}$.
- `spread`: $P_{\text{ask}} - P_{\text{bid}}$.
- `depth`: Aggregate top-5 bid and ask liquidity.
- `microprice`: Volume-weighted mid-price.
- `recent_trade`: Last traded price and trade size.
- `short_term_volatility`: Standard deviation of returns in window $[\tau - 30\text{s}, \tau]$.
- `feed_freshness_ms`: $\tau - t_{\text{local\_received}}(M^*)$.
- `session_state`: `OPEN`, `PRE_OPEN`, `CLOSED`, `HALTED`.

---

## 3. Post-Event Forward Outcome Horizons

To support subsequent Phase 5 predictive model research without polluting pre-event feature sets, forward market states are observed and stored in a **separate outcome vector**:

$$\mathcal{H} = \{ 100\text{ms}, 1\text{s}, 5\text{s}, 30\text{s}, 5\text{min} \}$$

For each horizon $h \in \mathcal{H}$:
$$M_{\text{post}}(I, \tau, h) = \arg\min_{M \in \mathcal{S}(I)} \{ t_{\text{local\_received}}(M) : t_{\text{local\_received}}(M) \ge \tau + h \}$$

From $M_{\text{post}}$, forward price returns are computed:
$$R(I, \tau, h) = \frac{P_{\text{mid}}(\tau + h) - P_{\text{mid}}(\tau)}{P_{\text{mid}}(\tau)}$$

### Gating by Timestamp Resolution:
- If market data resolution is 1-second (e.g. standard broker quotes), the $100\text{ms}$ horizon is flagged as `RESOLUTION_INSUFFICIENT` and omitted from model training targets.
- Sub-second horizons are active only when high-resolution tick-by-tick feeds are present.

---

## 4. Market Session, Holiday & Anomaly Handling

Indian equity markets (NSE/BSE) follow strict operating schedules:
- **Pre-Open Session:** 09:00:00 to 09:08:00 IST (03:30 to 03:38 UTC).
- **Normal Trading Session:** 09:15:00 to 15:30:00 IST (03:45 to 10:00 UTC).
- **Market Closed / Weekend / Exchange Holidays:** All other hours.

### Anomaly Rules:
1. **Out-of-Hours News:** When an announcement arrives while the market is `CLOSED`:
   - Pre-event state is tagged `MARKET_CLOSED`.
   - Forward returns are not computed against closed quotes; the event is marked `OUT_OF_HOURS_OVERNIGHT` and held for market-open auction alignment.
2. **Stale Quotes:** If $t_{\text{local\_received}}(M^*) < \tau - 60\text{s}$ during an active open session, the observation is tagged `STALE_PRE_EVENT_QUOTE`.
3. **Missing Observations:** If zero market ticks exist for the target instrument, the alignment status is recorded as `UNALIGNED_NO_MARKET_DATA`. The event is never discarded; it is retained in research manifests with an explicit quality flag.
4. **Trading Halts:** If exchange session status is `HALTED`, forward return calculation is suspended.
