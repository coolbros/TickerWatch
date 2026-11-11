# TickerWatch Alpha — Phase 5 Empirical Data Limitations (`PHASE_5_DATA_LIMITATIONS`)

**Audit Date:** 2026-11-11  
**Audit Engine:** `backend/alpha/data_readiness.py` (`RealDataReadinessAuditor`)  
**Overall Empirical Modeling Status:** **`BLOCKED`** (`IN: BLOCKED` | `US: INSUFFICIENT_DATA`)

---

## 1. Quantitative Summary of Observed Empirical Gaps

Auditing `tickerwatch.db`, `data/alpha_historical_store.db`, and `data/` strictly separates real ingested records from synthetic fixtures (`is_synthetic=True`):

| Dimension | Empirical Reality Observed | Minimum Institutional Requirement | Impact on Phase 5 |
|---|---|---|---|
| **Indian Corporate Disclosures (`IN`)** | **4** general Market Wrap articles (`moneycontrol_market_reports`); **0** NSE/BSE company filings (`0.00 yr` span) | $\ge 5,000$ NSE/BSE primary corporate filings spanning $\ge 2.0$ years | **`BLOCKED`** — Zero empirical Indian company-event training set |
| **Indian Macro Releases** | **0** RBI/MOSPI structured macro releases | $\ge 150$ timestamped macro releases | **`BLOCKED`** |
| **US SEC Filings (`US`)** | **3,038** real records (`3,013` `sec_edgar` on `AAPL`, `AMZN`, `MSFT`; `25` `sec_news_press`) spanning 11.09 yrs (`2015-10-05` to `2026-11-06`), incl. **80** `/A` amendments | Full parsed HTML filing bodies (Item 2.02 / 1.01), diverse issuer universe | `INSUFFICIENT_DATA` — **3,041 / 3,042** real records classify as `ROUTINE_ADMINISTRATIVE` (`1,841` Form 4 insider logs, `308` Form 144, header-only metadata) |
| **Real Market Data (`M_t`)** | **0** valid real market observations (only 2 synthetic benchmark files present) | Historical 1-minute OHLCV + L1 bid/ask quotes aligned to every filing timestamp | **`BLOCKED`** — No pre-event microstructure features $M_t$ on real filings |
| **Matured Real Outcome Labels ($\Delta F_{t+h}$)** | **0** matured real labels across `1m`–`5d` | Validated `REACTION` & `EXECUTABLE` labels with corporate action + futures rollover ledgers | **`BLOCKED`** — Zero empirical regression/classification targets |
| **Point-in-Time Consensus Estimates ($E$)** | **0 (0.0%)** | Historical consensus EPS/Revenue forecasts with explicit `as_of <= decision_ts` provenance | Numerical surprise (`e_surprise_*`) falls back cleanly to `e_surprise_missing = 1.0` |
| **Arrival Timestamp Fidelity (`observed_at_utc`)** | All **3,042** real records fall inside a **1.52-second batch backfill window** (`2026-11-08T09:34:42.13Z` to `09:34:43.65Z`) | True real-time wire/exchange arrival timestamps | Prohibits latency-sensitive wire-arrival modeling from backfill timestamps (`observed_timestamps_are_batch_backfill = True`) |
| **Persistent Historical Store (`historical_events`)** | **0** real indexed events; **1** synthetic test record excluded from real counts | Clean populated SQLite store with revision lineage & embeddings | Requires backfill pipeline execution once real filing bodies + market prices exist |

---

## 2. Scientific Policy Under `BLOCKED` Empirical Data

1. **Zero Fabrication Policy:** Missing filings, missing ticks, missing consensus estimates, and backfill-compressed arrival timestamps are documented transparently rather than patched with forward look-ahead prices or synthetic proxies labeled as real data.
2. **Dual Verification Regime:** Phase 5 code correctness, leakage immunity, walk-forward calibration, Holm-Bonferroni attribution math, and cost-aware backtesting are verified end-to-end on explicitly tagged `is_synthetic=True` datasets (`suite_a_synthetic_multimodal` and `suite_b_synthetic_zero_signal_null`).
3. **Path to `READY`:** See `docs/alpha/PHASE_5_DATA_REQUIREMENTS.md` for exact vendor/exchange ingestion milestones required to unlock empirical training for `IN` (`NIFTY_FUT` + NSE/BSE announcements) and `US` (`AAPL`/`MSFT`/`AMZN`+ full 8-K bodies).
