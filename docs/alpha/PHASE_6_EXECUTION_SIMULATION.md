# Phase 6 Shadow Execution Simulator Design (`PHASE_6_EXECUTION_SIMULATION`)

**Module:** [`backend/alpha/shadow_execution.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/shadow_execution.py)  
**Order-Placement Constraint:** Strictly paper-only (`NO` broker order routing or live capital interfaces).

---

## 1. Realistic Fill & Friction Architecture

`ShadowExecutionSimulator` isolates execution modeling inside pluggable `ShadowExecutionConfig` profiles so alternative market-microstructure assumptions can be swept against identical shadow predictions:

1. **Latency Arrival Delay:** Advances fill evaluation from `decision_timestamp_utc` by `decision_latency_ms + order_submission_latency_ms` (plus next-session open jump if triggered outside active NSE IST hours).
2. **Depth-Aware Liquidity & Partial Fills:**
   - **L2 Book Depth:** Sweeps `depth_asks` / `depth_bids` levels (`_sweep_l2_depth_price`) up to `max_book_participation` (`35%` default) to capture multi-level depth impact (`estimated_market_impact_bps`) and realistic partial-fill quantities.
   - **L1 BBO Quote:** Enters on opposing ask/bid and exits across spread, capped by top-of-book participation.
   - **OHLCV Conservative Fallback:** Applies explicit adverse half-spread + market impact penalties when tick quotes are absent (`FillQualitySource.OHLCV_CONSERVATIVE_FALLBACK`).
3. **Anti-Optimistic Limit Fill Rule:** Limit orders are **never** credited as full fills merely because market prices touched the limit price (`LIMIT_TOUCHED_ONLY_NO_TRADE_THROUGH` when `require_trade_through_for_full_limit_fill=True`, or configurable conservative partial `touch_only_fill_fraction`).
4. **Session, Rollover & Statutory Costs:** Caps intraday exits `exit_before_close_minutes` before close, checks contract halt/suspension/pre-rollover flags, and deducts brokerage, SEBI/exchange clearing fees, STT, stamp duty, and GST via `CostAssumptionSpec`.
5. **Separated Return Accounting:** Every simulated trade exposes three distinct columns: `gross_theoretical_return`, `estimated_executable_return`, and `net_return_after_costs`.
