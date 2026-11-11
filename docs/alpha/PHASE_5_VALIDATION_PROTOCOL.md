# Phase 5 Purged Walk-Forward Validation Protocol (`PHASE_5_VALIDATION_PROTOCOL`)

**Document Version:** `5.0.0-alpha`  
**Date:** 2026-11-11  
**Modules:** `backend/alpha/walk_forward.py`, `backend/alpha/leakage_audit.py`

---

## 1. Chronological Split Structure (No Random Cross-Validation)

Random cross-validation is strictly prohibited on time-indexed financial disclosures. `PurgedWalkForwardSplitter` enforces strictly forward expanding or rolling folds:

$$\text{Fold } k:\; \text{Train } [t_0, t_k^{\text{tr}}] \;\xrightarrow{\text{Purge + Embargo}}\; \text{Validation } (t_k^{\text{val}}) \;\xrightarrow{\text{Purge + Embargo}}\; \text{Out-of-Sample Test } (t_{k+1})$$

---

## 2. Leakage Prevention Controls

1. **Label-Window Overlap Purging:** Every prior observation whose `exit_timestamp` exceeds a downstream validation/test start is removed prior to fitting.
2. **Post-Boundary Embargo:** Requires $\tau_{\text{eval\_start}} - \tau_{\text{prior\_end}} \ge \delta_{\text{embargo}} \ge \max(\text{horizon})$, rejecting shorter embargo durations cleanly.
3. **Duplicate, Story & Revision Isolation:** Identical `content_hash` values and same-`event_id` revisions across boundaries are purged automatically (`LeakageAuditor.audit_split`).
4. **Issuer Reporting-Cluster Purge:** Optional `issuer_cycle_purge_seconds` removes adjacent announcements for the same `primary_issuer_id` straddling fold cuts.
5. **Fold-Only Scaling & Calibration:** Imputation/Z-scaling (`FoldSafeFeatureTransformer`) fits strictly on `train_indices`; probability/conformal calibration (`UncertaintyCalibratorAndGate`) fits strictly on `val_indices`; OOS scoring executes once on untouched `test_indices`.
6. **Empirical Synthetic Ban (`allow_synthetic=False`):** Empirical runs raise immediately if `is_synthetic=True` records appear in any fold.
