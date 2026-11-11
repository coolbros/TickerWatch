# Phase 5 Data Acquisition & Statistical Power Requirements (`PHASE_5_DATA_REQUIREMENTS`)

**Project:** TickerWatch Alpha  
**Phase:** Phase 5 — Work Package B  
**Document Version:** 1.0.0-alpha  
**Date:** 2026-11-11  
**Author:** Agastya Varshney (`agastyavarshney@gmail.com`)  
**Status:** Canonical Data Acquisition & Quality Standard

---

## 1. Purpose

This document formalizes the authorized dataset acquisition targets, provenance schemas, timestamp semantics, and sample-size requirements that must be satisfied before any Phase 5 predictive model can claim empirical out-of-sample alpha ($P(\Delta F_{t+h} \mid E_t, H_t, M_t) > P(\Delta F_{t+h} \mid M_t)$).

---

## 2. Minimum Dataset Targets Prior to Empirical Validation

### 2.1 Primary Research Market: Indian Equities & NIFTY Futures (`IN`)
- **Corporate Disclosures ($\ge 2$ Years, Target $\ge 5,000$ Events):**
  - Authorized historical NSE/BSE announcements across **NIFTY 50** (and liquid NIFTY Next 50 single-stock futures) constituents covering earnings, guidance updates, major contract wins, capex/M&A, and regulatory orders.
  - Must retain full text / parsed PDF body content (not headline-only boilerplate) plus original exchange dissemination timestamps in `Asia/Kolkata`.
- **Macroeconomic Releases (Target $\ge 150$ Releases):**
  - Official RBI Monetary Policy Committee (MPC) rate/stance resolutions and MOSPI CPI/GDP/IIP press releases with exact dissemination timestamps (`Asia/Kolkata`).
- **Authorized Market History (1-Minute OHLCV / L1 + Selective L2):**
  - Continuous regular-session (`09:15–15:30 IST`) 1-minute bars and Level 1 bid/ask quotes for all mapped equity tickers, active single-stock futures, and `NSE:NIFTY_FUT:CURRENT` benchmark, plus authorized L2 depth snapshots where microstructural slippage modeling is evaluated.
- **Adjustment & Derivative Ledgers:**
  - Verified corporate action factors (`split`, `bonus`, `rights`, `special dividend` multipliers per ex-date).
  - Exact futures `contract_id` labels (`NIFTYyyMMMFUT`) and audited `FuturesRolloverSpec` spread/ratio schedules across monthly expiry rolls.
- **Consensus Expectations (Optional / Explicit Missing Handling):**
  - Where licensed (e.g., FactSet/Bloomberg/broker consensus), store strict `captured_at_utc < decision_timestamp`; absent estimates remain explicitly `UNAVAILABLE_EXPECTATION` and never default to `0` surprise.

### 2.2 Secondary Research Market: US Equities (`US` — Strictly Isolated)
- Full HTML/XBRL Item extraction (especially Item 2.02 Earnings, Item 1.01 Material Agreements, Item 7.01 Regulation FD) for historical `SEC EDGAR Form 8-K`, `10-Q`, and `10-K` filings beyond Form 4 insider logs.
- Matching SIP/consolidated 1-minute NBBO market history with `America/New_York` (EST/EDT DST-aware) session calendars, excluding US market results from generalizing onto Indian equity alpha claims.

---

## 3. Mandatory Causal & Provenance Constraints on Imported Data

1. **Timezone Fidelity (WP-A `A1`):** Never infer UTC on naive exchange timestamps. Preserve raw strings and timezone interpretation inside `_timestamp_provenance`.
2. **Receipt Time Honesty:** Historical archives compressed into batch backfill runs (such as `2026-11-08T09:34Z` in `tickerwatch.db`) MUST be flagged as archival/backfill timestamps so millisecond `POST_DECISION_EXECUTABLE` latency models do not assume false zero-latency receipt if actual wire receipt telemetry is missing.
3. **Bitemporal Revisions (`A2`):** Corrections and filing amendments (`8-K/A`, exchange clarifications) must be appended as immutable revisions rather than overwriting prior revisions, preserving as-of visibility.
4. **Statistical Power Caveat:** Meeting count targets ($\ge 5,000$ events across $\ge 2$ years) is an initial baseline—sparse sub-strata (e.g., regulatory penalties or specific shock magnitudes across only 2 years of bull/bear markets) still require purged walk-forward stability verification and block-bootstrap confidence intervals before rejecting null hypotheses.

---

## 4. Legal & Ethical Acquisition Guardrails

- Zero unauthorized scraping, CAPTCHA/WAF circumvention, or ToS bypasses.
- Zero unauthorized purchases without explicit user/project authorization.
- All synthetic development datasets strictly tagged `is_synthetic = True` and permanently blocked from empirical performance evaluation (`leakage_audit.py`).
