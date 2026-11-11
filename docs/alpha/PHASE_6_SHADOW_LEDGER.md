# Phase 6 Immutable Shadow Signal Ledger (`PHASE_6_SHADOW_LEDGER`)

**Module:** [`backend/alpha/shadow_ledger.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/shadow_ledger.py) (`LEDGER_SCHEMA_VERSION = "6.0.0-alpha"`)

---

## 1. Structural Separation & Tamper Prevention

Every live or shadow inference appends a canonical `ShadowLedgerEntry` to SQLite (`shadow_predictions`) **before** any outcome horizon elapses:
- **SQLite Trigger Enforcement:** Both `shadow_predictions` and `shadow_outcomes` enforce `BEFORE UPDATE` and `BEFORE DELETE` triggers raising `IMMUTABLE_SHADOW_LEDGER` aborts. Predictions can never be patched once market moves occur.
- **Separate Linked Outcomes:** Matured post-event market prices, bid-ask spreads, conservative transaction cost estimates, and net executable returns are appended later into `shadow_outcomes` linked via foreign key `prediction_id`.
- **Canonical Digest Verification:** `ShadowLedgerEntry.compute_canonical_sha256()` hashes the entire serialized `(E_t, H_t, M_t, features, manifest_sha256, model_lineage, prediction)` payload so `verify_integrity(prediction_id)` immediately detects any external alteration.

---

## 2. Eligibility Status Tags Recorded Per Entry

| Status Tag | Meaning in Shadow Operation |
|---|---|
| `MODEL_ELIGIBLE` | Trained on verified real data, zero leakage, SHA-256 registry entry loaded |
| `MODEL_UNVALIDATED` | Real-data model lacking approved OOS gate — diagnostic/telemetry only |
| `SYNTHETIC_DIAGNOSTIC_ONLY` | Synthetic-trained model exercising plumbing — never evidence of live alpha |
| `NO_ELIGIBLE_MODEL` | Zero eligible model loaded — `E_t/H_t/M_t` feature snapshot & `NO_TRADE` persisted cleanly for dataset accumulation |
