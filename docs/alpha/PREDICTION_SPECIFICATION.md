# TickerWatch Alpha — Prediction Targets, Mathematical Formulation & Temporal Integrity

> **Document Version:** 1.0.0-alpha  
> **Status:** APPROVED BASELINE — PHASE 0  
> **Author:** Quantitative Modelling & Statistical Architecture  
> **Scope:** Formal Target Definitions, Excursion Metrics & Leakage Prevention Guardrails  

---

## 1. Mathematical Formulation of the Prediction Target

Rather than attempting to forecast an unconditioned point estimate of price, TickerWatch Alpha models the **conditional probability distribution of forward price movement**:

$$P\left(\Delta F_{t+h} \in \mathcal{Y} \;\middle|\; E_t, H_t, M_t\right)$$

Where:
- $t$: The strictly causal **Prediction Timestamp** (the exact moment at which feature generation completes and inference commences).
- $h$: The forecast horizon ($h \in \{100\text{ms}, 1\text{s}, 5\text{s}, 30\text{s}, 5\text{min}\}$).
- $E_t$: The information representation extracted from the incoming news event at or before $t$.
- $H_t$: The historical event context vector retrieved from the chronological index prior to $t$ ($\tau \le t - \delta_{\text{purge}}$).
- $M_t$: The contemporaneous order-book and market state at timestamp $t$.
- $\Delta F_{t+h}$: The future price trajectory metric of the futures contract over the interval $[t, t+h]$.
- $\mathcal{Y}$: The target space (either a continuous return distribution or discrete ordinal bins $\{-1, 0, 1\}$ with an explicit **NO TRADE** neutral class).

---

## 2. Candidate Prediction Horizons and Data Feasibility

| Horizon ($h$) | Target Duration | Timestamp Resolution Required | Order-Book Dynamics | Feasibility with Retail / Broker API | Feasibility with L3 / Tick Co-location | Phase 0 Recommendation |
|---|---|---|---|---|---|---|
| **$h_1$** | **100 ms** | Microsecond ($\mu\text{s}$) / Sub-millisecond | Top-of-book order cancellations, queue priority, immediate latency race | `[UNFEASIBLE]` (Network transit jitter $> 30\text{ms}$) | `[REQUIRES EXTERNAL VERIFICATION]` (Co-located hardware) | **RESEARCH ONLY (Simulated)**; Excluded from primary benchmarks until co-location verified. |
| **$h_2$** | **1 second** | Millisecond ($\text{ms}$) | First wave of algorithmic repricing, aggressive market order sweeps | `[MARGINAL]` (Requires dedicated local WebSocket daemon) | `[VERIFIED]` Standard institutional tick feeds | **ACTIVE BENCHMARK** (Subject to tick feed verification). |
| **$h_3$** | **5 seconds** | Millisecond ($\text{ms}$) | Secondary order book replenishment, algorithmic spread re-widening | `[VERIFIED]` Achievable via standard broker WebSocket feeds | `[VERIFIED]` | **PRIMARY BENCHMARK** |
| **$h_4$** | **30 seconds** | Second ($\text{s}$) | Broad market participant absorption, sector sympathy moves | `[VERIFIED]` Stable across standard commercial feeds | `[VERIFIED]` | **PRIMARY BENCHMARK** |
| **$h_5$** | **5 minutes** | Second ($\text{s}$) | Short-term drift completion, post-event liquidity stabilization | `[VERIFIED]` Readily observable on 1-minute historical bars | `[VERIFIED]` | **PRIMARY BENCHMARK** |

> **Feasibility Constraint:** The 100 ms horizon requires microsecond-stamped Level 3 matching engine data that cannot be reliably evaluated over public internet broker APIs. While mathematically specified below, $h = 100\text{ms}$ will remain a simulated exploratory target and will not be used as the gatekeeper for strategy validation.

---

## 3. Formal Definition of Prediction Targets

Let the continuous limit order book at timestamp $\tau$ be defined by top-of-book quotes:
- $P_{\text{bid}}(\tau)$: Best bid price.
- $P_{\text{ask}}(\tau)$: Best ask price.
- $P_{\text{mid}}(\tau) = \frac{P_{\text{bid}}(\tau) + P_{\text{ask}}(\tau)}{2}$: Mid-price.
- $S(\tau) = P_{\text{ask}}(\tau) - P_{\text{bid}}(\tau)$: Bid-ask spread.

### 3.1 Target 1: Futures Mid-Price Return ($R_{\text{mid}}$)
The unconstrained theoretical price drift over horizon $h$:
$$R_{\text{mid}}(t, h) = \frac{P_{\text{mid}}(t + h) - P_{\text{mid}}(t)}{P_{\text{mid}}(t)}$$
*Usage:* Used strictly for theoretical signal evaluation, Information Coefficient (IC) calculation, and model loss optimization. It does not reflect trade execution friction.

### 3.2 Target 2: Executable Long-Side Return ($R_{\text{exec}}^{\text{long}}$)
The net return realized by crossing the spread to buy at the current ask $P_{\text{ask}}(t)$ and liquidating at the future bid $P_{\text{bid}}(t+h)$, after factoring statutory transaction costs and estimated market impact:
$$R_{\text{exec}}^{\text{long}}(t, h) = \frac{P_{\text{bid}}(t + h) - P_{\text{ask}}(t) - \text{Impact}_{\text{entry}} - \text{Impact}_{\text{exit}}}{P_{\text{ask}}(t)} - C_{\text{statutory}}$$
Where:
- $\text{Impact} = f(\text{OrderSize}, \text{BookDepth})$.
- $C_{\text{statutory}}$: Sum of STT (0.02% on sell), NSE transaction fee (0.00173%), GST (18%), stamp duty (0.002% on buy), and broker commission.

### 3.3 Target 3: Executable Short-Side Return ($R_{\text{exec}}^{\text{short}}$)
The net return realized by initiating a short position at the current bid $P_{\text{bid}}(t)$ and covering at the future ask $P_{\text{ask}}(t+h)$:
$$R_{\text{exec}}^{\text{short}}(t, h) = \frac{P_{\text{bid}}(t) - P_{\text{ask}}(t + h) - \text{Impact}_{\text{entry}} - \text{Impact}_{\text{exit}}}{P_{\text{bid}}(t)} - C_{\text{statutory}}$$

### 3.4 Target 4: Maximum Favourable Excursion (MFE)
The maximum potential peak profit achieved at any point during the entire holding period $[t, t+h]$:
$$\text{MFE}_{\text{long}}(t, h) = \max_{\tau \in [t, t+h]} \left( \frac{P_{\text{bid}}(\tau) - P_{\text{ask}}(t)}{P_{\text{ask}}(t)} \right)$$
$$\text{MFE}_{\text{short}}(t, h) = \max_{\tau \in [t, t+h]} \left( \frac{P_{\text{bid}}(t) - P_{\text{ask}}(\tau)}{P_{\text{bid}}(t)} \right)$$
*Usage:* Measures the theoretical upper bound of edge available before mean reversion.

### 3.5 Target 5: Maximum Adverse Excursion (MAE)
The worst intra-interval drawdown experienced prior to horizon completion:
$$\text{MAE}_{\text{long}}(t, h) = \min_{\tau \in [t, t+h]} \left( \frac{P_{\text{bid}}(\tau) - P_{\text{ask}}(t)}{P_{\text{ask}}(t)} \right)$$
$$\text{MAE}_{\text{short}}(t, h) = \min_{\tau \in [t, t+h]} \left( \frac{P_{\text{bid}}(t) - P_{\text{ask}}(\tau)}{P_{\text{bid}}(t)} \right)$$
*Usage:* Mandatory metric for calibrating stop-loss thresholds and calculating the risk of adverse execution.

### 3.6 Target 6: Realised Volatility ($\sigma_{\text{realised}}$)
The annualized standard deviation of high-frequency returns across $K$ sub-intervals within $[t, t+h]$:
$$\sigma_{\text{realised}}(t, h) = \sqrt{\frac{252 \times 6.25 \times 3600}{h} \sum_{k=1}^K \left( \ln \frac{P_{\text{mid}}(\tau_k)}{P_{\text{mid}}(\tau_{k-1})} \right)^2}$$
*Usage:* Conditions risk-adjusted position sizing and flags volatility expansion regimes.

### 3.7 Target 7: Prediction Confidence and Uncertainty
Model outputs must include an epistemic uncertainty estimate. For classification into classes $\{-1, 0, 1\}$:
- Softmax probability vector: $\mathbf{p} = [p_{\text{short}}, p_{\text{neutral}}, p_{\text{long}}]$.
- Confidence: $C = \max(p_{\text{short}}, p_{\text{long}})$.
- Entropy / Uncertainty: $\mathcal{H}(\mathbf{p}) = -\sum_{i} p_i \ln p_i$.
- **NO TRADE Rule:** If $C < \theta_{\text{threshold}}$ (e.g., $0.65$) or $\mathcal{H}(\mathbf{p}) > \eta_{\text{uncertainty}}$, the decision engine outputs **NO TRADE** ($y_t = 0$).

---

## 4. Temporal Integrity & Bias Prevention Architecture

To ensure strict academic validity, the labelling and training pipeline enforces zero tolerance for information leakage.

```
TIMELINE OF AN EVENT & CAUSAL PREDICTION TIMESTAMP:

t_event_occurs        t_provider_receives   t_local_receipt    t_ingest_done      t_feature_done (t_pred)   t_horizon (t + h)
      |                       |                    |                 |                     |                    |
======*=======================*====================*=================*=====================*====================*=====> TIME
      \                      /                     \                 /                     ^                    ^
       Provider Latency Delta                       Ingestion Delta                        |                    |
                                                                                PREDICTION TIMESTAMP     FORWARD EVALUATION
                                                                                (Strict Causal Cutoff)  (Label Computation)
```

### 4.1 Strict Look-Ahead Bias Prevention
1. **The Causal Cutoff Rule:** No feature vector may access any market tick, order book snapshot, or news item with a timestamp $\tau > t_{\text{pred}}$.
2. **Feature Generation Lag:** $t_{\text{pred}}$ is defined as the time when feature calculation finishes ($t_{\text{pred}} = t_{\text{ingest}} + \Delta t_{\text{features}}$). Forward return calculations begin *strictly* at the best available quote at $t_{\text{pred}}$, never at the original publication timestamp $t_{\text{pub}}$.

### 4.2 Publication Time vs. Ingestion Time (The "Wire Delay" Hazard)
- Articles routinely carry a stated publication time ($t_{\text{pub}}$) that precedes the moment the data packet arrives at our network interface ($t_{\text{local\_recv}}$) by hundreds of milliseconds or even minutes.
- **Enforcement:** Backtest labels MUST NOT use $t_{\text{pub}}$ as the entry point. Calculating price movement from $t_{\text{pub}}$ assumes instantaneous zero-latency ingestion and manufactures massive, fictitious "alpha" that cannot be executed in reality. All return intervals are measured from $t_{\text{local\_recv}} + \Delta_{\text{processing}}$.

### 4.3 News Arriving After Market Absorption (The Stale News Hazard)
- If an event has already moved the price before local ingestion completes (i.e. other high-frequency market participants processed the news at $t_{\text{pub}}$), the order book at $t_{\text{pred}}$ will already reflect the shift.
- **Enforcement:** Compute the immediate pre-signal momentum:
  $$\Delta P_{\text{prior}} = \frac{P_{\text{mid}}(t_{\text{pred}}) - P_{\text{mid}}(t_{\text{pub}})}{P_{\text{mid}}(t_{\text{pub}})}$$
  If $|\Delta P_{\text{prior}}| > 3 \times \sigma_{\text{baseline}}$, the signal is flagged as `ALREADY_ABSORBED` and suppressed (**NO TRADE**).

### 4.4 Duplicate, Syndicated and Serial Articles
- A single corporate disclosure often generates dozens of syndicated articles over 30 minutes from news wires (PTI, Reuters, Bloomberg, Moneycontrol).
- **Enforcement:** The existing TickerWatch `content_hash` and entity deduplication engine groups incoming items into an active 60-minute event cluster. Only the *first chronological arrival* of an event generates a prediction. All subsequent syndicated duplicates update the cluster metadata without triggering new trade signals.

### 4.5 Historical Data Revisions
- Economic prints (GDP, inflation) and corporate filings are occasionally revised hours or days after initial publication.
- **Enforcement:** Training sets must strictly use point-in-time (PIT) unrevised historical records as first broadcast.

### 4.6 Survivorship Bias
- Backtesting only on companies currently constituent in the NIFTY 50 creates positive selection bias.
- **Enforcement:** Historical instrument universes are dynamically reconstructed using point-in-time index composition records for each target trading date.

### 4.7 Trading Sessions, Gaps and Market Holidays
- Corporate filings released after 15:30 IST or over weekends cannot be executed until 09:15 IST the next trading session.
- **Enforcement:** Post-market events are partitioned into a dedicated "Overnight Disclosures" test bucket. Their prediction horizon is defined as the opening auction equilibrium return ($t_{\text{open}} = 09:15:00$ to $09:15:30\text{ IST}$), explicitly separating overnight gap risk from intra-day continuous trading.

### 4.8 Futures Rollover and Expiry Consistency
- Near-month futures contracts expire on the last Thursday of each month (for NSE equity derivatives).
- **Enforcement:** Continuous price series must be constructed using liquidity-based rollover rules (switching to the next-month contract when its daily volume exceeds the near-month contract, typically $T-2$ days before expiry) with backward ratio-adjusted splicing to prevent artificial price jumps.

### 4.9 Universal UTC Time-Zone Standardization
- All database timestamps, market feed ticks, and logs are persisted strictly in UTC ISO 8601 with microsecond resolution. Monotonic offsets (`time.perf_counter_ns()`) are maintained locally for measuring in-process latency deltas.
