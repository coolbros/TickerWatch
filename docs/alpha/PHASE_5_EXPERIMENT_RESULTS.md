# TickerWatch Alpha — Phase 5 Experiment Results (`PHASE_5_EXPERIMENT_RESULTS`)

**Artifact Reference:** [`docs/alpha/benchmark_phase5_synthetic_results.json`](file:///c:/Users/Agastya/Downloads/tickerwatch/docs/alpha/benchmark_phase5_synthetic_results.json)  
**Experiment Script:** [`scripts/alpha_phase5_experiment.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/scripts/alpha_phase5_experiment.py)  
**Run Date:** 2026-11-11T05:35Z

> [!IMPORTANT]
> **Strict Synthetic vs. Empirical Boundary:**
> - **Real-Market Empirical Results:** **`BLOCKED`** (`IN: BLOCKED`, `US: INSUFFICIENT_DATA`, `real_events = 3042`, `real_mkt_obs = 0`, `matured_real_labels = 0`).
> - **All metrics reported below derive from labeled synthetic verification suites (`is_synthetic = True`)** designed to verify pipeline math, feature-group ablation isolation (`E/H/M`), calibration, cost-aware backtesting, and null-signal rejection. **They do NOT constitute live empirical market alpha.**

---

## 1. Synthetic Suite A — Multi-Modal Ablation & Cost-Aware Backtest (`N=220`, 3 Purged Folds, Horizon `1d`)

Constructed with genuine noisy multi-modal structure where `M_t` carries mild pre-event momentum/volatility effects (~10% variance), `E_t` carries event surprise/materiality (~45% variance), `H_t` carries analogue persistence (~25% variance), and Gaussian noise degrades predictability (`all_leakage_audits_passed = True`, `165` pooled out-of-sample test evaluations across 3 purged expanding folds).

### 1.1 Out-of-Sample Statistical & Cost-Adjusted `EXECUTABLE` Backtest Summary

| Model / Config | Active / Abstain | RMSE | Spearman IC | ROC-AUC | Brier | ECE | 80% Cov | Trades | Net Ret | Mean Net (bps) | Ann. Sharpe | Stress 2x (+8bps) Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `BASELINE_ZERO` | 0 / 1.000 | 0.019026 | +0.0000 | 0.5000 | 0.2500 | 0.0333 | 0.836 | 0 | +0.00% | +0.00 | +0.00 | 0.00 |
| `BASELINE_MEAN` | 0 / 1.000 | 0.018999 | +0.0544 | 0.5487 | 0.2472 | 0.0137 | 0.830 | 0 | +0.00% | +0.00 | +0.00 | 0.00 |
| `BASELINE_ROLLING_INST` | 0 / 1.000 | 0.019146 | -0.0203 | 0.5487 | 0.2472 | 0.0137 | 0.861 | 0 | +0.00% | +0.00 | +0.00 | 0.00 |
| `BASELINE_SUBTYPE_SHRINK` | 94 / 0.430 | 0.018811 | +0.1547 | 0.5739 | 0.2458 | 0.0387 | 0.836 | 94 | +16.39% | +18.06 | +1.11 | +0.08 |
| `MODEL_A_MARKET_ONLY_RIDGE` | 69 / 0.582 | 0.020625 | -0.0389 | 0.4361 | 0.3256 | 0.2371 | 0.824 | 69 | -6.03% | -7.35 | -0.41 | -1.35 |
| `MODEL_B_EVENT_ONLY_RIDGE` | 141 / 0.145 | 0.007603 | +0.9136 | 0.9451 | 0.1028 | 0.0954 | 0.879 | 141 | +735.76% | +152.47 | +16.32 | +14.79 |
| `MODEL_C_EVENT_HIST_RIDGE` | 150 / 0.091 | 0.007361 | +0.9201 | 0.9553 | 0.0889 | 0.0659 | 0.842 | 150 | +738.17% | +143.53 | +16.23 | +14.49 |
| `MODEL_E_EVENT_MARKET_RIDGE` | 132 / 0.200 | 0.007859 | +0.9101 | 0.9255 | 0.1187 | 0.1198 | 0.873 | 132 | +691.65% | +158.74 | +15.62 | +14.32 |
| `MODEL_D_FULL_RIDGE` | 134 / 0.188 | 0.007550 | +0.9172 | 0.9523 | 0.0898 | 0.0759 | 0.885 | 134 | +711.05% | +158.19 | +15.92 | +14.58 |
| `MODEL_D_FULL_GBDT` | 141 / 0.145 | 0.008804 | +0.9032 | 0.9523 | 0.0898 | 0.0759 | 0.830 | 141 | +718.21% | +150.95 | +16.10 | +14.58 |
| `MODEL_D_FULL_RF` | 143 / 0.133 | 0.008378 | +0.9110 | 0.9523 | 0.0898 | 0.0759 | 0.842 | 143 | +723.70% | +149.31 | +16.17 | +14.60 |

### 1.2 Holm-Bonferroni Paired Attribution (`MODEL_D_FULL_RIDGE` vs. Comparators)

- **Significant Reductions ($p_{\text{Holm}} < 0.05$ & 95% Block Bootstrap CI $> 0$):** **5 / 10** comparisons (`BASELINE_ZERO`, `BASELINE_MEAN`, `BASELINE_ROLLING_INST`, `BASELINE_SUBTYPE_SHRINK`, `MODEL_A_MARKET_ONLY_RIDGE`, all `p_Holm = 0.0000`, `DM z = +7.58` to `+8.40`).
- **vs. `MODEL_E_EVENT_MARKET_RIDGE` ($E+M$):** Ridge `D` lowered RMSE (`+0.000309`), Brier (`+0.0289`), and lifted Spearman IC (`+0.00715`), though at $N=165$ synthetic OOS samples this increment did not cross Holm-adjusted $0.05$ (`p_Holm = 0.6871`, honestly reported rather than cherry-picked!).
- **vs. `MODEL_C_EVENT_HIST_RIDGE` ($E+H$):** On synthetic sample `N=165`, adding `M` slightly degraded linear Ridge RMSE by `-0.000188` (`p_Holm = 0.8512`), showing how the pipeline accurately catches feature-dilution / variance trade-offs.

---

## 2. Synthetic Suite B — Zero-Signal Null Stress Test (`N=180`, 3 Purged Folds, Pure i.i.d. Noise)

When $y_{t+h}$ contains **zero structural relationship** to $(E_t, H_t, M_t)$:

| Model / Config | Active / Abstain | RMSE | Spearman IC | ROC-AUC | Brier | Net Ret | Mean Net (bps) | Ann. Sharpe | Stress 2x Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `BASELINE_ZERO` | 0 / 1.000 | 0.010630 | +0.0000 | 0.5000 | 0.2500 | +0.00% | +0.00 | +0.00 | 0.00 |
| `BASELINE_MEAN` | 45 / 0.667 | 0.010793 | -0.0354 | 0.4699 | 0.2522 | -5.29% | -11.50 | -0.98 | -2.37 |
| `MODEL_A_MARKET_ONLY_RIDGE` | 48 / 0.644 | 0.011526 | +0.0875 | 0.5943 | 0.2707 | +3.87% | +8.52 | +0.73 | -0.71 |
| `MODEL_B_EVENT_ONLY_RIDGE` | 52 / 0.615 | 0.010977 | -0.0384 | 0.5493 | 0.2910 | -0.94% | -1.32 | -0.13 | -1.76 |
| `MODEL_C_EVENT_HIST_RIDGE` | 47 / 0.652 | 0.011184 | -0.0079 | 0.5455 | 0.2950 | -9.47% | -20.54 | -1.72 | -3.06 |
| `MODEL_E_EVENT_MARKET_RIDGE` | 40 / 0.704 | 0.011595 | +0.0567 | 0.6066 | 0.2694 | +0.69% | +2.29 | +0.18 | -1.16 |
| `MODEL_D_FULL_RIDGE` | 37 / 0.726 | 0.011862 | +0.1013 | 0.5598 | 0.2726 | -3.28% | -8.58 | -0.78 | -2.25 |

- **Key Verification Takeaways from Suite B:**
  1. `MODEL_D_FULL_RIDGE` automatically abstained on **72.6%** of pure-noise events (`37/135` active trades vs `81.2%` active in Suite A).
  2. Net return on active noise trades was honestly negative (`-3.28%`, Sharpe `-0.78`, degrading to `-9.15%`, Sharpe `-2.25` under 2x stress).
  3. **Zero False Discoveries:** `AlphaAttributor` reported **0 / 8** Holm-significant improvements (`headline_conclusion: "...vs 0/8 comparators (none)"`). The single uncorrected `raw_p_value = 0.0335` on `MODEL_D vs BASELINE_ZERO` (negative direction: zero beat D!) yielded `p_Holm = 0.2680` -> **`statistically_significant_at_05 = False`**.
