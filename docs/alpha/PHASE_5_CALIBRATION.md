# Phase 5 Probabilistic Forecasting, Uncertainty Calibration & Abstention (`PHASE_5_CALIBRATION`)

**Document Version:** `5.0.0-alpha`  
**Date:** 2026-11-11  
**Module:** `backend/alpha/prediction_uncertainty.py`

---

## 1. Probability Calibration Protocol

TickerWatch Alpha strictly prohibits treating heuristic Phase 3 NLP `composite_confidence` scores as calibrated probabilities of predictive return correctness. All probabilistic heads (`p_positive`, `p_negative`, `p_material_move`) and conformal quantile intervals (`[q10, q90]`) are calibrated exclusively on held-out **validation folds** (`fit_calibration`):

- **Platt Scaling (`_PlattScaler`):** L2-regularized 1D logistic transform $\sigma(a \cdot \text{logit}(p) + b)$ with monotonicity constraint $a \ge 0.05$.
- **Isotonic Regression (`_IsotonicPAVA`):** Monotonic Pool Adjacent Violators Algorithm for non-parametric probability mapping when validation size allows.
- **Conformal Quantile Padding:** Computes held-out validation nonconformity quantiles over `max(q10 - y, y - q90)` to guarantee valid empirical coverage (`80%` nominal `[q10, q90]` interval).

---

## 2. Explicit Calibration Diagnostics

Evaluated per horizon via `compute_calibration_metrics()`:
1. **Brier Score** and **Binary Log Loss**
2. **Expected Calibration Error (ECE)** and decile **Reliability Diagrams**
3. **Quantile Pinball Loss** at $\tau \in \{0.10, 0.25, 0.50, 0.75, 0.90\}$
4. **Empirical Interval Coverage** (`80%` and `50%` central prediction bands)

---

## 3. Selective Trading & Explicit Abstention (`NO_TRADE`)

`UncertaintyCalibratorAndGate.calibrate_and_gate()` forces `abstain = True` (`action_state = "NO_TRADE"`) whenever any risk/evidence threshold fails:
1. `LOW_CLASSIFICATION_CONFIDENCE` (`e_cls_confidence < 0.50`)
2. `AMBIGUOUS_ENTITY_RESOLUTION` (`e_entity_resolved == 0` or `e_entity_confidence < 0.50`)
3. `INSUFFICIENT_HISTORICAL_EVIDENCE` (`h_insufficient_evidence == 1` when config uses Group `H`)
4. `UNAVAILABLE_OR_STALE_MARKET` (`m_available == 0` or `m_is_stale == 1` when config uses Group `M`)
5. `HIGH_PREDICTIVE_UNCERTAINTY` (`predictive_std > max_predictive_std`)
6. `EXPECTED_RETURN_BELOW_COSTS` (`|E[r]| * 10000 - round_trip_cost_bps < min_edge_over_cost_bps`)
7. `SUB_THRESHOLD_DIRECTIONAL_PROB` (`calibrated P(dir) < min_directional_prob`)
