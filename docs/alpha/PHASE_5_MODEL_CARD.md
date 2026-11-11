# TickerWatch Alpha — Phase 5 Model Card (`PHASE_5_MODEL_CARD`)

**Model Family:** TickerWatch Alpha Event-Conditioned Return & Direction Models (`v5.0.0-alpha`)  
**Author:** Agastya Varshney (`agastyavarshney@gmail.com`)  
**Empirical Production Eligibility:** **BLOCKED** — Verified solely on synthetic & simulated cohorts (`is_synthetic=True`); zero empirical market alpha verified to date.

---

## 1. Intended Research Objective & Hypothesis

Test the event-conditioned predictive distribution:

$$P(\Delta F_{t+h} \mid E_t, H_t, M_t)$$

across five strict ablation configurations trained and evaluated on **identical purged chronological walk-forward folds**:

| Identifier | Active Feature Groups | Feature Count | Role |
|---|---|---:|---|
| **Baselines** | Unconditional / Rolling Instrument / Event Subtype Shrinkage | N/A | Non-learned empirical & conditional anchors |
| **Model A** | `M_t` only (microstructure & session regime) | 23 | Tests whether price/book regime alone predicts event drift |
| **Model B** | `E_t` only (Phase 3 NLP classification, entities, surprise) | 38 | Isolates contemporaneous event semantics |
| **Model C** | `E_t + H_t` (NLP + Phase 4 retrieved historical analogues) | 64 | Tests joint event + historical context |
| **Model E** | `E_t + M_t` (NLP + market microstructure, without `H_t`) | 61 | Isolates incremental contribution of `H_t` when compared against `D` |
| **Model D** | `E_t + H_t + M_t` (Full TickerWatch Alpha System) | 87 | Complete multi-modal event + analogue + regime hypothesis |

Canonical contract enforced by `backend/alpha/feature_registry.py` (`FEATURE_SCHEMA_VERSION = "5.0.0-alpha"`).

---

## 2. Supported Estimators & Inductive Bias

Implemented in pure vectorized NumPy (`backend/alpha/predictive_models.py`) with zero external heavy ML dependencies and strict train-fold-only imputation/clipping (`FoldSafeFeatureTransformer`, `clip_z = 8.0`):

- **Baselines:** `ZERO_RETURN`, `UNCONDITIONAL_MEAN`, `ROLLING_INSTRUMENT` (Bayesian shrunk instrument mean), `EVENT_SUBTYPE_SHRINKAGE`.
- **Linear / Generalized Linear Models:** `RIDGE` (analytical closed-form L2), `ELASTIC_NET` (proximal coordinate descent), `LOGISTIC_DIRECTION` (L2 logistic regression with Armijo backtracking line search), `QUANTILE_LINEAR` (IRLS weighted quantile regression across $q_{10}, q_{25}, q_{50}, q_{75}, q_{90}$).
- **Non-Linear Tree Ensembles:** `RANDOM_FOREST` (bagged CART trees with leaf sample quantiles), `GBDT` (boosted shallow residual trees + separate $q_{10}, q_{50}, q_{90}$ pinball pseudo-residual heads).
- **Neural Baseline Gate:** `COMPACT_MLP` (single hidden ReLU layer, strictly sample-gated at $N_{\text{train}} \ge 100$ to curb small-sample overfitting).

---

## 3. Target Separation, Calibration & Explicit `NO_TRADE` Policy

- **Target Separation (`backend/alpha/predictive_targets.py`):**
  - Strictly distinguishes `OutcomeLabelType.REACTION` (research price impact anchored prior to decision) from `OutcomeLabelType.EXECUTABLE` (post-decision + pipeline latency + active-session spread crossing).
  - Cost hurdles incorporate conservative frictions (`9.1 bps` NIFTY Futures / `12.5 bps` NSE Equity / `5.1 bps` US Equity all-in) producing `ActionableDirectionClass.NEUTRAL_NO_TRADE` (`0`) so negligible fluctuations never pollute directional classes.
  - `ResearchBacktester` programmatically **rejects** any non-`EXECUTABLE` target.
- **Validation-Only Calibration (`backend/alpha/prediction_uncertainty.py`):**
  - Fits Platt logistic scaling or PAVA Isotonic regression + split-conformal $[q_{10}, q_{90}]$ padding strictly on held-out validation slices (`val_indices`), never on training or test folds.
- **7 Mandatory `NO_TRADE` Abstention Rules:** Forces `action_state = "NO_TRADE"` and zero position/fees when any of the following triggers:
  1. `LOW_CLASSIFICATION_CONFIDENCE`
  2. `AMBIGUOUS_ENTITY_RESOLUTION`
  3. `INSUFFICIENT_HISTORICAL_EVIDENCE`
  4. `UNAVAILABLE_OR_STALE_MARKET`
  5. `HIGH_PREDICTIVE_UNCERTAINTY`
  6. `EXPECTED_RETURN_BELOW_COSTS`
  7. `SUB_THRESHOLD_DIRECTIONAL_PROB`

---

## 4. Known Failure Modes & Deployment Prohibitions

1. **Zero Empirical Validation on Real Markets:** Current repository data yields `ReadinessStatus.BLOCKED` (see `PHASE_5_DATA_LIMITATIONS.md`). No real-world out-of-sample alpha has been observed.
2. **NO Live Capital / Automated Execution:** Live broker routing, portfolio margin sizing, and automated trading remain strictly prohibited until empirical real-data readiness reaches `READY`, Phase 5 Gate falsification tests clear on real out-of-sample markets, and Phase 6 execution simulations pass.
3. **Horizon Resolution Constraint:** Training automatically blocks intraday horizons (`1m`, `5m`, `30m`, `1h`) whenever underlying bars have insufficient temporal resolution (`OutcomeDataQualityStatus.INSUFFICIENT_RESOLUTION`).
