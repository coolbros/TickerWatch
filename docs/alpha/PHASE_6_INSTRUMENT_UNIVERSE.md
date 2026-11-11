# Phase 6 Live Market Instrument Universe & Dynamic Contract Resolution (`PHASE_6_INSTRUMENT_UNIVERSE`)

**Module:** [`backend/alpha/live_instruments.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/live_instruments.py) (`INSTRUMENT_MASTER_VERSION = "6.0.0-alpha"`)

---

## 1. Prioritized Research Universe

| Category | Prioritized Symbols | Role in Shadow Operation |
|---|---|---|
| **Index Futures (`FUTIDX`)** | `NSE:NIFTY_FUT:CURRENT`, `NSE:BANKNIFTY_FUT:CURRENT` | Primary macro/RBI response targets & systematic hedge benchmarks |
| **Core Corporate Cash Equities (`EQ`)** | `RELIANCE`, `TCS`, `INFY`, `HDFCBANK`, `ICICIBANK`, `LT` (+ configurable liquid NIFTY 50 names: `SBIN`, `BHARTIARTL`, `AXISBANK`, `KOTAKBANK`, `ITC`) | Direct corporate-disclosure impact measurement without derivative rollover noise |
| **Core Single-Stock Futures (`FUTSTK`)** | `NSE:{SYM}_FUT:CURRENT` matching above issuers | Leveraged directional futures contract specs (lot size, expiry, rollover transitions) |
| **Contextual Benchmarks (`INDEX`)** | `NSE:NIFTY:INDEX`, `NSE:BANKNIFTY:INDEX` | Read-only systematic market adjustment baseline (`CONTEXTUAL` tier) |

---

## 2. Non-Expired Contract Resolution & Rollover Rules

1. **Zero Hardcoded Expired Months:** Contracts are never statically pinned to dead monthly codes. When exchange instrument dumps (`ContractMetadataEntry` ledgers) are registered, `resolve_active_contract()` selects the earliest non-expired contract (`expiry_date >= as_of_date`), raising `ValueError` if only expired entries remain. When no vendor dump is present yet, calendar-derived contracts are tagged explicitly `CALENDAR_DERIVED_PROVISIONAL` (`VerificationStatus.PROVISIONAL`).
2. **Pre-Rollover Transition Window:** Within `rollover_days_before_expiry = 2` days of expiry, near-month status flips from `ACTIVE` to `PRE_ROLLOVER` while surfacing `rollover_target_symbol` and `rollover_target_expiry`.
3. **Separated Mapping Tiers:** `map_event_separated()` isolates `cash_equity`, `single_stock_futures`, `index_futures`, and `contextual_benchmarks`. Corporate disclosures map `DIRECT` strictly onto the affected issuer; macroeconomic/RBI disclosures map `DIRECT` onto `NIFTY_FUT` and contextual banking futures.
