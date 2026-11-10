# TickerWatch Alpha — Phase 3 Research Plan & Technical Specification
## Financial NLP, Entity Disambiguation & Numerical Surprise Detection

> **Document Version:** 1.0.0-alpha  
> **Status:** APPROVED RESEARCH PLAN — PHASE 3  
> **Authors:** Quantitative Research & Financial Machine Learning Team  
> **Mandate:** Causal Information Interpretation, Point-in-Time Correctness, and Sub-20ms CPU Inference

---

## 1. Research Objectives & Hypotheses

Phase 0 established Hypothesis $H1$:
$$\text{Directional edge is maximized when numerical earnings surprises } (\Delta_{\text{surprise}}) \text{ are conditioned on entity-disambiguated text materiality.}$$

Phase 3 builds the information-extraction engine to test this hypothesis. The system must transform raw news articles into structured `FinancialEventRepresentation` objects answering five quantitative questions at decision timestamp $\tau_{\text{decision}}$:
1. **What happened?** (Hierarchical multi-label event classification).
2. **Which entities & instruments are affected?** (Contextual disambiguation & 4-tier relevance mapping).
3. **What are the quantified metrics?** (Structured numerical extraction of actuals, revisions, contracts).
4. **How surprising is it relative to prior expectations?** (Point-in-time numerical surprise $S$ and $S_{\%}$).
5. **How material and confident is the observation?** (Calibrated materiality score $\mathcal{M} \in [0, 1]$ and confidence score $\mathcal{C} \in [0, 1]$).

---

## 2. Architectural Blueprint & Subsystem Decomposition

```
+====================================================================================================+
|                                    PHASE 3 FINANCIAL NLP PIPELINE                                  |
+====================================================================================================+

   [ CANONICAL NEWS EVENT (Phase 2) ]
   (Headline, Body Text, Ingress Timestamp tau)
                 |
                 v
   +---------------------------------------+
   | WORK PACKAGE B: ENTITY RESOLUTION     |
   | * Contextual Disambiguation           |
   | * Alias Dictionary & CIK Matching     |
   | * 4-Tier Instrument Relevance Mapping |
   +-------------------+-------------------+
                       |
                       v
   +---------------------------------------+
   | WORK PACKAGE C: EVENT CLASSIFICATION  |
   | * Hierarchical Event Taxonomy         |
   | * Deterministic Classifier (Baseline) |
   | * Compact Financial Transformer Head  |
   +-------------------+-------------------+
                       |
                       v
   +---------------------------------------+
   | WORK PACKAGE D: STRUCTURED EXTRACTION |
   | * Indian (Lakh/Crore) & Global Units  |
   | * Actuals, Guidance, Capex, Contracts |
   | * Evidence Spans & Provenance         |
   +-------------------+-------------------+
                       |
                       v
   +---------------------------------------+
   | WORK PACKAGE E: SURPRISE ENGINE       |
   | * S = A - E, S% = (A - E) / |E|       |
   | * Point-in-Time Expectation Gating    |
   | * Explicit UNAVAILABLE_EXPECTATION    |
   +-------------------+-------------------+
                       |
                       v
   +---------------------------------------+
   | WORK PACKAGE F: MATERIALITY SCORING   |
   | * Category Severity & Magnitude Ratio |
   | * Independent of Sentiment & Surprise |
   +-------------------+-------------------+
                       |
                       v
   +---------------------------------------+
   | WORK PACKAGE G: HISTORICAL RETRIEVAL  |
   | * Strict Pre-Event Lookback (tau_H < t)|
   | * Top-K Comparable Historical Analogues|
   +-------------------+-------------------+
                       |
                       v
   [ CANONICAL OUTPUT: FinancialEventRepresentation (v1.0.0) ]
```

---

## 3. Work Package Roadmap & Implementation Strategy

### Work Package B: Financial Entity Resolution (`backend/alpha/entities.py`)
- **Challenge:** Avoid naive substring matching (e.g. ticker "IT" vs English pronoun "it", "RIL" vs "Reliance Power").
- **Solution:** Multi-pass context-aware resolution engine:
  1. Exact ticker match guarded by boundary checks and exchange qualification (e.g. `NSE:RELIANCE`).
  2. Multi-word corporate alias dictionary with stem matching.
  3. Parent/subsidiary knowledge graph (e.g. Jio Platforms -> Reliance Industries).
  4. Explicit `UNRESOLVED` and `AMBIGUOUS` states when confidence is below threshold.

### Work Package C: Event Taxonomy & Classification (`backend/alpha/event_classifier.py`)
- **Taxonomy:** 4 primary categories (Corporate Financial, Corporate Operational, Macroeconomic, Market/Systemic) spanning 25 granular subtypes.
- **Independence:** Event Sentiment $\neq$ Event Materiality $\neq$ Event Surprise. A negative event can be in-line and non-surprising.
- **Dual-Model Architecture:**
  - *Model A (Deterministic Rule Engine):* High-precision regex keyword scoring ($< 0.1\text{ms}$).
  - *Model B (Pretrained Compact Transformer):* Evaluated against open-weights models (`ProsusAI/finbert`, `sentence-transformers/all-MiniLM-L6-v2`) for multi-label classification.

### Work Package D: Structured Numerical Extraction (`backend/alpha/event_extraction.py`)
- **Challenge:** Indian regulatory announcements express financial figures in ₹ Crores and Lakhs, while international filings use Millions and Billions.
- **Solution:** Universal financial unit normalizer:
  - $1\text{ Crore} = 10,000,000$ ($10^7$).
  - $1\text{ Lakh} = 100,000$ ($10^5$).
  - $1\text{ Billion} = 1,000,000,000$ ($10^9$).
  - Extract actuals, revisions, comparative periods, currencies (`INR`, `USD`), and percentages.
  - Zero hallucination: Missing values default to explicit `None`.

### Work Package E: Numerical Surprise Detection (`backend/alpha/surprise.py`)
- **Formula:**
  $$S = A - E, \quad S_{\%} = \frac{A - E}{|E|} \times 100$$
- **Safeguards:**
  - Denominator clipping when $|E| \to 0$ or $E \le 0$.
  - Contract win ratio: $\text{Contract Ratio} = \frac{\text{Contract Value}}{\text{Annual Revenue Baseline}}$.
  - **Point-in-Time Integrity:** Consensus $E$ must be verified as published prior to $\tau_{\text{decision}}$. If absent, state is strictly `UNAVAILABLE_EXPECTATION`.

### Work Package F: Materiality & Event Relevance (`backend/alpha/materiality.py`)
- Computes scalar materiality $\mathcal{M} \in [0.0, 1.0]$ based on:
  - Event category intrinsic weight (e.g. Bankruptcy = 1.0, Order Win = 0.6, Routine Meeting = 0.1).
  - Relative financial magnitude (Value / Company Size).
  - Forward guidance revision direction.

### Work Package G: Historical Event Retrieval (`backend/alpha/event_retrieval.py`)
- Local-first dense/sparse hybrid retrieval (TF-IDF + BM25 / Cosine similarity).
- **Strict Invariant:** Retrieved events must satisfy $t_{\text{historical}} < \tau_{\text{decision}}$. Future events are purged from candidate pools.

### Work Package H: Unified Pipeline & Schema (`backend/alpha/nlp_pipeline.py`)
- Integrates all components into an end-to-end processing pipeline emitting `FinancialEventRepresentation` (schema `v1.0.0`).

### Work Package I & J: Evaluation, Calibration & Tests
- Offline gold evaluation benchmark with annotated ground-truth test cases.
- Comprehensive test suite covering edge cases, negative values, crossed books, and temporal constraints.
- Latency profiling ensuring CPU execution $< 20\text{ms}$.
