# TickerWatch Alpha — Phase 2 Event-to-Instrument Mapping & Instrument Master Specification

> **Document Version:** 1.0.0-alpha  
> **Status:** APPROVED BASELINE — PHASE 2  
> **Authors:** Quantitative Engineering & Research Architecture  
> **Scope:** Dynamic Entity-to-Instrument Resolution, Instrument Master, and Multi-Tier Contextual Mapping

---

## 1. Research Problem & Multi-Tier Mapping Thesis

Phase 0 provisionally selected NIFTY 50 futures (`NIFTY_FUT`) as the benchmark instrument. However, financial news events do not interact with the market uniformly:
- **Macroeconomic Events:** A surprise in RBI Repo Rate, CPI inflation, or GDP growth influences the aggregate market, directly affecting `NIFTY_FUT` and sector index futures (`BANKNIFTY_FUT`).
- **Single-Company Corporate Earnings:** An earnings surprise announced by Reliance Industries primarily and directly impacts Reliance cash equity (`RELIANCE.NS`) and single-stock futures (`RELIANCE_FUT`). Its transmission to `NIFTY_FUT` is moderated by its index weight ($\approx 9.5\%$), current index sentiment, and sector-wide implications.
- **Major Corporate Orders / Contract Wins:** A massive capital expenditure or defense order awarded to a specific entity drives localized order-flow in that specific company's equity and derivatives, rarely causing statistically distinguishable movements in NIFTY 50 futures unless the contract is systemic.

**Principle:** We must not blindly map every corporate announcement to `NIFTY_FUT`. Every event must resolve to a structured portfolio of affected instruments categorized by explicit causal relevance tiers.

---

## 2. Event Relevance Taxonomy

When an event arrives, the system resolves it into one or more candidate instruments classified into four distinct operational tiers:

```
+==================================================================================================+
|                                    EVENT-TO-INSTRUMENT RELEVANCE TIERS                            |
+==================================================================================================+

  [ EVENT ARRIVAL ]
         |
         +---> [ DIRECT ]      Primary instrument issued by or directly bound to the corporate entity.
         |                     (e.g., RELIANCE.NS cash equity, RELIANCE_FUT single-stock future)
         |
         +---> [ CONTEXTUAL ]  Macro/Index instrument acting as systemic market backdrop or hedge.
         |                     (e.g., NIFTY_FUT, BANKNIFTY_FUT, INDIA_VIX)
         |
         +---> [ INDIRECT ]    Peer firm or sector-correlated instrument affected via sympathy flows.
         |                     (e.g., TCS.NS when INFY.NS reports earnings; ICICIBANK.NS for HDFCBANK.NS)
         |
         +---> [ NOT_RELEVANT] Disconnected instrument; explicitly pruned from feature alignment.
```

### Definition of Tiers:
1. **DIRECT:** The instrument where primary price discovery and direct order-flow impact are expected.
   - For corporate earnings / order wins: The underlying cash equity (`EQ`) and active single-stock futures contract (`FUTSTK`).
   - For macroeconomic announcements: The benchmark index futures (`NIFTY_FUT`, `BANKNIFTY_FUT`).
2. **CONTEXTUAL:** Market-wide or sector benchmarks required to isolate idiosyncratic event alpha from beta market drift.
   - E.g., `NIFTY_FUT` is recorded alongside `RELIANCE.NS` so the predictive model can compute market-adjusted abnormal returns:
     $$R_{\text{abnormal}}(t, t+h) = R_{\text{stock}}(t, t+h) - \beta \cdot R_{\text{index}}(t, t+h)$$
3. **INDIRECT:** Peer or supply-chain instruments where secondary spillover occurs (deferred to Phase 10 multi-asset expansion).
4. **NOT_RELEVANT:** Instruments with zero structural or economic transmission.

---

## 3. Re-evaluation of `RELIANCE_FUT` & Initial Universe Selection

In Phase 0, `RELIANCE_FUT` was recorded as an uncommitted candidate. We now formally evaluate its contract specifications and suitability:

### Suitability Evaluation: `RELIANCE_FUT`
- **Exchange & Segment:** National Stock Exchange of India (NSE), Futures & Options Segment (`FUTSTK`).
- **Underlying:** Reliance Industries Limited (Cash Equity `RELIANCE`).
- **Liquidity & Depth:** Consistently the highest or second-highest volume single-stock future on NSE, with tight bid-ask spreads (typically 1 tick = ₹0.05), making it ideal for sub-second microstructure research without excessive slippage distortion.
- **Lot Size:** 250 shares.
- **Tick Size:** ₹0.05.
- **Expiry Cycle:** Monthly contracts expiring on the last Thursday of each month (or preceding trading day if Thursday is a holiday).
- **Rollover Window:** Active liquidity shifts to next-month contract during the final 3 trading days of the expiry week.
- **Conclusion:** `RELIANCE_FUT` is **APPROVED** as the primary single-stock future candidate for single-stock earnings and corporate order research.

### Initial Research Universe (Phase 2):
To maintain extreme engineering discipline, the initial live universe is strictly bounded to 5 core instruments across cash and derivatives:

| Canonical ID | Exchange | Segment | Underlying | Instrument Type | Lot Size | Tick Size | Role in Research |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `NSE:NIFTY_FUT:CURRENT` | NSE | FUTIDX | NIFTY | Index Future | 25 | 0.05 INR | Primary Macro Benchmark & Secondary Context |
| `NSE:BANKNIFTY_FUT:CURRENT` | NSE | FUTIDX | BANKNIFTY | Index Future | 15 | 0.05 INR | Banking Sector Macro Context |
| `NSE:RELIANCE:EQ` | NSE | EQ | RELIANCE | Cash Equity | 1 | 0.05 INR | Primary Single-Stock Cash Equity |
| `NSE:RELIANCE_FUT:CURRENT` | NSE | FUTSTK | RELIANCE | Stock Future | 250 | 0.05 INR | Primary Single-Stock Derivatives |
| `NSE:INFY:EQ` | NSE | EQ | INFY | Cash Equity | 1 | 0.05 INR | IT Sector Single-Stock Cash Benchmark |

---

## 4. Instrument Master Schema & Specification

Every financial instrument tracked by TickerWatch Alpha must be registered in the **Instrument Master**.

### Field Specifications:
- `instrument_id` (str): Unique canonical key, e.g. `NSE:NIFTY_FUT:20261126`.
- `exchange` (str): Trading venue (`NSE`, `BSE`).
- `segment` (str): Market segment (`EQ`, `FUTIDX`, `FUTSTK`, `OPTIDX`, `OPTSTK`).
- `symbol` (str): Exchange trading symbol (e.g. `NIFTY`, `RELIANCE`).
- `underlying` (str): Underlying asset identifier.
- `contract_id` (str): Venue-specific numeric or alphanumeric token (e.g. Kite instrument token `256265`).
- `instrument_type` (str): `INDEX_FUTURE`, `STOCK_FUTURE`, `EQUITY_CASH`, `INDEX_CASH`.
- `expiry_date` (date | null): Expiration date for derivatives (`YYYY-MM-DD`); `null` for cash equities.
- `strike_price` (Decimal | null): Strike price for options; `null` for futures and cash.
- `tick_size` (Decimal): Minimum price increment (e.g. `0.05`).
- `lot_size` (int): Number of shares per contract unit (e.g. 25 for NIFTY, 1 for cash).
- `quote_currency` (str): Currency ISO code (`INR`).
- `trading_hours` (dict): Session opening and closing times in Asia/Kolkata (IST):
  - Normal Trading: `09:15:00` to `15:30:00`.
  - Pre-Open Session: `09:00:00` to `09:08:00`.
- `source_metadata` (str): Provenance of contract specifications (e.g. `NSE_SECURITY_MASTER_202611`, `STATIC_VERIFIED`).
- `verification_status` (str): `VERIFIED_SPEC`, `PROVISIONAL`, `EXPIRED`.

---

## 5. Event-to-Instrument Mapping Rules Matrix

```
+---------------------------+-----------------------------------+-----------------------------------+
| Event Family              | Primary Target (DIRECT)           | Benchmark Context (CONTEXTUAL)    |
+---------------------------+-----------------------------------+-----------------------------------+
| Macro: Monetary Policy    | NSE:NIFTY_FUT:CURRENT             | NSE:BANKNIFTY_FUT:CURRENT         |
| Macro: CPI / GDP Data     | NSE:NIFTY_FUT:CURRENT             | None                              |
| Corporate: Earnings (RIL) | NSE:RELIANCE:EQ, NSE:RELIANCE_FUT | NSE:NIFTY_FUT:CURRENT             |
| Corporate: Order Win(RIL) | NSE:RELIANCE:EQ                   | NSE:RELIANCE_FUT:CURRENT          |
| Corporate: Earnings (INFY)| NSE:INFY:EQ                       | NSE:NIFTY_FUT:CURRENT             |
+---------------------------+-----------------------------------+-----------------------------------+
```

### Extensibility & Contract Rollover Mechanics:
- When a futures contract approaches expiry (last Thursday of expiry month), the alignment engine automatically rolls `CURRENT` to the near-month contract upon maturity.
- Contract specifications are strictly validated against the instrument master; synthetic or fabricated contract identifiers are rejected at ingestion time.
