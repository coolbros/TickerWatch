# Phase 4 Correctness Audit & Remediation Report (Phase 5 Entry Gate)

**Document Version:** 1.0.0-alpha  
**Date:** 2026-11-11  
**Phase:** Phase 5 — Work Package A (Entry Gate Remediation)  
**Author:** Agastya Varshney (`agastyavarshney@gmail.com`)  
**Status:** COMPLETED & VERIFIED — SAFE FOR PHASE 5 DATA AUDIT & MODELING  

---

## 1. Executive Summary

Before auditing datasets or training predictive models in Phase 5, seven foundational issues (**A1–A7**) in Phase 4 were audited, remediated across `backend/alpha/`, and locked down with deterministic regression tests.

---

## 2. Remediation Matrix (A1–A7)

| ID | Area | Original Phase 4 Defect | Remediated State (Phase 5 WP-A) | Verification Test |
|---|---|---|---|---|
| **A1** | Indian & US Timestamp Normalization (`historical_import.py`) | Naive NSE (`10-Nov-2026 09:15:00`), BSE, and RBI strings silently called `.replace(tzinfo=timezone.utc)`, introducing a **5.5-hour shift**; SEC lacked explicit ET DST & date-only handling. | Added `parse_source_timestamp_to_utc` binding NSE/BSE/RBI naive strings strictly to `Asia/Kolkata` (`09:15 IST -> 03:45 UTC`), SEC naive strings to DST-aware `America/New_York` (EST `-05:00` vs EDT `-04:00`), preserving explicit offsets and full `_timestamp_provenance` metadata. | `test_nse_corporate_importer`, `test_bse_filings_importer`, `test_rbi_macro_importer`, `test_sec_filings_importer_and_us_dst_handling` |
| **A2** | Bitemporal Historical Revisions (`historical_store.py`) | Revisions overwrote `historical_events` with the latest headline/metrics/timestamp; querying *before* a correction failed to reconstruct Revision 1 or exposed future revision timestamps/vectors. | Upgraded `historical_event_revisions` to store full immutable snapshots (`entities`, `mappings`, `instruments`, `metrics`, `surprise`, `materiality`, `embedding_model_version`, `embedding_ref`) plus `historical_event_revision_embeddings`; `query_events` and `get_event_as_of` reconstruct the latest revision with `system_available_at_utc < decision_timestamp` via SQL `ROW_NUMBER()`. | `test_bitemporal_revision_as_of_reconstruction` |
| **A3** | Embedding Preprocessing Parity (`event_embeddings.py`, `event_retrieval.py`) | `ONNXBgeSmallEncoder` truncated at `192` tokens, hardcoded zero `token_type_ids`, omitted asymmetric BGE retrieval instruction prefix, and needed CLS vs Mean parity reconciliation. | Reconciled against official `BAAI/bge-small-en-v1.5` spec and cached `Xenova/bge-small-en-v1.5` (`ea104dacec62c0de699686887e3f920caeb4f3e3`): `[CLS]` pooling (`[CLS]=101`, `[SEP]=102`), `max_length=512`, true `e.type_ids`, asymmetric `encode_queries` (`"Represent this sentence for searching relevant passages: "`) vs `encode_documents`, and bumped tag to **`bge-small-en-v1.5-onnx-int8-cls512-v2`**. | `test_bge_preprocessing_parity_and_version_isolation` + `verify_bge_preprocessing_parity()` |
| **A4** | Reaction vs Executable Label Split (`outcomes.py`, `event_study.py`, `historical_store.py`) | Default `PRE_EVENT_AS_OF` measured pre-event reaction, while `POST_DECISION_EXECUTABLE` did not account for pipeline latency, bid-ask spread crossing, or skipping off-hours prints to next session open. | Added `OutcomeLabelType` (`REACTION` vs `EXECUTABLE`), SQLite PK `(event_id, instrument_id, horizon_label, label_type)`, `pipeline_latency_ms` delay gating, `executable_side` ask/bid crossing, `entry_spread_bps_cost`, active-session filtering (`NEXT_SESSION_OPEN`), and `compute_reaction_and_executable_pair()`. | `test_reaction_vs_executable_spread_latency_and_pre_event_beta` |
| **A5** | Benchmark-Adjusted Integrity (`event_study.py`, `outcomes.py`) | Default `benchmark_beta = 1.0` presented `R - 1.0 * R_bench` without estimating beta or distinguishing simple market adjustment. | Replaced silent `1.0` default with `BenchmarkAdjustmentMethod`: strictly pre-event rolling OLS beta (`PRE_EVENT_ROLLING_BETA`, `timestamp_utc <= start_obs.timestamp_utc`, `beta_min_samples=10`) with transparent fallback to explicitly labeled `SIMPLE_MARKET_ADJUSTED` (`estimated_beta=None`) when pre-event observations are scarce. | `test_reaction_vs_executable_spread_latency_and_pre_event_beta` |
| **A6** | Legacy Context Isolation (`historical_intelligence.py`) | Needed an explicit barrier preventing Phase 5 trainers from accidentally reading Phase 3's in-memory `EventRetrievalContext`. | Added `historical_context_source="PHASE_4_PERSISTENT_MATURITY_VERIFIED"`, bumped pipeline to `4.1.0-alpha`, and added `assert_valid_phase4_training_input()` rejecting legacy Phase 3 `EventRetrievalContext` with `TypeError` and auditing future/maturity invariants. | `test_end_to_end_historical_intelligence_pipeline_and_label_separation` |
| **A7** | Documentation Reconciliation | `PHASE_4_REPORT.md` still showed PR #2 `"open"` at creation time; `PHASE_4_EVALUATION_REPORT.md` cited pre-remediation v1 numbers. | Updated `PHASE_4_REPORT.md` (confirming PR #2 normal merge `66b9328`), `PHASE_4_DATA_LIMITATIONS.md`, `PHASE_4_EVALUATION_REPORT.md` (re-benchmarked on `cls512-v2`), `PHASE_GATES.md`, and created this remediation record. | Offline documentation audit |

---

## 3. Empirical BGE-Small Preprocessing Parity Verification (`A3`)

Measured locally on CPU against cached `Xenova/bge-small-en-v1.5` (`ea104dacec62c0de699686887e3f920caeb4f3e3`):
- **Special Tokens & Truncation:** `[CLS] = 101`, `[SEP] = 102`, `[PAD] = 0`, 600-word test string cleanly truncated at `max_length = 512`.
- **CLS vs Mean Pooling Divergence:** Document cosine between CLS and attention-masked Mean pooling on the same financial headline = **`0.953665`** (meaning pooling choice shifts similarity scores meaningfully and must remain explicitly versioned).
- **L2 Normalization & Quantization Batch Stability:** `||v_doc|| = 1.000000`; single vs padded batch (`120` words) cosine similarity = **`0.994932`** under INT8 dynamic quantization.
- **Full Benchmark Re-Evaluation (`benchmark_historical_results.json`, `2026-11-11T05:14:52Z`):**
  - `3B_DENSE_ONNX_BGE_SMALL` (`bge-small-en-v1.5-onnx-int8-cls512-v2`): `P@5=0.3600`, `R@5=1.0000`, `MRR=1.0000`, `nDCG@5=0.9830`.
  - `4B_HYBRID_ONNX_BGE_SMALL`: `P@5=0.3333`, `R@5=0.9445`, `MRR=1.0000`, `nDCG@5=0.9597`.
  - Event-study invariance checks: **6/6 PASS**.
