# TickerWatch Alpha — Phase 5 Final Research Report (`PHASE_5_REPORT`)

**Phase:** Phase 5 — Predictive Modelling, Walk-Forward Validation & Alpha Attribution  
**Branch:** `phase/5-predictive-modeling` (branched cleanly from verified Phase 4 merge commit `66b9328`)  
**Author:** Agastya Varshney (`agastyavarshney@gmail.com`)  
**Date:** 2026-11-11

---

## 1. Software Implemented & Phase 4 Remediation Completed

Phase 5 delivered the full causal predictive modeling, uncertainty calibration, purged walk-forward, alpha attribution, cost-adjusted backtesting, and artifact governance stack on top of hardened Phase 4 infrastructure:

1. **Work Package A — Phase 4 Causal Remediation ([`docs/alpha/PHASE_5_PHASE_4_REMEDIATION.md`](file:///c:/Users/Agastya/Downloads/tickerwatch/docs/alpha/PHASE_5_PHASE_4_REMEDIATION.md)):**
   - Timezone-aware ingestion parser (`parse_source_timestamp_to_utc`) mapping naive NSE/BSE/RBI strings to `Asia/Kolkata` and SEC strings to DST-aware `America/New_York` (`17:30 ET` for date-only SEC strings).
   - Bitemporal SQLite store `v4.1.0` reconstructing exact pre-decision revisions (`get_event_as_of`, `query_events`), embedding parity `bge-small-en-v1.5-onnx-int8-cls512-v2` (`[CLS]` pooling, `max_length=512`, BGE instruction prefix, snapshot-pinned revisions), dual `REACTION` vs `EXECUTABLE` outcome keys, and pre-event OLS rolling beta (`beta_min_samples=10`, causal cutoff `<= start_ts`).
2. **Work Package B — Real-Data Readiness Audit ([`backend/alpha/data_readiness.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/data_readiness.py)):**
   - Programmatic auditor strictly segregating real vs synthetic counts across `tickerwatch.db`, `historical_events`, and `data/`.
3. **Work Packages C & D — Causal Features & Targets ([`backend/alpha/feature_registry.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/feature_registry.py), [`backend/alpha/predictive_features.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/predictive_features.py), [`backend/alpha/predictive_targets.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/predictive_targets.py)):**
   - Versioned 87-feature contract (`E=38`, `H=26`, `M=23`), `ModelConfiguration` A–E subsets, train-fold-only `FoldSafeFeatureTransformer`, explicit `NEUTRAL_NO_TRADE` economic cost hurdles, and off-session market state handling (`MarketSessionStatus.OPEN` verified in testing).
4. **Work Packages E & F — Models, Calibration & `NO_TRADE` Abstention ([`backend/alpha/predictive_models.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/predictive_models.py), [`backend/alpha/prediction_uncertainty.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/prediction_uncertainty.py)):**
   - Pure vectorized NumPy estimators (4 Baselines + Ridge, ElasticNet, Logistic w/ Armijo line search, Random Forest, GBDT + Quantile Pinball Heads, Quantile Linear IRLS, sample-gated Compact MLP `N>=100`).
   - Validation-only Platt / PAVA Isotonic probability scaling, split-conformal $[q_{10}, q_{90}]$ padding, calibration metrics, and 7-rule `NO_TRADE` abstention.
5. **Work Packages G, H & I — Purged Validation, Attribution, Backtester & Registry:**
   - [`backend/alpha/walk_forward.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/walk_forward.py) & [`backend/alpha/leakage_audit.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/leakage_audit.py): chronological expanding/rolling splits, horizon-scaled embargoing, outcome overlap + hash/revision purging, and adversarial `LeakageAuditor`.
   - [`backend/alpha/predictive_evaluation.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/predictive_evaluation.py) & [`backend/alpha/research_backtest.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/research_backtest.py): statistical evaluation, Newey-West HAC Diebold-Mariano + moving-block bootstrap 95% CIs + Holm-Bonferroni attribution, strict `EXECUTABLE`-only simulation, brokerage/SEBI/STT/spread/slippage/latency sweeps, and regime breakdowns.
   - [`backend/alpha/model_registry.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/model_registry.py) & [`backend/alpha/predictive_pipeline.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/predictive_pipeline.py): SQLite + JSON + SHA-256 tamper-verified artifact reproduction.

---

## 2. Model Training Performed

- **Real-Data Training:** **None performed.** Intentionally gated and blocked cleanly by `RealDataReadinessAuditor` (`overall_empirical_training_status = BLOCKED`).
- **Synthetic Training Performed (`is_synthetic = True`):** Executed via [`scripts/alpha_phase5_experiment.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/scripts/alpha_phase5_experiment.py) across two controlled cohorts (3 chronological purged folds each, validation-only Platt calibration):
  - **Suite A (`SYNTHETIC_SUITE_A_MULTIMODAL`):** `N=220` synthetic events (`165` pooled OOS evaluations) testing Baselines + Models `A` (`M`), `B` (`E`), `C` (`E+H`), `E` (`E+M`), `D` (`E+H+M`) across Ridge, GBDT, and Random Forest.
  - **Suite B (`SYNTHETIC_SUITE_B_ZERO_SIGNAL_NULL`):** `N=180` zero-signal i.i.d. noise events (`135` pooled OOS evaluations) verifying honest null rejection and negative net PnL under costs.

---

## 3. Evaluation on Real Data — Status: `BLOCKED`

Auditing `tickerwatch.db`, `data/alpha_historical_store.db`, and `data/` proved empirical training cannot proceed honestly yet:
- **India (`IN`): `BLOCKED`** — 4 general wrap-up articles only; **0** NSE/BSE corporate filings; **0** RBI/MOSPI macro bulletins; **0** market price bars.
- **US (`US`): `INSUFFICIENT_DATA`** — 3,038 SEC header records (`3,041/3,042` classify `ROUTINE_ADMINISTRATIVE`, `61%` Form 4 insider logs), `0` real market bars/quotes, `0` real matured labels, `0%` consensus estimates, and all `observed_at_utc` timestamps compressed into a `1.52s` batch backfill (`2026-11-08T09:34Z`).

**Genuine Out-of-Sample Empirical Evidence on Live Markets:** **None exists yet.** No claim of positive live market alpha is made or supported by currently ingested data.

---

## 4. Evaluation on Labeled Synthetic Data (`is_synthetic = True`)

Full tables appear in [`docs/alpha/PHASE_5_EXPERIMENT_RESULTS.md`](file:///c:/Users/Agastya/Downloads/tickerwatch/docs/alpha/PHASE_5_EXPERIMENT_RESULTS.md) and [`docs/alpha/benchmark_phase5_synthetic_results.json`](file:///c:/Users/Agastya/Downloads/tickerwatch/docs/alpha/benchmark_phase5_synthetic_results.json):
- **Suite A (Structured Synthetic):** `MODEL_D_FULL_RIDGE` achieved `RMSE = 0.007550`, `Spearman IC = +0.9172`, `ROC-AUC = 0.9523`, `Brier = 0.0898`, `80% Conformal Coverage = 0.885`, and `5/10` Holm-adjusted significant RMSE reductions over all 4 Baselines and `MODEL_A_MARKET_ONLY_RIDGE` (`RMSE = 0.020625`, `IC = -0.0389`, `p_Holm = 0.0000`).
- **Suite B (Pure Noise Null Check):** `MODEL_D_FULL_RIDGE` abstained on **72.6%** of events and yielded **0/8** Holm-significant gains (`p_Holm >= 0.268`), confirming zero false-positive discovery bias.

---

## 5. Cost-Adjusted Research Backtest Results (`EXECUTABLE` Labels Only)

- **Suite A (Synthetic):** With conservative IN round-trip costs (`~9.1–11.5 bps` incl. spread) deducted on every trade and `NO_TRADE` abstentions respected:
  - `MODEL_D_FULL_RIDGE`: `134` trades / `18.8%` abstention, `+158.19 bps` mean net per trade (`Sharpe = +15.92`, degrading to `+14.58` at 2x cost + 8 bps latency stress).
  - `MODEL_A_MARKET_ONLY_RIDGE`: `69` trades / `58.2%` abstention, `-7.35 bps` mean net (`Sharpe = -0.41`).
- **Suite B (Zero-Signal Noise, Synthetic):**
  - `MODEL_D_FULL_RIDGE`: `37` trades / `72.6%` abstention, `-8.58 bps` mean net (`-3.28%` cumulative, `Sharpe = -0.78`, `Stress 2x Sharpe = -2.25`).

---

## 6. Verification Scope & Missing Data Dependencies

- **Offline Synchronous Verification:** Verified cleanly across Phase 4 remediated tests, Phase 5 WP-B through WP-J tests (including all 19 structural, leakage, calibration, backtest, and reproduction gates), and non-async core unit suites (exact final test totals run after WP-K commit).
- **Async Test Constraint in Windows Terminal Sandbox:** Asynchronous `pytest-asyncio` tests (e.g. `tests/alpha/test_alpha_phase2.py`, `test_api.py`, `test_resilience.py`, `test_rss_connector.py`, `test_sec_connector.py`) block inside Python's loopback `socketpair` initialization under OS sandbox network isolation and must run outside the sandbox to verify asyncio loops.
- **Unresolved Data Blockers Before Phase 6:**
  1. Historical 1-minute OHLCV / L1 quote + corporate action & futures rollover archives for `NIFTY_FUT` / NSE equities and US equities.
  2. Multi-year primary exchange corporate announcements (`>= 5,000` NSE/BSE disclosures with true wire arrival timestamps) and full-body SEC 8-K parsing.
  3. Point-in-time historical analyst consensus database (`as_of <= decision_timestamp`).
