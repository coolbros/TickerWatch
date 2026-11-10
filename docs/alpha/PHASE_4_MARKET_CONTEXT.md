# TickerWatch Alpha — Phase 4 Point-in-Time Market Regime Context (`PHASE_4_MARKET_CONTEXT`)

> **Phase:** Phase 4 — Work Package G  
> **Status:** IMPLEMENTED  
> **Primary Module:** `backend/alpha/market_context.py`  

---

## 1. Objective & Causal Architecture

`MarketRegimeContextBuilder` bridges Phase 2 market-data streaming/alignment primitives (`MarketEvent`, `TemporalAlignmentEngine`, `get_market_session_status`) with the Phase 4 historical intelligence pipeline while keeping market microstructure state **strictly separate** from NLP representations (`FinancialEventRepresentation`) so downstream Phase 5 experiments can cleanly ablate $\{E_t\}$, $\{E_t, H_t\}$, $\{E_t, M_t\}$, and $\{E_t, H_t, M_t\}$.

Every feature obeys the hard causal invariant:
$$\forall m \in \text{Stream}: m.\text{local\_received\_at\_utc} \le \tau_{\text{decision}}$$

Future observations arriving strictly after $\tau_{\text{decision}}$ are filtered out and counted in `excluded_future_events_count` for auditability.

---

## 2. Extracted Point-in-Time Features ($M_t$)

### Microstructure & Liquidity State
- `bid_ask_spread`, `bid_ask_spread_bps`
- `top_of_book_bid_qty`, `top_of_book_ask_qty`, `top_of_book_total_depth`
- `l2_total_bid_depth`, `l2_total_ask_depth` (when Level-2 depth levels are present)
- `depth_imbalance` $\in [-1, +1]$: $\frac{Q_{\text{bid}} - Q_{\text{ask}}}{Q_{\text{bid}} + Q_{\text{ask}}}$
- `microprice` and `microprice_offset_bps`: deviation of size-weighted microprice from arithmetic mid-price

### Pre-Event Momentum & Volatility
- `recent_return_1m`, `recent_return_5m`, `recent_return_30m`
- `benchmark_recent_return_30m`
- `realized_volatility_bps`: realized quadratic variation in basis points over lookback `[t - 30m, t]`

### Regime & Feed Health Telemetry
- `feed_freshness_ms`, `is_stale`, `session_state` (`OPEN`, `PRE_OPEN`, `CLOSED`, `HALTED`)
- `market_direction`: `BULLISH` ($\ge +15\text{ bps}$), `BEARISH` ($\le -15\text{ bps}$), `NEUTRAL`, or `UNAVAILABLE`
- `volatility_regime`: `LOW` ($< 25\text{ bps}$), `NORMAL` ($[25, 75]\text{ bps}$), `ELEVATED` ($> 75\text{ bps}$), or `UNAVAILABLE`
- `liquidity_regime`: `HIGH` (tight spread $\le 5\text{ bps}$ & healthy depth), `NORMAL`, `THIN` (stale, wide spread $> 20\text{ bps}$, or depth $< 50$), `CLOSED_OR_HALTED`, or `UNAVAILABLE`
