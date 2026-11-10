# Phase 3 Financial Event Materiality and Relevance Specification

**Document Version:** 1.0.0-alpha  
**Date:** 2026-11-10  
**Phase:** Phase 3 — Financial NLP, Entity Resolution & Numerical Surprise Detection  
**Status:** Canonical Implementation Specification  

---

## 1. Executive Summary and Thematic Independence

In predictive financial research, many systems confound three orthogonal dimensions of information:
1. **Sentiment ($[-1.0, +1.0]$):** Is the development favourable or unfavourable for the company's prospects?
2. **Surprise ($S = A - E, S_{\%}$):** Did the reported metrics deviate from what the market consensus already priced in?
3. **Materiality ($[0.0, 1.0]$):** How economically significant is the event to the entity and the broader market?

### The Independence Axiom

$$\text{Sentiment} \perp\!\!\!\perp \text{Surprise} \perp\!\!\!\perp \text{Materiality}$$

| Event Scenario | Sentiment | Numerical Surprise | Materiality | Interpretation |
|:---|:---:|:---:|:---:|:---|
| **Catastrophic Plant Fire / Fraud Probe** | Strongly Negative ($-0.95$) | Large Negative | **Very High ($0.90$)** | Critical risk catalyst; severe market impact regardless of prior rumors. |
| **Anticipated \$50B Mega-Merger Announced** | Moderately Positive ($+0.40$) | In-line ($0.0\%$) | **Very High ($0.88$)** | Zero surprise because it was leaked, but massive long-term economic materiality. |
| **Routine Board Meeting Intimation** | Neutral ($0.00$) | Unquantified | **Routine ($0.22$)** | Standard compliance disclosure; negligible market pricing impact. |
| **Micro-Cap beats Q3 profit by 100% on ₹5 lakh base** | Strongly Positive ($+0.90$) | Extreme ($+100\%$) | **Low ($0.25$)** | High percentage beat on economically negligible nominal amounts. |

---

## 2. Multi-Dimensional Composite Formulation

The materiality score is evaluated as a weighted linear combination of five fundamental vectors:

$$\text{Composite Materiality} = w_{\text{cat}} M_{\text{cat}} + w_{\text{mag}} M_{\text{mag}} + w_{\text{fwd}} M_{\text{fwd}} + w_{\text{nov}} M_{\text{nov}} + w_{\text{sys}} M_{\text{sys}}$$

Where $\sum w_i = 1.0$.

### 1. Category Intrinsic Weight ($M_{\text{cat}}$) — $w_{\text{cat}} = 0.35$
Reflects inherent structural severity:
- `BANKRUPTCY_INSOLVENCY`: $1.00$
- `INTEREST_RATE_DECISION`: $0.90$
- `MERGER_ACQUISITION`: $0.85$
- `EARNINGS_ANNOUNCEMENT`: $0.80$
- `REGULATORY_INVESTIGATION`: $0.75$
- `CONTRACT_ORDER_WIN`: $0.70$
- `CAPITAL_EXPENDITURE`: $0.50$
- `MANAGEMENT_CHANGE`: $0.45$
- `ROUTINE_ADMINISTRATIVE`: $0.10$

### 2. Quantitative Magnitude ($M_{\text{mag}}$) — $w_{\text{mag}} = 0.25$
Evaluates the scale of the catalyst:
- For order wins / capex: $\text{Materiality Ratio} = \frac{\text{Value}}{\text{Annual Revenue}}$.
  - $\ge 10\%$ of revenue: $1.00$
  - $\ge 5\%$ of revenue: $0.85$
  - $\ge 2\%$ of revenue: $0.65$
  - $< 0.5\%$ of revenue: $0.25$
- For earnings / CPI surprise:
  - $|S_{\%}| \ge 25\%$: $0.95$
  - $|S_{\%}| \ge 5\%$: $0.60$
  - In-line ($< 1\%$): $0.20$
- Unquantified baseline: $0.30$

### 3. Forward Guidance Prominence ($M_{\text{fwd}}$) — $w_{\text{fwd}} = 0.15$
Forward guidance revisions carry higher market impact than historical quarterly figures:
- Primary `GUIDANCE_REVISION`: $0.95$
- Secondary guidance cut/raise: $0.80$
- Macro policy guidance: $0.70$
- Backward-looking results: $0.20$

### 4. Announcement Novelty ($M_{\text{nov}}$) — $w_{\text{nov}} = 0.15$
- Breaking / First announcement: $0.90$
- Follow-up revision / clarification: $0.40$
- Routine administrative filing: $0.15$

### 5. Spillover Potential ($M_{\text{sys}}$) — $w_{\text{sys}} = 0.10$
- Systemic / Macroeconomic / Policy rate: $0.95$ (impacts full banking sector & benchmark index)
- Index constituent / Conglomerate (Reliance, HDFC Bank, TCS): $0.65$
- Single mid/small-cap entity: $0.20$

---

## 3. Materiality Tiering

| Tier | Score Range | Description | Typical Event Examples |
|:---|:---:|:---|:---|
| **Tier 1: Routine** | $[0.00, 0.35)$ | Low or negligible market pricing impact | Board meeting intimations, loss of share certificates, minor executive transfers |
| **Tier 2: Moderate Catalyst** | $[0.35, 0.70)$ | Moderate company-specific catalyst | Dividend announcements, medium order wins ($<5\%$ revenue), small capex plants |
| **Tier 3: High Catalyst** | $[0.70, 1.00]$ | Significant market-moving event | Central bank rate decisions, major earnings surprises, large M&A, regulatory probes |

---

## 4. Benchmark Calibration Results

| Test Event Description | Extracted Subtype | Composite Materiality | Assigned Tier |
|:---|:---|:---:|:---:|
| `RBI MPC hikes repo rate by 50 bps` | `INTEREST_RATE_DECISION` | **0.750** | `TIER_3_HIGH` |
| `L&T bags mega order worth Rs 15,000 crore` | `CONTRACT_ORDER_WIN` | **0.725** | `TIER_3_HIGH` |
| `TCS declares interim dividend of Rs 10 per share` | `DIVIDEND_ANNOUNCEMENT` | **0.485** | `TIER_2_MODERATE` |
| `Intimation of Board Meeting and Trading Window Closure` | `ROUTINE_ADMINISTRATIVE` | **0.227** | `TIER_1_ROUTINE` |

The engine is thread-safe, executes in $< 0.10\text{ ms}$, and produces inspectable audit rationales for every assessment.
