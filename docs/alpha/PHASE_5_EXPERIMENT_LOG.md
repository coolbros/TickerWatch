# TickerWatch Alpha — Phase 5 Experiment Registry & Governance Log

> **Governance Standard:** Every predictive training run—whether exploratory synthetic verification or future real-market training—must log immutable lineage, feature/target schema hashes, walk-forward boundary timestamps, leakage audit pass/fail flags, software versions, synthetic/real provenance (`is_synthetic`), and explicit known limitations into `LocalModelRegistry` (`backend/alpha/model_registry.py`).

---

## 1. Required Lineage Fields per Registered Run

| Field | Purpose |
|---|---|
| `model_id` / `model_version` | Unique experiment identifier and semantic tag |
| `algorithm` / `model_config_name` | Estimator (`GBDT`, `RIDGE`, etc.) & ablation slice (`MODEL_A`..`MODEL_E`) |
| `feature_schema_version` & `feature_manifest_sha256` | Exact ordered feature contract verification |
| `target_schema_version` & `dataset_manifest_hash` | Exact ordered event IDs & decision timestamps |
| `training_cutoff_utc`, `validation_period`, `test_period` | Chronological split boundary proof |
| `software_environment` & `git_commit_hash` | Python, NumPy, Pydantic, OS, and repo SHA |
| `leakage_audit_passed` & `is_synthetic` | Non-negotiable gate and synthetic vs real segregation |
| `artifact_sha256` | Tamper-evident hash checked prior to deserializing any bundle |
| `known_limitations` | Explicit documented caveats blocking unwarranted live promotion |

---

## 2. Recorded Experiment Series

### Run Series 001 — Real-Data Readiness Gate (Live Workspace DB)
- **Date:** 2026-11-11
- **Command:** `RealDataReadinessAuditor` against `data/tickerwatch.db`
- **Result:** `overall_empirical_training_status = BLOCKED` (`3,042` real news records ingested inside a 1.5s backfill window; `0` real market bars; `0` matured real outcome labels; `0` Indian company filings). Zero real-market models registered or promoted.

### Run Series 002 — Controlled Synthetic Verification & Noise Null Check
- Populated automatically upon running `scripts/alpha_phase5_experiment.py` (see `docs/alpha/PHASE_5_EXPERIMENT_RESULTS.md` and `docs/alpha/benchmark_phase5_synthetic_results.json`). Every run produced by this harness is permanently tagged `is_synthetic=True` and is barred from real-capital deployment decisions.
