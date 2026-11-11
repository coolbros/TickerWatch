# TickerWatch Alpha — Phase 5 Incremental Alpha Attribution & Research Backtest Design

> **Scope:** Methodology for formal ablation testing ($E_t$, $H_t$, $M_t$) and cost-adjusted historical signal evaluation (`backend/alpha/predictive_evaluation.py` and `backend/alpha/research_backtest.py`).
>
> **Empirical Status Note:** Live empirical testing on real NSE/BSE and US cash equities remains `BLOCKED` until non-trivial event history and market price/quote archives are ingested (see `docs/alpha/PHASE_5_DATA_READINESS.md`). All attribution software components are independently verified under synthetic ground-truth suites and zero-signal null checks.

---

## 1. Research Hypothesis Under Test

$$P(\Delta F_{t+h} \mid E_t, H_t, M_t)$$

To determine whether event semantics ($E_t$) and persistent vector retrieval analogues ($H_t$) provide genuine out-of-sample predictive lift beyond contemporaneous price/microstructure features ($M_t$), every experiment compares all five strict feature configurations on **identical purged chronological walk-forward folds**:

| Model Config | Feature Groups | Role in Ablation |
|---|---|---|
| **Baselines** | Unconditional / Rolling Instrument / Subtype Shrinkage | Zero-skill & trivial conditional anchors |
| **Model A** | `M_t` only (23 features) | Pure market microstructure & regime baseline |
| **Model B** | `E_t` only (38 features) | Pure Phase 3 NLP event representation |
| **Model C** | `E_t + H_t` (64 features) | NLP + Phase 4 historical analogues without microstructure |
| **Model D** | `E_t + H_t + M_t` (87 features) | Complete Alpha multi-stream research hypothesis |
| **Model E** | `E_t + M_t` (61 features) | Event + market without historical vector retrieval |

---

## 2. Out-of-Sample Statistical Metrics

`compute_predictive_metrics` evaluates every model at each horizon independently per fold (`CrossFoldStabilitySummary`) and pooled OOS:

- **Regression:** MAE, RMSE, Pearson $r$, tie-aware Spearman Information Coefficient (IC).
- **Direction Classification:** Positive base rate, balanced accuracy, precision, recall, F1, exact Mann-Whitney U ROC-AUC, Average Precision PR-AUC, Brier score, clipped log-loss.
- **Uncertainty & Calibration:** 10-bin Expected Calibration Error (ECE), full reliability diagram bins, Pinball loss at $q_{10}, q_{50}, q_{90}$, and empirical interval coverage (50% and 80% target intervals).

---

## 3. Paired Dependence Testing & Multiple-Testing Control

1. **Paired Loss Differential Series:** For each observation $i$, $d_i = (y_i - \hat{y}_{i,\text{comp}})^2 - (y_i - \hat{y}_{i,\text{cand}})^2$. Positive $\bar{d}$ indicates the candidate achieved lower MSE.
2. **Newey-West HAC Diebold-Mariano Test:** Uses Bartlett kernel HAC variance estimator (`lag = round(N^(1/3))`) so overlapping or serially clustered market events do not inflate $z$-statistics.
3. **Moving-Block Bootstrap 95% CIs:** Circular block bootstrap (`block_length=5`, `n_bootstrap=1000`, seeded RNG) preserves temporal cluster dependence.
4. **Holm-Bonferroni FWER Adjustment:** Controls family-wise error rate across simultaneous comparisons vs Baselines and Models A/B/C/E. A pairwise lift is deemed statistically meaningful only when BOTH `holm_adjusted_p_value < 0.05` AND the moving-block bootstrap 95% CI lower bound exceeds zero.

---

## 4. Strict Separation in Cost-Adjusted Research Backtesting

`ResearchBacktester` enforces four structural safeguards:

1. **`EXECUTABLE` Labels Only:** Any attempt to pass `OutcomeLabelType.REACTION` raises `ValueError` immediately.
2. **Strict `NO_TRADE` Honoring:** Observations flagged `abstain=True` or `signal_direction=0` take zero position and incur zero fees, feeding explicitly into `abstention_rate` and `exposure_fraction`.
3. **Full Friction Accounting:** Every simulated trade deducts brokerage, exchange/SEBI clearing fees, STT/stamp duty/GST, observed or conservative bid-ask spread, and latency/market-impact slippage. Gross metrics are **never** reported without accompanying net metrics.
4. **Regime & Sensitivity Breakdowns:** Performance is decomposed across event family, instrument, volatility regime, session time-of-day (`FIRST_30M`, `MID_SESSION`, `LAST_30M`, `OFF_HOURS_NEXT_OPEN`), and calibrated probability buckets, alongside `1.0x`, `1.5x (+3 bps)`, and `2.0x (+8 bps)` cost stress scenarios.
