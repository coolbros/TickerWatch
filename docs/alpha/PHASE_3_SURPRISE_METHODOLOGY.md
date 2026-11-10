# Phase 3 Numerical Surprise Detection and Consensus Integration

**Document Version:** 1.0.0-alpha  
**Date:** 2026-11-10  
**Phase:** Phase 3 — Financial NLP, Entity Resolution & Numerical Surprise Detection  
**Status:** Canonical Implementation Specification  

---

## 1. Executive Summary and Thematic Principle

In financial markets, price movements are not driven merely by the absolute magnitude of reported numbers, but by **how much the reported number deviates from market expectations**. A company reporting 20% earnings growth may sell off sharply if consensus expected 25% growth.

The central research principle of TickerWatch Alpha is:
> **Determine how surprising the information was relative to prior market consensus at the precise instant of disclosure, with strict temporal causality and zero look-ahead bias.**

---

## 2. Mathematical Formulations

### 1. Absolute Surprise ($S$)
$$S = A - E$$
Where:
- $A$: Actual reported figure (extracted from corporate announcement or press release).
- $E$: Consensus analyst expectation captured prior to the disclosure.

### 2. Percentage Surprise ($S_{\%}$)
$$S_{\%} = \frac{A - E}{|E|} \times 100$$
Using $|E|$ (the absolute value of consensus in the denominator) guarantees mathematical and financial consistency across both profits and losses:
- **Positive Example:** Expected profit of \$100M, Actual profit of \$120M $\rightarrow S_{\%} = \frac{120 - 100}{|100|} \times 100 = +20.0\%$.
- **Negative Profit Example:** Expected profit of \$100M, Actual profit of \$80M $\rightarrow S_{\%} = \frac{80 - 100}{|100|} \times 100 = -20.0\%$.
- **Loss Outperformance Example:** Expected loss of -\$100M, Actual loss of -\$80M $\rightarrow S_{\%} = \frac{-80 - (-100)}{|-100|} \times 100 = \frac{+20}{100} \times 100 = \mathbf{+20.0\%}$ (*correctly positive: beat consensus by \$20M*).

### 3. Edge Case: Zero Denominators
If $|E| < 1\times 10^{-9}$:
- If $|A| < 1\times 10^{-9}$, $S_{\%} = 0.0\%$ (`IN_LINE`).
- If $|A| \ge 1\times 10^{-9}$, $S_{\%}$ is mathematically undefined (`None`), and classification falls back to sign of absolute surprise $S$. Division by zero is strictly prohibited.

### 4. Standardised Surprise / Z-Score ($Z$)
When analyst dispersion ($\sigma$) is published alongside consensus:
$$Z = \frac{A - E}{\sigma}$$
Where $\sigma > 0$. Measures the surprise in units of standard deviations.

---

## 3. Baseline Materiality for Non-Consensus Events

Certain corporate events (e.g. commercial contract wins, capex investments) do not have prior numeric sell-side consensus estimates. For these events, the engine evaluates the figure relative to company-specific fundamental baselines:

### Order / Contract Win Materiality Ratio
$$\text{Materiality Ratio} = \frac{\text{Contract Value}}{\text{Trailing Twelve Month (TTM) Revenue}}$$
- **Rule of Thumb:**
  - Ratio $< 1\%$: Routine operational order, minimal stock price impact.
  - Ratio $1\% - 5\%$: Moderate operational catalyst.
  - Ratio $> 5\%$: Highly material catalyst, prioritized for downstream temporal alignment.

---

## 4. Causal Temporal Integrity Mandate

To guarantee that research models trained on Alpha datasets do not suffer from look-ahead bias:

1. **Strict Timestamp Ordering:** An expectation is valid for event $e$ at timestamp $\tau_{\text{decision}}$ if and only if:
   $$\tau_{\text{captured\_at}} < \tau_{\text{decision}}$$
   If $\tau_{\text{captured\_at}} \ge \tau_{\text{decision}}$, `ValueError` is raised immediately.
2. **Post-Event Revision Rejection:** Analyst revisions made after an announcement must never be backfilled into the pre-event expectation field.
3. **Missing Expectation Protocol:** If no consensus was registered prior to $\tau_{\text{decision}}$, the engine explicitly returns:
   $$\text{surprise\_category} = \text{UNAVAILABLE\_EXPECTATION}$$
   Imputing fake consensus numbers or silently dropping the field is strictly prohibited.

---

## 5. Output Schema

The output model (`NumericalSurpriseResult`) captures:

```json
{
  "metric_name": "NET_PROFIT_PAT",
  "actual_value": 12434.0,
  "expected_value": 12000.0,
  "expectation_source": "CONSENSUS_FEED",
  "expectation_captured_at": "2026-11-10T01:00:00Z",
  "absolute_surprise": 434.0,
  "percentage_surprise": 3.617,
  "standardised_surprise_z": 2.17,
  "surprise_category": "POSITIVE_SURPRISE",
  "materiality_ratio": null,
  "baseline_annual_revenue": null,
  "in_line_threshold_pct": 1.0,
  "status_notes": "Computed against point-in-time consensus expectation.",
  "evaluated_at": "2026-11-10T03:09:08.734405Z"
}
```
