# Phase 6 Live Event-to-Market Causal Pipeline (`PHASE_6_LIVE_PIPELINE`)

**Module:** [`backend/alpha/live_research_pipeline.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/live_research_pipeline.py)

---

## 1. End-to-End Execution Flow

```
  NewsEvent (decision_ts = local_received_at_utc)
     |--[1] LiveCaptureStore.record_news_event (immutable revision history)
     |--[2] Phase 3 FinancialNLPPipeline (E_t) + LiveInstrumentUniverse mapping
     |--[3] Phase 4 HistoricalIntelligencePipeline (H_t + live M_t ring buffer <= decision_ts)
     |      * Retrieved strictly BEFORE indexing query event to prevent self-match leakage
     |--[4] Phase 5 PointInTimeFeatureBuilder (87-feature causal contract)
     |--[5] LoadedShadowModel Eligibility Check + UncertaintyCalibratorAndGate
     |      * MODEL_ELIGIBLE -> calibrated prediction OR NO_TRADE abstention
     |      * NO_ELIGIBLE_MODEL / MODEL_UNVALIDATED / SYNTHETIC_DIAGNOSTIC_ONLY -> NO_TRADE forced
     |--[6] ShadowSignalLedger.append_prediction (SHA-256 sealed, SQLite trigger immutable)
     |--[7] record_matured_outcome_from_market -> ShadowSignalLedger.append_outcome (separate table)
```

---

## 2. Fail-Closed Abstention & Measured Latency Guarantees

- **Mandatory Fail-Closed Rules:** Unresolved entity, low event classification confidence, stale/missing live market observations (`MISSING_LIVE_MARKET_STATE`), insufficient historical analogues, excessive missing features (`> 65%`), unapproved/missing model (`MODEL_NOT_ELIGIBLE` / `NO_ELIGIBLE_MODEL`), or calibration failure all guarantee `action_state = "NO_TRADE"` while still persisting full point-in-time feature vectors and diagnostic telemetry cleanly.
- **Stage Latency Attribution (`perf_counter_ns`):** Every live pass logs exact `queue_delay_ms`, `news_to_nlp_ms`, `nlp_to_hist_ms`, `feature_build_ms`, `inference_ms`, `persistence_ms`, and `total_event_to_prediction_ms` aggregated into `p50`, `p95`, and `p99` summaries. Isolated CPU speeds do not substitute for full live network/market session latency reporting.
