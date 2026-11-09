# TickerWatch Alpha — Initial Instrument Selection & Feasibility Matrix

> **Document Version:** 1.0.0-alpha  
> **Status:** PROVISIONAL DECISION — PHASE 0  
> **Author:** Quantitative Infrastructure & Market Architecture  
> **Target Market:** Indian Financial Markets (NSE / NSE IX)  

---

## 1. Objective and Mandate

The objective of Work Package B is to identify **exactly one** provisional financial instrument for the initial phase of predictive AI research. 

Selecting a large or heterogeneous universe prematurely introduces unmanageable data engineering complexity, confounding cross-asset variables, and divergent liquidity regimes. Conversely, choosing an instrument with opaque data feeds, prohibitive transaction costs, or legally ambiguous access would invalidate research conclusions.

### Core Principles:
1. **No Assumed Eligibility:** We do not assume that the user or system has legal authorization, broker permissions, or regulatory clearance to trade any given contract.
2. **No Assumed Data Feeds:** We do not assume that exchange tick feeds or historical L2 books are available unless an authorized, verified provider is confirmed.
3. **Evidence-Based Selection:** Every candidate specification is classified into one of four empirical verification states:
   - `[VERIFIED]`: Confirmed against official exchange circulars, regulatory guidelines, or active data samples.
   - `[REQUIRES EXTERNAL VERIFICATION]`: Plausible based on industry standard practices but unverified in our environment.
   - `[UNAVAILABLE]`: Confirmed absent or inaccessible.
   - `[UNKNOWN]`: Data, rules, or access requirements are currently uncatalogued.

---

## 2. Candidate Instruments Evaluated

We evaluate three distinct instrument categories in the Indian financial marketplace:

1. **Candidate A: NSE NIFTY 50 Index Futures (`NIFTY_FUT`)**  
   The benchmark Indian equity index futures contract traded on the National Stock Exchange of India (NSE). Cash-settled, high systemic liquidity, broad macroeconomic and basket news sensitivity.
2. **Candidate B: High-Beta, Liquid Single-Stock Equity Futures (Prototypical: `RELIANCE_FUT`)**  
   Single-stock futures traded on NSE (e.g., Reliance Industries Ltd, HDFC Bank Ltd, ICICI Bank Ltd, Infosys Ltd). Physically settled upon expiry, high idiosyncratic corporate disclosure sensitivity (earnings releases, contract wins, regulatory notices).
3. **Candidate C: GIFT Nifty 50 Futures (`GIFT_NIFTY_FUT`)**  
   US Dollar-denominated Nifty futures traded on NSE IX (International Exchange) in GIFT City, Gandhinagar, Gujarat. Nearly 21-hour trading window spanning Asian, European, and US market sessions.

---

## 3. Instrument Selection Matrix (11 Evaluation Criteria)

| # | Evaluation Criterion | Candidate A: NSE NIFTY 50 Futures (`NIFTY_FUT`) | Candidate B: Liquid Single-Stock Futures (`RELIANCE_FUT`) | Candidate C: GIFT Nifty Futures (`GIFT_NIFTY_FUT`) |
|---|---|---|---|---|
| **1** | **Liquidity (ADV & Open Interest)** | `[VERIFIED]` Daily turnover $> \$1.5\text{B}$ USD equivalent; OI $> 200,000$ contracts. World's most actively traded index derivatives. | `[VERIFIED]` Daily turnover $\$80\text{M} - \$200\text{M}$ USD; deep domestic institutional participation. | `[VERIFIED]` Daily turnover $\$1.5\text{B} - \$2.5\text{B}$ USD; active institutional offshore liquidity. |
| **2** | **Historical Market Data Availability** | `[VERIFIED]` Readily available from authorised NSE data vendors (DotEx, TrueData, GlobalDataFeeds) at 1-minute and tick levels. | `[VERIFIED]` Readily available from authorised NSE vendors for top 20 constituents at 1-minute and tick levels. | `[REQUIRES EXTERNAL VERIFICATION]` Commercial historical tick archives for NSE IX are fragmented; vendor coverage unclear. |
| **3** | **Real-Time Market Data Access** | `[VERIFIED]` Multi-vendor broker APIs (Zerodha Kite, Interactive Brokers India, AngelOne) provide L1/L2 snapshots via WebSocket. | `[VERIFIED]` Identical broker WebSocket feeds provide L1/L2 updates during regular NSE trading hours. | `[REQUIRES EXTERNAL VERIFICATION]` Requires specific offshore broker / clearing member feeds or specialised market-data vendors. |
| **4** | **Timestamp Precision** | `[VERIFIED]` Vendor feeds provide millisecond timestamps; native exchange matching engine provides nanosecond/microsecond order matching. | `[VERIFIED]` Identical exchange matching engine timestamps forwarded by commercial tick feeds. | `[UNKNOWN]` Precision and feed latency from GIFT City co-location to external recipients unverified. |
| **5** | **Bid–Ask Spread** | `[VERIFIED]` Extremely tight: typically 1 tick (0.05 index points $\approx 0.0002\%$). Near-zero spread friction at top-of-book. | `[VERIFIED]` Relatively tight: 1–2 ticks ($0.05 - 0.10$ INR $\approx 0.002\% - 0.004\%$) during normal market hours. | `[REQUIRES EXTERNAL VERIFICATION]` Varies significantly by session; tight during domestic hours, wider during late US/early Asian sessions. |
| **6** | **Market Depth Availability** | `[VERIFIED]` Top 5 bids/asks standard on broker APIs; full 20-depth or tick-by-tick available on premium commercial feeds. | `[VERIFIED]` Top 5 standard; depth thins rapidly outside top 3 price levels during non-peak hours. | `[REQUIRES EXTERNAL VERIFICATION]` L2 depth on retail/research feeds unverified; institutional access required. |
| **7** | **Transaction Costs & Statutory Levies** | `[VERIFIED]` STT: 0.02% on sell side (raised in Union Budget 2024); exchange fee $\approx 0.00173\%$; GST 18% on fees; stamp duty 0.002% on buy side. | `[VERIFIED]` STT: 0.02% on sell side; exchange charges $\approx 0.00325\%$; stamp duty 0.002% on buy side; GST 18%. | `[VERIFIED]` Zero STT, Zero CTT, zero capital gains tax for offshore entities in IFSC zone; exchange fees apply. |
| **8** | **Contract Expiry & Settlement Complexity** | `[VERIFIED]` **Cash-settled** in INR against underlying index closing level. Monthly and weekly expiries. Clean rollover mechanics. | `[VERIFIED]` **Compulsory Physical Delivery** upon expiry (SEBI mandate). Requires square-off or delivery margins in final expiry week. | `[VERIFIED]` **Cash-settled** in USD against NSE Nifty 50 underlying index settlement price. Monthly contracts. |
| **9** | **Trading Hours** | `[VERIFIED]` 09:15 to 15:30 IST (Monday–Friday). News arriving between 15:30 and 09:15 cannot be traded until next market open. | `[VERIFIED]` 09:15 to 15:30 IST. Same overnight gap risk. | `[VERIFIED]` Extended: Session 1 (06:30–15:45 IST) & Session 2 (16:35–02:45 IST). Captures after-market disclosures. |
| **10** | **Broker / Legal Access Eligibility** | `[VERIFIED]` Open to resident Indian entities and registered FPIs via SEBI-registered brokers. Standard retail/institutional onboarding. | `[VERIFIED]` Open to resident Indian entities and registered FPIs under identical regulatory framework. | `[REQUIRES EXTERNAL VERIFICATION]` Resident Indians restricted under RBI LRS rules from speculative derivatives in IFSC; primarily for non-residents and FPIs. |
| **11** | **News Data Alignment Suitability** | `[REQUIRES EXTERNAL VERIFICATION]` Macro/political news affects index, but causality is diffused across 50 constituents. High signal-to-noise ratio. | `[VERIFIED]` Direct causal linkage: company-specific corporate announcements (filings, earnings) directly affect the specific underlying stock. | `[REQUIRES EXTERNAL VERIFICATION]` Captures global overnight news, but requires international wire synchronisation. |

---

## 4. In-Depth Comparative Analysis

### 4.1 Index Futures vs. Single-Stock Futures: The Direct Causal Link
- **Single-Stock Futures (`RELIANCE_FUT`, `HDFCBANK_FUT`):**  
  Corporate disclosures (quarterly results, board meetings, acquisitions, order wins) map with high causal fidelity to single-stock price action. When Reliance Industries announces an oil-to-chemicals agreement or quarterly earnings beat, the economic impact is concentrated directly in `RELIANCE_FUT`. The research task of attributing price movement to specific news text is direct and verifiable.  
  *Limitation:* In the Indian market, SEBI rules mandate physical delivery for in-the-money equity stock derivatives remaining open at expiry. This imposes severe delivery margin escalation during the final 4–5 days of the contract cycle.

- **Index Futures (`NIFTY_FUT`):**  
  `NIFTY_FUT` is purely cash-settled, completely eliminating the physical delivery hazard. Its liquidity is the deepest in the Indian financial system, with spreads rarely exceeding a single tick.  
  *Limitation:* Macro announcements (RBI repo rate, Union Budget, CPI inflation) occur infrequently (monthly/quarterly). Company-specific news from an individual constituent (even heavyweights like Reliance at $\sim 9\%$ weight or HDFC Bank at $\sim 11\%$ weight) is significantly diluted by the remaining 49 stocks and general market beta.

### 4.2 The GIFT Nifty Paradox: Extended Hours vs. Eligibility Barriers
- **Advantages:** GIFT Nifty trades nearly 21 hours a day, overlapping with international news cycles and absorbing corporate earnings released post-15:30 IST. Furthermore, it is completely exempt from Securities Transaction Tax (STT) and stamp duty in the GIFT IFSC zone.
- **Disqualifying Barriers for Initial Phase:**  
  1. *Regulatory Eligibility:* Under current Reserve Bank of India (RBI) Liberalised Remittance Scheme (LRS) regulations, resident Indian individuals cannot utilize remittances to trade leveraged financial derivatives on IFSC exchanges. Only non-residents, Eligible Foreign Entities (EFEs), or registered Foreign Portfolio Investors (FPIs) can directly trade.
  2. *Data Availability:* Commercial research data feeds for NSE IX GIFT Nifty at the tick and historical L2 level are significantly more expensive and less widely integrated into retail research SDKs than standard domestic NSE feeds.

---

## 5. Decision & Recommendation

### 5.1 Provisional Research Instrument
We select as our single provisional research instrument:

> **Provisional Instrument:** **NSE NIFTY 50 Near-Month Index Futures (`NIFTY_FUT`)**  
> *Secondary Benchmark (for Single-Stock Idiosyncratic Studies):* **Reliance Industries Near-Month Futures (`RELIANCE_FUT`)**

### 5.2 Rationale
1. **Cash Settlement:** Zero risk of physical delivery complications, forced delivery margin calls, or auction penalties.
2. **Minimal Execution Friction:** The bid-ask spread on `NIFTY_FUT` is the tightest in India (1 tick), ensuring that backtested execution models do not suffer from synthetic spread estimation errors.
3. **Data Availability:** Authorised historical tick-level and minute-level data from domestic providers is affordable, verified, and accessible.
4. **Verified Legal Eligibility:** Standard domestic broking accounts can legally access and trade NSE derivatives under SEBI oversight.
5. **Research Prudence:** While single-stock futures (`RELIANCE_FUT`) offer cleaner idiosyncratic news mapping, their physical delivery requirement poses operational risks during expiry week. Therefore, `NIFTY_FUT` serves as the primary instrument for system infrastructure, macro news, and systemic event models, while `RELIANCE_FUT` is retained as an uncommitted provisional vehicle strictly for corporate earnings research, with automated trade closure 7 trading days prior to contract expiry.

---

## 6. Unresolved Questions & External Verifications Required

Before transition to Phase 2 (Market Data Feed Scaffolding), the following must be verified:
- [ ] **Data Vendor Selection:** Evaluate whether TrueData, GlobalDataFeeds, or a direct broker API (e.g. Zerodha Kite Connect WebSocket) provides the requisite timestamp precision (sub-second/millisecond) for research recording.
- [ ] **Contract Rollover Rules:** Define exact quantitative rollover thresholds (e.g., roll to the next-month contract on the Tuesday preceding the final Thursday expiry).
- [ ] **Margin Requirements:** Confirm maintenance and Span+Exposure margin requirements (typically $\sim 10-12\%$ of notional for Nifty) to calibrate backtest capital utilization.
