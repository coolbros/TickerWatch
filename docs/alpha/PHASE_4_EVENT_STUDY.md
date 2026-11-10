# TickerWatch Alpha — Phase 4 Historical Market Response & Event-Study Engine (`PHASE_4_EVENT_STUDY`)

> **Phase:** Phase 4 — Work Package E  
> **Status:** IMPLEMENTED  
> **Primary Modules:** `backend/alpha/event_study.py`, `backend/alpha/outcomes.py`  

---

## 1. Objective & Design Principles

The Event-Study Engine associates historical financial disclosures with verified post-event market behavior without contaminating downstream predictive features or creating look-ahead leakage.

Every generated `EventOutcomeLabel` strictly enforces:
1. **Separated Label Storage:** Outcomes reside in dedicated `EventOutcomeLabel` / `StoredOutcomeRecord` schemas (`historical_event_outcomes` SQLite table), physically decoupled from live `FinancialEventRepresentation` feature payloads.
2. **Causal Horizon Maturity:** An outcome label is eligible for historical contextual conditioning at query decision timestamp $\tau_{\text{query}}$ **if and only if** `outcome_end_price_ts <= tau_query`. Unelapsed windows yield `OutcomeDataQualityStatus.IMMATURE` with return fields masked to `None`.
3. **Intrinsic Source Resolution Gating:** Intraday horizons (`1m`, `5m`, `30m`, `1h`) are refused (`INSUFFICIENT_RESOLUTION`) when source bars only support coarser intervals (e.g., daily EOD series), eliminating flat-fill artifacts.
4. **Calendar, Session & Gap Awareness:** Distinguishes active session continuous intervals from overnight gaps, exchange weekends, and holidays (`Asia/Kolkata` NSE/BSE `09:15–15:30 IST`; `America/New_York` NYSE/NASDAQ `09:30–16:00 ET` with DST handling).

---

## 2. Supported Outcome Horizons & Resolution Gates

| Horizon Enum | Meaning | Max Allowable Median Bar Spacing |
|---|---|---|
| `1m` | 1 trading minute | 60 seconds |
| `5m` | 5 trading minutes | 300 seconds |
| `30m` | 30 trading minutes | 900 seconds |
| `1h` | 60 trading minutes (crosses overnight boundary if near close) | 1800 seconds |
| `1d` | 1 exchange trading day | 86400 seconds |
| `5d` | 5 exchange trading days (skips weekends & holidays) | 86400 seconds |

Resolution estimation computes median consecutive spacing over intraday pairs ($<4\text{h}$) when present, preventing normal overnight gaps in minute-bar data from inflating estimated sampling spacing.

---

## 3. Computed Outcome Measures

For each `(event_id, instrument_id, horizon)` passing quality gates:
- **Simple Percentage Return:** $R_{\text{simple}} = \frac{P_{\text{end}}^{\text{adj}}}{P_{\text{start}}^{\text{adj}}} - 1$
- **Raw Log Return:** $r_{\log} = \ln\left(\frac{P_{\text{end}}^{\text{adj}}}{P_{\text{start}}^{\text{adj}}}\right)$
- **Benchmark-Adjusted Return:** $R_{\text{excess}} = R_{\text{simple}} - \beta \cdot R_{\text{benchmark}}$ over the synchronized window (default $\beta = 1.0$)
- **Window Realized Volatility:** $\sigma_{\text{RV}} = \sqrt{\sum_{i=2}^K \left(\ln \frac{P_i^{\text{adj}}}{P_{i-1}^{\text{adj}}}\right)^2}$ when $\ge 3$ observations cover the horizon
- **Maximum Favorable Excursion (MFE) & Maximum Adverse Excursion (MAE):** Path extrema $\max_{t} R_t$ and $\min_{t} R_t$ over $(t_{\text{start}}, t_{\text{end}}]$
- **Execution Liquidity Context:** Mean bid-ask spread in bps and cumulative window volume when available

---

## 4. Handling Real Market Mechanics

1. **Primary vs. Contextual Instrument Selection (`select_study_instruments`):**
   - Company events (Earnings, Contract Wins, Corporate Actions, Regulatory) assign the directly affected cash equity or single-stock future as primary, keeping `NSE:NIFTY_FUT:CURRENT` / `US:SPY:EQ` strictly as contextual benchmark.
   - `MACRO` / RBI / FinMin releases assign the index future (`NSE:NIFTY_FUT:CURRENT`) as primary.
2. **Futures Contract Expiry & Rollover Policy (`FuturesRolloverSpec`):**
   - Transitions between `contract_id`s are explicitly checked across the full outcome window.
   - If no documented `FuturesRolloverSpec` (additive Panama back-adjustment spread or multiplicative ratio) is provided, the engine emits `OutcomeDataQualityStatus.ROLLOVER_INVALID` and refuses to stitch prices silently across expiry boundaries.
3. **Corporate Actions & Halts:**
   - Applies cumulative `corporate_action_factor` multipliers and marks `CORPORATE_ACTION_ADJUSTED`.
   - Trading halts (`is_halted=True`) at window start or endpoint trigger `HALTED_TRADING` with `None` returns.
4. **Anchor Modes (`AnchorMode`):**
   - `PRE_EVENT_AS_OF`: Anchors $P_{\text{start}}$ at latest pre-event price $\le t_{\text{decision}}$ so after-hours disclosures capture the overnight open jump (`overnight_gap_included=True`).
   - `POST_DECISION_EXECUTABLE`: Anchors $P_{\text{start}}$ at first observable market price $\ge t_{\text{decision}}$ (e.g., next market open) for slippage-conscious execution simulation.
