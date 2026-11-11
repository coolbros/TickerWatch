# Phase 6 Operational Runbook (`PHASE_6_OPERATIONAL_RUNBOOK`)

**System Version:** `6.0.0-alpha` (Paper Shadow Observation Only — NO Live Order Placement)

---

## 1. Environment & Pre-Flight Checks

1. Verify Python `.venv` dependencies (`numpy`, `onnxruntime`, `httpx`, `feedparser`, `pydantic`, `fastapi`, `psutil`, `tzdata`).
2. Confirm broker/vendor credentials remain unset if operating in news-only shadow collection mode, or export authorized read-only credentials (`KITE_API_KEY`, `KITE_ACCESS_TOKEN`, `SEC_USER_AGENT`) via `.env` / OS vault (never print raw secrets in logs or CLI scripts).
3. Run repository offline + opt-in checks:
   ```powershell
   .\.venv\Scripts\python.exe -m pytest -v
   .\.venv\Scripts\python.exe -m pytest -v -m live
   ```

---

## 2. Daily Operational Monitoring & Telemetry Workflow

1. **Generate Daily Health & Drift Report:**
   ```powershell
   .\.venv\Scripts\python.exe scripts/alpha_shadow_report.py --output-json docs/alpha/daily_health_sample.json
   ```
2. **Verify Key Operational Signals:**
   - **News Health:** Confirm `rbi_press_rss`, `sec_edgar_live`, and `sec_news_press_live` poll cleanly; verify `missing_publication_ts_count == 0` and confirm `nse_corporate` / `bse_notices` remain cleanly disabled until authorized.
   - **Market-Data Health:** Inspect `sequence_gap_count`, `stale_feed_incidents`, `queue_overflow_drops`, and `resync_requests` whenever a WebSocket subscription is connected.
   - **Ledger Integrity & Parity:** Confirm `ShadowSignalLedger.verify_integrity(prediction_id)` returns `True` and re-run periodic `LiveReplayParityVerifier` checks verifying `0.0` feature vector divergence.

---

## 3. Enabling a Live Indian Market Feed (When Authorized Credentials Are Acquired)

1. Provision authorized read-only Zerodha Kite Connect API subscription (`KITE_API_KEY` + daily rotated `KITE_ACCESS_TOKEN`) or commercial TrueData subscription.
2. Supply current exchange derivative instrument ledger (`ContractMetadataEntry`) into `LiveInstrumentUniverse` before session open so lot sizes, expiry dates, and contract tokens match exchange truth without calendar-derived guesses.
3. Keep `ModelValidationStatus == MODEL_ELIGIBLE` disabled (`NO_ELIGIBLE_MODEL`) while collecting real feature snapshots + matured outcomes across **at least 20–30 market sessions** before fitting and auditing any real-data Phase 5 model.
4. **Incident Handling:** Never manually `UPDATE` or `DELETE` rows in `alpha_live_capture.db` (`live_news_revisions` / `live_market_snapshots`) or `alpha_shadow_ledger.db` (`shadow_predictions` / `shadow_outcomes`); triggers abort mutations cleanly to protect audit provenance.
