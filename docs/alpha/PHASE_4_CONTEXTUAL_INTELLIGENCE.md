# TickerWatch Alpha — Phase 4 Contextual Intelligence Features (`PHASE_4_CONTEXTUAL_INTELLIGENCE`)

> **Phase:** Phase 4 — Work Package F  
> **Status:** IMPLEMENTED  
> **Primary Module:** `backend/alpha/contextual_features.py`  

---

## 1. Purpose & Statistical Discipline

The Contextual Feature Synthesizer converts causally retrieved historical event matches (`EventRetrievalContext`) and their strictly matured outcome records into uncertainty-aware distributional features (`ContextualIntelligenceFeatures`) for downstream Phase 5 models.

### Core Anti-Illusion Guardrails
1. **Small-Sample Gating (`min_sample_size = 5` default):**
   - If zero matured outcomes exist, status is `NO_MATCHED_OUTCOMES` and all return moments/quantiles/intervals remain `None`.
   - If `0 < matured_count < min_sample_size`, status is explicitly set to `INSUFFICIENT_EVIDENCE` (`insufficient_evidence=True`), and means, medians, standard deviations, quantiles, and confidence intervals are suppressed (`None`) so models never mistake a noisy 2-event average for precise signal. Integer hit/miss counts remain visible.
2. **Double-Lock Outcome Verification:**
   - Every underlying outcome record must verify `outcome_end_ts <= decision_timestamp` AND `data_quality_status in {'VERIFIED', 'CORPORATE_ACTION_ADJUSTED', 'ROLLOVER_ADJUSTED'}`. Any halted, incomplete, immature, or invalid-rollover record is excluded before aggregation.
3. **Expectation Integrity on Surprise Conditioning:**
   - The `SURPRISE_MATCHED` slice activates **only** when both the query event and candidate analogues carry verified numerical consensus comparisons; when `surprise_category == UNAVAILABLE_EXPECTATION`, the slice reports `EXPECTATION_UNAVAILABLE`.

---

## 2. Feature Schema Structure

For each horizon in `{1m, 5m, 30m, 1h, 1d, 5d}`, `HorizonResponseDistribution` exposes:
- **Sample Telemetry:** `comparable_event_count`, `matured_count`, `evidence_status`, `insufficient_evidence`, `data_quality_status`.
- **Central Tendency & Weighting:** unweighted `mean_return`, `similarity_weighted_mean_return`, `median_return`, `mean_benchmark_adjusted_return`.
- **Dispersion & Asymmetry:** sample `std_dev`, empirical `quantiles` (`p10`, `p25`, `p50`, `p75`, `p90`), `positive_count`, `negative_count`, `flat_count`, `positive_frequency`, `negative_frequency`, `mean_realized_volatility`.
- **Uncertainty Quantification:** deterministic seeded 1,000-resample 95% percentile bootstrap confidence interval (`BootstrapConfidenceInterval`) for the mean return.
- **Conditioned Slices:** `INSTRUMENT_SPECIFIC`, `SECTOR_SPECIFIC`, `MATERIALITY_MATCHED` ($\pm 0.20$), and `SURPRISE_MATCHED`.
- **Temporal Recency:** `recency_days_latest` and `recency_days_median`.
