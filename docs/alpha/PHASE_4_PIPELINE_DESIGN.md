# TickerWatch Alpha — Phase 4 Unified Historical Intelligence Pipeline Design (`PHASE_4_PIPELINE_DESIGN`)

> **Phase:** Phase 4 — Work Package H  
> **Status:** IMPLEMENTED  
> **Schema Version:** `4.0.0-alpha`  
> **Primary Module:** `backend/alpha/historical_intelligence.py`  

---

## 1. End-to-End Orchestration Flow

```
  NewsEvent / Raw Financial Text
         |
         v  [Phase 3 - FinancialNLPPipeline]
  FinancialEventRepresentation (E_t)  [decision_timestamp = local_received_at_utc]
         |
         +----------------------------------------+
         |                                        |
         v  [Work Package G]                      v  [Work Packages C & D]
  MarketRegimeContextBuilder               PersistentHistoricalEventStore (SQLite WAL)
    (strictly <= tau_decision)               |  Jurisdiction / Instrument / Subtype / Time pre-filter
         |                                   |  Duplicate & Revision hash exclusion
         v                                   v
  MarketRegimeContext (M_t)                PersistentVectorRetriever
         |                                   |  BM25 + Jaccard + ONNX BGE-Small Cosine + Finance Stratum
         |                                   |  SQL-enforced outcome_end_ts <= tau_decision
         |                                   v
         |                                 ContextualFeatureSynthesizer [Work Package F]
         |                                   |  Distributions, quantiles, bootstrap 95% CIs, sample gating
         |                                   v
         +-----------------+-----------------+
                           |
                           v
           HistoricalIntelligenceRepresentation (4.0.0-alpha)
             - Causal Feature Bundle (E_t, H_t, M_t)
             - Hard Separation from Query Future Return Labels (Delta F_{t+h})
```

---

## 2. Hard Invariants & Feature/Label Separation

1. **Query Pre-Retrieval Ordering:** When `index_event_after_query=True`, retrieval and context generation ALWAYS execute *before* inserting `rep` into `PersistentHistoricalEventStore`, backed additionally by explicit `query_event_id` and `content_hash` SQL exclusions.
2. **Jurisdiction Non-Substitution:** `infer_event_jurisdiction()` routes `NSE:` / `BSE:` events to `MarketJurisdiction.IN` and `US:` / SEC issuer events to `MarketJurisdiction.US` so US earnings distributions never contaminate Indian NIFTY/single-stock analogies.
3. **Zero Query Future Labels (`contains_query_future_labels = False`):** `HistoricalIntelligenceRepresentation` never holds any post-event returns belonging to `query_event_id`. Forward labels of the query event are generated offline solely by `EventStudyEngine` into `historical_event_outcomes`.
4. **Backwards-Compatible Phase 3 Bridge:** `FinancialNLPPipeline.process_news_event()` and `process_raw_text()` remain 100% signature-compatible; callers opt into Phase 4 via `HistoricalIntelligencePipeline` or `nlp_pipeline.build_historical_intelligence(rep, store)`.
