# Phase 6 Live-vs-Replay Deterministic Parity Design (`PHASE_6_REPLAY_PARITY`)

**Module:** [`backend/alpha/live_replay_parity.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/live_replay_parity.py)

---

## 1. Parity Contract

Given persisted raw payloads and immutable revision snapshots recorded by a live `LiveResearchPipeline` run (`LiveCaptureStore` + `ShadowSignalLedger`), replaying the exact chronological merge (`local_received_at_utc`, Market before News on tie) through a fresh isolated pipeline must match:
1. Exact `decision_timestamp_utc` and `news_revision`
2. Identical Phase 3 `E_t` classification, resolved entity, and materiality
3. Identical Phase 4 `H_t` analogue counts and `M_t` prevailing mid/spread regime
4. Exact Phase 5 `87`-feature vectors within floating-point tolerance (`<= 1e-9`)
5. Identical calibrated output (`action_state`, `signal_direction`, `no_trade_status`, `no_trade_reasons`, `expected_return`)

Any sequence gap, out-of-order market packet, duplicate arrival, revision update, or restart discrepancy is logged transparently in `EventParityComparison.discrepancies` and `integrity_anomalies_detected`.
