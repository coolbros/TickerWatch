# Phase 5 Forecast Target & Label Specification (`PHASE_5_TARGET_SPECIFICATION`)

**Document Version:** `5.0.0-alpha`  
**Date:** 2026-11-11  
**Modules:** `backend/alpha/predictive_targets.py`, `backend/alpha/outcomes.py`, `backend/alpha/event_study.py`

---

## 1. Dual Label Families (`REACTION` vs. `EXECUTABLE`)

Phase 5 enforces strict separation between measuring total market repricing and modeling achievable post-receipt returns:

1. **`REACTION` (`AnchorMode.PRE_EVENT_AS_OF`):**
   - Entry anchors to the last valid pre-decision quote ($\le \tau_{\text{decision}}$).
   - Quantifies total economic repricing from event onset (including overnight gaps for out-of-hours disclosures).
   - **Prohibited** from direct P&L claims.
2. **`EXECUTABLE` (`AnchorMode.POST_DECISION_EXECUTABLE`):**
   - Entry anchors strictly to the first regular-session market quote at or after $\tau_{\text{decision}} + \Delta_{\text{pipeline\_latency}}$.
   - Incorporates bid/ask spread crossing and next-session open execution for after-hours news.
   - Forms the sole valid label basis for economic backtesting (`research_backtest.py`).

---

## 2. Multi-View Targets & Economic Neutral Band (`NEUTRAL_NO_TRADE`)

Over horizons $h \in \{1\text{m}, 5\text{m}, 30\text{m}, 1\text{h}, 1\text{d}, 5\text{d}\}$ (gated via `check_horizon_trainability` against source resolution and minimum sample thresholds), each `PredictiveTargetRecord` provides:

- **Continuous Return (`y_continuous`):** Forward simple return (or strictly pre-event beta / market-adjusted return when configured).
- **3-Class Economic Action Label (`y_direction_3class`):**
  Let $H_{\text{cost}} = \frac{\text{brokerage} + \text{exchange/SEBI} + \text{STT/stamp} + \text{slippage} + \text{spread} + \text{buffer}}{10000}$:
  - `+1` (`LONG_POSITIVE`): $r > H_{\text{cost}}$
  - `-1` (`SHORT_NEGATIVE`): $r < -H_{\text{cost}}$
  - `0` (`NEUTRAL_NO_TRADE`): $|r| \le H_{\text{cost}}$ (economically non-viable after frictions)
- **Binary Sub-Targets:** `y_positive_material`, `y_negative_material`, `y_material_move` ($|r| > H_{\text{cost}}$), plus unthresholded `y_positive_raw`.

Conservative defaults are defined for NIFTY Futures (`~7.1 bps` + spread + `1.5 bps` buffer), NSE Equities (`~10.5 bps` + spread + `2.0 bps` buffer), and US Equities (`~3.6 bps` + spread + `1.5 bps` buffer).
