# Phase 6 Real-World Shadow Validation Protocol (`PHASE_6_VALIDATION_PROTOCOL`)

**Module:** [`backend/alpha/shadow_validation.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/shadow_validation.py)

---

## 1. Strict Model Eligibility Rules (`ModelEligibilityAuditor`)

A registered model is marked **`MODEL_ELIGIBLE`** for live shadow signaling **only** if ALL of the following pass:
1. Trained strictly on non-synthetic market data (`is_synthetic == False`). Synthetic-trained bundles remain quarantined as `SYNTHETIC_DIAGNOSTIC_ONLY`.
2. Training cutoff UTC, dataset manifest SHA-256, and validation period are non-empty.
3. Chronological out-of-sample metrics exist cleanly (`oos_metrics`).
4. Feature schema version (`5.0.0-alpha`) and `feature_manifest_sha256` match frozen pipeline contract.
5. Artifact SHA-256 passes `LocalModelRegistry.verify_artifact_integrity()`.
6. `leakage_audit_passed == True` and `explicit_human_approval == True`.

Even when no eligible predictive model exists (`NO_ELIGIBLE_MODEL`), live events, $E_t/H_t/M_t$ feature snapshots, and matured post-event market outcomes continue persisting immutably into `ShadowSignalLedger`.

---

## 2. Observation Window & Honest Non-Completion Discipline

- **Minimum Collection Window:** **20** real NSE/US trading sessions (`minimum_session_window_target = 20`), with **30** sessions preferred (`preferred_session_window_target = 30`).
- **Explicit Statistical Disclaimer:** Completing 20 or 30 calendar market sessions is an empirical **collection protocol threshold**, NOT proof of statistical significance. Rare corporate events require independent Holm-Bonferroni attribution testing once sufficient unique events accumulate.
- **Six-Axis Stratification:** `ShadowValidationEvaluator` partitions matured results by `instrument`, `event_subtype`, `horizon`, `market_regime`, `confidence_bucket`, and IST session `time_of_day`.
