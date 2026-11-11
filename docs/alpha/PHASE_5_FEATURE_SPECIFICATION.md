# Phase 5 Point-in-Time Feature Specification (`PHASE_5_FEATURE_SPECIFICATION`)

**Document Version:** `5.0.0-alpha`  
**Date:** 2026-11-11  
**Modules:** `backend/alpha/feature_registry.py`, `backend/alpha/predictive_features.py`

---

## 1. Feature Architecture Overview

Every prediction row is constructed strictly from information observable at or before the operative news receipt timestamp ($\tau_{\text{decision}} = \text{local\_received\_at\_utc}$), organized into three orthogonal intelligence streams supporting ablation Models A–E:

| Group | Intelligence Source | Feature Count | Active In Configurations |
|---|---|---:|---|
| **E** | Phase 3 Financial NLP (`FinancialEventRepresentation`) | 38 | Model B, C, D, E |
| **H** | Phase 4 Persistent Historical Intelligence (`HistoricalIntelligenceRepresentation`) | 26 | Model C, D |
| **M** | Phase 2 & 4 Market State (`MarketRegimeContext` + Session/Basis) | 23 | Model A, D, E |
| **Total** | Full Multi-Modal System (`MODEL_D_FULL_SYSTEM`) | **87** | Model D |

---

## 2. Causality, Missingness & Fold-Safety Rules

1. **Legacy Context Prohibited (WP-A `A6`):** `PointInTimeFeatureBuilder.build_row()` calls `assert_valid_phase4_training_input(hist_rep)`, rejecting legacy Phase 3 in-memory `EventRetrievalContext` with `TypeError` and auditing `contains_query_future_labels is False`.
2. **Missing Consensus vs. Zero Surprise:** Absent pre-event consensus keeps `e_surprise_pct_signed = None`, `e_surprise_z = None`, and `e_surprise_missing = 1.0`. Missing surprise is never encoded as `0.0` before fold imputation.
3. **Sample-Size Awareness in Group H:** `h_matured_count`, `h_insufficient_evidence`, `h_outcomes_missing`, and `h_sample_weight_saturation = min(1.0, h_matured_count / 30.0)` accompany every historical return statistic so models distinguish 5-sample noise from 50-sample empirical frequencies.
4. **Market Timestamp Guard in Group M:** `market_regime.latest_market_timestamp` and `futures_meta.as_of_timestamp` strictly satisfy $\le \tau_{\text{decision}}$. Intraday session features normalize time cleanly in exchange-local timezone (`Asia/Kolkata` or `America/New_York`).
5. **Training-Fold-Only Normalization (`FoldSafeFeatureTransformer`):** Imputation means and continuous Z-score scalers are fitted strictly on each walk-forward training fold and applied to validation/test folds without reading validation/test distributions. Binary, probability, and explicit `_missing` flags are never shifted.
6. **Immutable Feature Manifest (`FeatureManifest`):** `build_feature_manifest()` emits a deterministic SHA-256 digest binding exact feature ordering, schema version (`5.0.0-alpha`), and active stream mask into every saved model artifact.
