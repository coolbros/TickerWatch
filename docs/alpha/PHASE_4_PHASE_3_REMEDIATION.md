# Phase 3 Correctness Audit & Remediation Report (Phase 4 Entry Gate)

**Document Version:** 1.0.0-alpha  
**Date:** 2026-11-10  
**Phase:** Phase 4 — Historical Event Vector Retrieval & Contextual Intelligence  
**Author:** Agastya Varshney <agastyavarshney@gmail.com>  
**Status:** Verification Gate Passed & Sign-Off Complete  

---

## 1. Executive Summary

Prior to constructing the Phase 4 persistent vector retrieval and contextual intelligence architecture, a comprehensive mathematical, architectural, and causal audit was conducted on the Phase 3 Financial NLP foundation.

Seven critical entry-gate deficiencies (A1 through A7) were discovered and systematically remediated. All modifications have been empirically verified across 31 dedicated unit tests in `tests/alpha/test_alpha_nlp.py` and the complete 144-test repository regression suite.

```
+--------------------------------------------------------------------------------------------------+
|                                    PHASE 3 AUDIT & REMEDIATION MATRIX                            |
+----+----------------------------+-----------------------------------+----------------------------+
| ID | Audit Item                 | Original Phase 3 Limitation       | Remediated Phase 4 State   |
+----+----------------------------+-----------------------------------+----------------------------+
| A1 | Decision Timestamp         | Used published_at_utc when pres.  | Strict local_received_at   |
|    | Integrity                  | Leaked non-received future wire   | Provenance across all 5    |
+----+----------------------------+-----------------------------------+----------------------------+
| A2 | Outcome Maturity &         | Filtered t_hist < tau_decision;   | Enforces t_hist + H <= tau |
|    | Look-Ahead Protection      | Leaked immature 1d/5d returns     | Immature returns masked    |
+----+----------------------------+-----------------------------------+----------------------------+
| A3 | Full-Document Numerical    | Extracted only headline even      | Full doc (headline + body) |
|    | Extraction                 | when body text existed            | Per-share & deduplication  |
+----+----------------------------+-----------------------------------+----------------------------+
| A4 | Multi-Entity Event         | Picked first resolved entity;     | Primary, secondary, all    |
|    | Representation             | dropped parent/sub & mappings     | instruments preserved      |
+----+----------------------------+-----------------------------------+----------------------------+
| A5 | Currency & Economic        | Mixed USD/INR baselines; treated  | Isolated by (entity, curr) |
|    | Polarity Integrity         | higher expense as positive        | Inverted polarity on costs |
+----+----------------------------+-----------------------------------+----------------------------+
| A6 | Persistent Historical      | In-memory list only; lost on      | Persistent SQLite WAL db;  |
|    | Retrieval                  | process restart                   | Idempotent index & reload  |
+----+----------------------------+-----------------------------------+----------------------------+
| A7 | Documentation              | Materiality tier discrepancy      | Reconciled tiers & latency |
|    | Consistency                | (0.50 vs 0.70); TF-IDF misnomer   | Accurate Jaccard metadata  |
+----+----------------------------+-----------------------------------+----------------------------+
```

---

## 2. Work Package A Remediation Details

### A1. Decision Timestamp Integrity & Provenance

**Issue Discovered:** In `backend/alpha/nlp_pipeline.py`, the pipeline assigned `published_at_utc` as the operational decision timestamp whenever it was present. In an actionable production or replay system, the system cannot act on an announcement before the bytes arrive at the local network socket. If an external RSS feed or news wire claims publication at `09:14:00 UTC` but the packet arrives at `09:15:02 UTC`, using `09:14:00 UTC` creates a retroactive look-ahead leakage where historical retrieval or market alignment queries market data from an interval prior to when the system actually knew the event.

**Remediation Applied:**
1. The operative actionable timestamp is strictly assigned to `news_event.local_received_at_utc`.
2. Preserved full immutable provenance across all five lifecycle checkpoints:
   - `published_at_utc`: Publisher-claimed release timestamp.
   - `provider_observed_at_utc`: External provider observation timestamp.
   - `local_received_at_utc`: System network socket ingress timestamp (operative decision timestamp).
   - `ingestion_completed_at_utc`: Database persistence completion timestamp.
   - `processed_at_utc`: NLP extraction and enrichment completion timestamp.
3. Added `TimestampProvenance` data model and property on `FinancialEventRepresentation`.
4. Verified via `test_decision_timestamp_precedence_and_provenance` that when publication precedes receipt, `decision_timestamp` is strictly equal to `local_received_at_utc`.

---

### A2. Historical Outcome Maturity & Look-Ahead Protection

**Issue Discovered:** In `backend/alpha/event_retrieval.py`, candidate historical matches were filtered using:
$$\tau(e_h) < \tau_{\text{decision}}$$
While this correctly ensured the historical event *occurred* before the query event, if that historical event occurred 2 hours prior to the query event, its metadata could be retrieved as an analogue. However, the record stored post-event market returns such as `post_event_return_1d` and `post_event_return_5d`. Including those returns in the contextual features (`mean_historical_return_1d`) leaked future price movement that had not yet occurred as of $\tau_{\text{decision}}$.

**Remediation Applied:**
1. Established the **Outcome Maturity Invariance**:
   - For a 1-day outcome horizon: $\tau(e_h) + 1\text{ day} \le \tau_{\text{decision}}$
   - For a 5-day outcome horizon: $\tau(e_h) + 5\text{ days} \le \tau_{\text{decision}}$
   - Generalised for arbitrary horizon $H$: $\tau(e_h) + \Delta t_H \le \tau_{\text{decision}}$
2. If an event is chronologically eligible as an event analogue ($\tau(e_h) < \tau_{\text{decision}}$) but its outcome horizon has not matured, the corresponding return is masked to `None`.
3. In `RetrievedHistoricalMatch`, explicit boolean flags `is_1d_outcome_matured` and `is_5d_outcome_matured` are exposed.
4. In `EventRetrievalContext`, separate counters `matured_1d_count` and `matured_5d_count` are maintained, and contextual return metrics (`mean_historical_return_1d`, `mean_historical_return_5d`) are calculated strictly over matured samples.
5. Verified via `test_outcome_maturity_and_lookahead_protection`.

---

### A3. Full-Document Numerical Extraction & Span Preservation

**Issue Discovered:** In `backend/alpha/nlp_pipeline.py`, the pipeline invoked `extractor.extract_metrics(news_event.headline)` even when extensive article body text was present, completely discarding detailed operational metrics, Capex breakdowns, and dividend announcements in the body.

**Remediation Applied:**
1. Refactored `FinancialEventExtractor.extract_metrics(text: str, body_text: Optional[str] = None)` to process the complete document.
2. Section attribution: Each metric records `text_section = "HEADLINE"` or `text_section = "BODY"`.
3. Added intelligent deduplication: Metrics extracted in the body that duplicate headline metrics (same type and value within 1% tolerance) are deduplicated while preserving body-only discoveries.
4. Added per-share dividend extraction (`_extract_per_share_values`) for strings like `"Rs 15 per share"` or `"$2.50 per share"`.
5. Added reporting period inference (`_infer_reporting_period`) extracting explicit financial quarters/years (`FY26`, `FY27`, `Q3`, `H1`).
6. Preserved raw character spans, original nominal values, original currencies, and confidence scores.
7. Verified via `test_full_document_extraction_and_deduplication`.

---

### A4. Multi-Entity Event Representation & Hierarchy

**Issue Discovered:** When an announcement referenced multiple corporations (e.g. `"Reliance Retail and Jio Infocomm announce joint initiative"` or client-contractor relationships like `"L&T bags order from Middle East client"`), Phase 3 selected only the first resolved entity and discarded all secondary entities and instrument mappings.

**Remediation Applied:**
1. Extended `FinancialEventRepresentation` to include:
   - `primary_entity`: Highest-confidence resolved entity.
   - `secondary_entities`: All additional corporate entities resolved in text.
   - `all_entities`: Complete list of resolved entities.
   - `all_instrument_mappings`: Deduplicated union of Phase 2 instrument mappings and resolved entity instruments.
2. Preserved parent/subsidiary relationships (e.g. `parent_entity_id="RELIANCE"` for `RELIANCE_RETAIL` and `JIO`).
3. Retained full backwards compatibility by maintaining `rep.entity_resolution` as an alias for `primary_entity`.
4. Verified via `test_multi_entity_and_instrument_mapping`.

---

### A5. Currency Isolation & Economic Polarity of Surprise

**Issue Discovered:**
1. Revenue baselines for materiality in `backend/alpha/surprise.py` were keyed only by ticker symbol (`"TCS"`), causing potential collisions if USD-denominated announcements were compared directly against INR revenue figures without explicit exchange rates.
2. The surprise engine categorized any positive difference ($A > E$) as `POSITIVE_SURPRISE`. In financial reality, higher-than-expected operating expenses, liabilities, losses, or inflation figures represent negative economic outcomes.

**Remediation Applied:**
1. Partitioned `_revenue_baselines` strictly by `(entity_id, CurrencyUnit)`. Baseline lookup requires matching currency, preventing cross-currency contamination.
2. Added economic polarity inversion for cost and liability metrics:
   $$\text{is\_cost} = \text{metric\_type} \in \{\text{EXPENSE}, \text{DEBT}, \text{LOSS}, \text{INFLATION\_CPI}\}$$
   - For standard revenue/profit metrics: $A > E \implies \text{POSITIVE\_SURPRISE}$
   - For expense/debt/loss metrics: $A > E \implies \text{NEGATIVE\_SURPRISE}$
3. Exposed `is_cost_metric`, `original_value`, `original_currency`, and `baseline_currency` in `NumericalSurpriseResult`.
4. Verified via `test_currency_integrity_and_cost_surprise_polarity`.

---

### A6. Persistent Historical Retrieval Engine

**Issue Discovered:** The Phase 3 `CausalHistoricalEventRetriever` stored historical event records purely in an in-memory Python list. Restarting the backend process or running tests in isolated environments required manual re-indexing.

**Remediation Applied:**
1. Implemented persistent SQLite storage in `CausalHistoricalEventRetriever` utilizing WAL mode and busy timeouts.
2. Table schema `historical_event_records`:
   - `event_id TEXT PRIMARY KEY`
   - `instrument_id TEXT`
   - `headline TEXT NOT NULL`
   - `event_subtype TEXT NOT NULL`
   - `timestamp TEXT NOT NULL`
   - `materiality_score REAL NOT NULL`
   - `surprise_category TEXT`
   - `post_event_return_1d REAL`
   - `post_event_return_5d REAL`
   - `sector TEXT`
   - `indexed_at TEXT NOT NULL`
3. Implemented idempotent indexing (`save_event_to_db` with `INSERT OR REPLACE`).
4. Implemented recovery loading (`load_from_db`) restoring state automatically on initialization.
5. Verified via `test_sqlite_persistence_in_retriever`.

---

### A7. Documentation Consistency Reconciliation

**Issue Discovered:**
1. `PHASE_3_EVALUATION_REPORT.md` stated in section 3.E that `TIER_3_HIGH` was $\ge 0.50$, whereas the canonical code definition in `materiality.py` and `PHASE_3_MATERIALITY.md` defines `TIER_3_HIGH` as $[0.70, 1.00]$ and `TIER_2_MODERATE` as $[0.35, 0.70)$.
2. `PHASE_3_RESEARCH_PLAN.md` referred to "TF-IDF + BM25", which was inaccurate since the actual Phase 3 implementation used Token Jaccard lexical similarity combined with subtype, instrument, and materiality scoring.
3. Initial evaluation draft reported P50 of $0.287\text{ ms}$, whereas the final benchmark artifact `docs/alpha/benchmark_nlp_results.json` recorded P50 of $0.341\text{ ms}$ ($341\ \mu\text{s}$) and P95 of $0.754\text{ ms}$ ($754\ \mu\text{s}$) on the Windows host environment.

**Remediation Applied:**
1. Corrected `docs/alpha/PHASE_3_EVALUATION_REPORT.md` line 68 to reflect canonical tier thresholds:
   - Routine: $[0.00, 0.35)$, `TIER_1_ROUTINE`
   - Moderate: $[0.35, 0.70)$, `TIER_2_MODERATE`
   - High Catalyst: $[0.70, 1.00]$, `TIER_3_HIGH`
2. Corrected `docs/alpha/PHASE_3_RESEARCH_PLAN.md` line 128 to state: "Causal lexical and metadata hybrid retrieval (Token Jaccard similarity + subtype + instrument + materiality scoring)."
3. Reconciled latency documentation: Both $0.287\text{ ms}$ (clean test run) and $0.341\text{ ms}$ (full benchmark artifact) comfortably beat the Phase 3 entry gate $(< 2.0\text{ ms})$.

---

## 3. Verification & Test Evidence

All 31 unit tests in `tests/alpha/test_alpha_nlp.py` execute and pass offline:

```
tests/alpha/test_alpha_nlp.py ............................... [100%]
======================== 31 passed, 1 warning in 0.54s ========================
```

The entire repository test suite (144 unit, integration, and contract tests) passes cleanly:

```
================ 144 passed, 2 deselected, 1 warning in 4.66s =================
```

## 4. Phase 4 Readiness Gate Sign-Off

With all seven remediation points implemented, tested, and documented, the foundation is now strictly causal, free of look-ahead leakage, multi-entity aware, and ready for Work Package B (Historical Data Acquisition) and Work Package C (Persistent Historical Store).
