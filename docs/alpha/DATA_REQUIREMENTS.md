# TickerWatch Alpha — Data Requirements, Source Feasibility & Alignment Architecture

> **Document Version:** 1.0.0-alpha  
> **Status:** PHASE 3 COMPLETE — FINANCIAL NLP & EXPECTATION DATA REQUIREMENTS INTEGRATED  
> **Author:** Data Engineering & Quantitative Market Microstructure  
> **Mandate:** Rigorous Licensing, Push-Based Ingestion & Temporal Synchronization  

---

## 1. News Data Requirements

High-frequency predictive modeling requires clean, timestamp-accurate, and legally unencumbered news inputs. Unstructured text crawled casually from public search engines lacks the temporal resolution and legal durability necessary for quantitative research.

### 1.1 Minimum News Data Specifications
1. **Reliable Publication Timestamps ($t_{\text{pub}}$):** Must reflect the exact moment the corporate disclosure or news bulletin was published by the originator or regulatory exchange filing portal, with at least second-level precision.
2. **Provider Receipt Timestamps ($t_{\text{provider}}$):** For commercial news wires, the wire timestamp indicating when their gateway ingested the news release.
3. **Local Ingestion Timestamps ($t_{\text{local\_recv}}$):** High-resolution monotonic and UTC timestamps recorded at the local network socket interface upon receiving the first packet byte.
4. **Historical Archive Depth:** At least 3 to 5 years of point-in-time corporate announcements and earnings releases to permit robust, walk-forward cross-validation without temporal overfitting.
5. **Stable Unique Identifiers:** Immutable IDs (e.g., regulatory accession numbers such as SEC CIK-Accession or BSE/NSE Announcement IDs) to ensure deterministic idempotency.
6. **Raw Payload Retention:** Archival of the unmodified source document (HTML, JSON, XML, or PDF text) within bounded database tables (as implemented in `dead_letter_items` and `articles.raw_metadata` in Phase 1).
7. **Corrections & Retractions Tracking:** Explicit handling of amended filings, headline revisions, and errata disclosures without overwriting historical point-in-time observations.
8. **Entity Coverage:** Explicit mapping of corporate entities to exchange symbols, ISINs, and parent/subsidiary hierarchies.
9. **Licensing & Terms of Use:** Strict compliance with publisher terms of service, robots.txt, and commercial data redistribution agreements. Scraping password-protected portals or bypassing bot mitigation is strictly forbidden.

---

## 2. Market Data Requirements

To evaluate the predictive signal under realistic market microstructure friction, market data must provide granular order-book and trade flow visibility.

### 2.1 Minimum Market Data Specifications
1. **Historical Futures Prices:** Continuous, point-in-time tick or 1-second sampled quotes for `NIFTY_FUT` and liquid equity futures.
2. **Real-Time Streaming Feeds:** Low-latency push-based WebSocket or TCP direct feed delivering Level 1 (Top of Book) and Level 2 (5–20 depth levels).
3. **Continuous Bid and Ask:** Sub-millisecond quotes for best bid, best ask, bid size, and ask size.
4. **Market Depth:** Full queue depth across top 5 price tiers to enable realistic market-impact and order-fill modeling.
5. **Trade Data (Time & Sales):** Individual trade prints including trade price, trade volume, trade side (buyer-initiated vs. seller-initiated aggressor), and exchange matching engine timestamp.
6. **Trading Session Metadata:** Clear delineation of pre-market auction (09:00–09:15 IST), continuous trading (09:15–15:30 IST), block deal windows, and official market holidays.
7. **Contract Metadata:** Lot size (e.g., 25 or 50 for Nifty), tick size (0.05 index points), expiry date, margin multipliers, and settlement methodology.
8. **Timestamp Precision:** Millisecond or microsecond exchange matching engine timestamps ($t_{\text{exchange}}$) forwarded without vendor buffering.
9. **Licensing:** Authorised data distribution licenses from the National Stock Exchange of India (NSE) or authorized redistributors (e.g., DotEx / TrueData / GlobalDataFeeds).

---

## 3. News and Market Data Synchronization Methodology

Synchronizing unstructured text events with high-frequency market order books is the single most common source of synthetic alpha and look-ahead bias in financial AI literature.

```
+====================================================================================================+
|                                  SYNCHRONIZATION & ALIGNMENT TIMELINE                              |
+====================================================================================================+

   MARKET FEED STREAM:
   --[Tick 10:01:05.100]--[Tick 10:01:05.150]--[Tick 10:01:05.200]--[Tick 10:01:05.250]-------->
                                                                         ^
                                                                         | (Matched Quote State)
   NEWS INGESTION STREAM:                                                |
   [t_pub: 10:01:04.000]                                                 |
         \ (Wire Transit Delay ~1.15s)                                   |
          v                                                              |
   [t_local_recv: 10:01:05.150]                                          |
         \ (Parsing & Deduplication ~12ms)                               |
          v                                                              |
   [t_ingest: 10:01:05.162]                                              |
         \ (Feature Generation & NLP Inference ~38ms)                    |
          v                                                              |
   [t_pred: 10:01:05.200] -----------------------------------------------+
                                 (FORWARD EVALUATION WINDOW BEGINS HERE)
                                 Interval: [10:01:05.200 -> 10:01:10.200] (for h = 5s)
```

### 3.1 Strict Synchronization Protocols
1. **Search-Engine Indexing Times Are Prohibited:** Google News, Bing, or web search indexing timestamps reflect spider crawl intervals (often 15 minutes to 4 hours late). They MUST NEVER be treated as publication timestamps.
2. **Push-Based Feeds Preferred Over Polling:** Push-based WebSocket feeds or webhook streams are preferred. Polling intervals (e.g., polling RSS every 300 seconds) create artificial 0–300 second ingestion latencies that make sub-second market alignment impossible.
3. **The Local Monotonic Clock Anchor:** Both the market data buffer and the news ingestion engine are timestamped upon arrival by the identical physical system clock (`time.perf_counter_ns()`) synchronized via local NTP / PTP.
4. **The Anchor Quote:** When a news prediction is generated at $t_{\text{pred}}$, the baseline market state is defined as the *most recent valid order book snapshot with $t_{\text{market}} \le t_{\text{pred}}$*.
5. **No Negative Latency:** Any dataset instance where $t_{\text{market}} < t_{\text{pub}}$ is classified as an asynchronous data anomaly and quarantined.

---

## 4. Potential Data Source Evaluation Matrix

The following table catalogs potential data vendors and sources. **Access is NOT claimed for any source that has not been empirically integrated and verified in our codebase.**

| Provider / Source | Data Type | Coverage | Delivery Method | Timestamp Quality | Historical Availability | License / Cost Status | Integration Complexity | Verification Status | Outstanding Questions |
|---|---|---|---|---|---|---|---|---|---|
| **BSE / NSE Corporate Announcements** | News / Filings | All Indian listed companies (equities/derivatives) | RSS / Web scraping / Exchange API | Second-level ($t_{\text{pub}}$ exchange clock) | Multi-year on exchange portal | Free public regulatory disclosure; commercial redistribute requires license | Moderate (PDF parsing required) | `[REQUIRES EXTERNAL VERIFICATION]` | Does NSE provide a direct push JSON announcement API for non-members? |
| **Moneycontrol RSS Feeds** | Financial News | Indian markets, macroeconomic, corporate | Polling HTTP RSS / Atom | Second-level RFC 822 (often modified) | Limited (rolling 20–50 items) | Public RSS for personal use; commercial redistribution restricted | Low (Already integrated in Phase 1) | `[VERIFIED]` (Active in `feeds.yaml`) | What is the publication latency relative to official BSE/NSE stock filings? |
| **SEC EDGAR Submissions API** | US Filings (10-K, 10-Q, 8-K) | All US public equities | REST HTTP JSON (`data.sec.gov`) | Second-level UTC | Complete historical archives (1994–present) | Free public domain; requires User-Agent header and $< 10\text{ req/s}$ | Low (Already integrated in Phase 1) | `[VERIFIED]` (Active in `sec.py`) | Unsuitable for Indian derivatives research (US corporate focus only). |
| **TrueData Market Data** | Market Data (Ticks, L1/L2) | NSE Equities, Indices, Futures, Options | WebSocket API / Python SDK | Millisecond exchange timestamp | Available on paid subscription (5+ years) | Commercial subscription (INR 2,000–8,000/mo) | Moderate | `[REQUIRES EXTERNAL VERIFICATION]` | Does their standard WebSocket package include full Level 2 (5-depth) order book snapshots? |
| **GlobalDataFeeds (Nimble)** | Market Data (Ticks, L1/L2) | NSE, BSE, MCX derivatives | TCP / COM / WebSocket API | Millisecond exchange timestamp | Available on paid subscription | Commercial subscription | Moderate | `[REQUIRES EXTERNAL VERIFICATION]` | Latency profile on cloud Linux servers. |
| **Zerodha Kite Connect WebSocket** | Market Data (L1/L2 Quotes) | NSE / BSE Equities & Derivatives | WebSocket streaming | Millisecond timestamp | Historical 1-minute bars available; tick history not retained | Commercial broker API (INR 2,000/mo + INR 2,000 historical) | Low | `[REQUIRES EXTERNAL VERIFICATION]` | Is WebSocket rate sufficient for sub-second microstructure research without dropped packets? |
| **Reuters / Bloomberg Wires** | Ultra-Low-Latency News | Global macro, Indian corporate, earnings | Machine-Readable News (MRN) / Enterprise Feed | Sub-millisecond | Extensive institutional archives | Prohibitive ($>\$20,000/\text{year}$) | High | `[UNAVAILABLE]` | Cost and institutional access criteria exceed current research scope. |

---

## 5. Phase 0 Concrete Data Plan

For the initial proof-of-concept in Phase 1–3:
1. **News Source:** Expand existing TickerWatch RSS connectors to ingest official BSE and NSE corporate announcement feeds (`bse_announcements`, `nse_announcements`), alongside Moneycontrol and Economic Times financial RSS feeds.
2. **Market Data Source:** Rely on an authorized Indian market-data vendor or broker WebSocket feed (e.g., Zerodha Kite Connect or TrueData) capturing `NIFTY_FUT` Level 2 top-5 depth during live market hours (09:15–15:30 IST).
3. **Historical Data:** Procure an authorized 1-year historical sample of 1-second sampled `NIFTY_FUT` order book quotes aligned with historical BSE/NSE corporate announcements.

---

## 5. Phase 2 Validated Sources, Access Gates & Research Dataset Specification

### 5.1 Source Access Status (Phase 2 Validation)
- **SEC EDGAR (US Regulatory):** Verified & operational baseline connector; compliant with <= 10 req/s limit.
- **NSE Corporate Announcements (India):** Adapter implemented; live stream marked `Officially available but subscription required` due to Akamai WAF.
- **BSE Corporate Disclosures (India):** Adapter implemented; live stream marked `Subscription required`.
- **Zerodha Kite Connect WebSocket:** Binary protocol decoder (`KiteConnectBinaryDecoder`) implemented and tested; marked `Requires credentials`.
- **Deterministic Replay Engine:** Verified 100% operational offline using canonical sample fixtures.

### 5.2 Canonical Data Storage Specification
- **Metadata Tier:** SQLite WAL (`tickerwatch.db`) for article records, connector states, and dataset manifests.
- **Market Microstructure Tier:** Partitioned compressed JSONL (`data/market_events/YYYY-MM-DD/{instrument}/events_*.jsonl.gz`) delivering 58.4x compression ratio and > 89,000 events/second write throughput.
- **Raw Payloads:** Content-hashed binary storage (`data/raw_payloads/YYYY-MM-DD/{provider}/{content_hash}.bin`).

### 5.3 Research Dataset Specification
Exported datasets conform to `schema_version = "2.0.0"` with accompanying cryptographic manifests (`ResearchDatasetManifest`):
- Format: JSONL and CSV.
- Traceability: Every row links `news_event_id`, `headline`, `content_hash`, `decision_timestamp_utc`, `instrument_id`, `relevance`, pre-event quotes/volatility, and multi-horizon forward returns (100ms, 1s, 5s, 30s, 5m).

---

## 6. Phase 3 Financial NLP & Expectation Data Requirements

### 6.1 Point-in-Time Consensus Expectation Data
- **Core Prerequisite:** Essential for calculating genuine financial surprise ($S = A - E, S_{\%}$).
- **Required Metadata:** Metric name, consensus mean/median estimate, dispersion standard deviation ($\sigma$), estimate capture timestamp, and provider ID.
- **Strict Causal Integrity:** Expectation timestamp must strictly precede the event publication timestamp ($\tau_{\text{captured}} < \tau_{\text{decision}}$).
- **Candidate Data Sources for Indian Markets:** FactSet, Bloomberg Estimates, Refinitiv I/B/E/S, or certified domestic institutional consensus aggregators.
- **Handling Protocol When Absent:** Return explicit `UNAVAILABLE_EXPECTATION`. Fabricating consensus estimates or silently imputing numbers is strictly prohibited.

### 6.2 Financial Entity Knowledge Base Specifications
- **Master Registry:** Canonical tickers, exchange symbols (NSE, BSE, NASDAQ, NYSE), full corporate names, and aliases.
- **Corporate Hierarchy:** Parent-subsidiary mapping linking unlisted operating arms (e.g. Jio Platforms, Reliance Retail) to listed equity parents (`RELIANCE`).
- **Leadership Co-occurrence:** Key executive rosters (e.g. Mukesh Ambani, Tim Cook, Salil Parekh).
- **Collision Protection:** Vocabulary stopword list to prevent false-positive ticker matching on common English prepositions and nouns ("IT", "AT", "NOW", "CAN").

### 6.3 Gold Standard Evaluation Benchmark Corpus
- Hand-curated, multi-source evaluation corpus of at least 50 cases covering:
  - Indian corporate disclosures (quarterly earnings, dividend declarations, capex commitments, order wins).
  - Macroeconomic releases (RBI MPC repo rates, CPI/WPI inflation, SEBI regulatory actions).
  - Routine administrative notices (board meeting intimations, trading window closures, share certificate loss).
  - US SEC EDGAR filings (Apple, Amazon, Microsoft).
  - Adversarial collision cases and parenthesized accounting losses.

---

## 7. Phase 4 Historical Corpus, Embedding & Event-Study Requirements

### 7.1 Strictly Partitioned Historical Event Corpora
- **Geographic Non-Substitution:** Indian (`IN`: NSE/BSE/RBI/MOSPI) and US (`US`: SEC 8-K/10-Q/10-K/Enforcement) disclosures MUST remain stored and queried under explicit `MarketJurisdiction` partitions. Cross-market substitution (e.g., applying US tech 8-K post-event variance to Indian capital goods contracts) is prohibited.
- **Availability vs. Event Time:** Historical records MUST retain both publisher event timestamps (`event_timestamp_utc`) and system availability timestamps (`system_available_at_utc >= event_timestamp_utc`); causal candidate retrieval filters strictly on `system_available_at_utc < tau_decision`.
- **Versioning & Synthetic Isolation:** Every imported or benchmark dataset requires SHA-256 deduplication and explicit `is_synthetic` boolean flagging (`is_synthetic=0` mandatory in default production/research queries).

### 7.2 Historical Market Data for Multi-Horizon Event Studies
- **Intraday Outcomes (`1m`, `5m`, `30m`, `1h`):** Require verified intraday bar or quote/trade series whose median active-session spacing satisfies the horizon resolution limit (e.g., $\le 60\text{s}$ for `1m`). Daily EOD bars are strictly blocked from generating synthetic intraday outcomes (`INSUFFICIENT_RESOLUTION`).
- **Daily/Swing Outcomes (`1d`, `5d`):** Evaluated strictly on exchange trading calendars (`Asia/Kolkata` 09:15–15:30 IST; `America/New_York` 09:30–16:00 ET with DST and exchange holiday exclusions), with explicit `overnight_gap_included` and `session_boundary_crossed` metadata.
- **Derivatives & Rollover Provenance:** Futures observations require explicit `contract_id` tags and documented `FuturesRolloverSpec` adjustments across contract expiry transitions; unadjusted stitching across contract expiries yields `ROLLOVER_INVALID`.
- **Hard Feature/Label Separation:** Future outcome records live exclusively in `historical_event_outcomes` (`outcome_end_ts`) and are surfaced for historical peers only after `outcome_end_ts <= tau_decision`.

---

## 8. Phase 5 Empirical Data Readiness Gate & Known Gaps

Before any empirical claim about $P(\Delta F_{t+h} \mid E_t, H_t, M_t)$ can be accepted, `RealDataReadinessAuditor` (`backend/alpha/data_readiness.py`) requires minimum non-synthetic market and disclosure volume. As audited on `2026-11-11`:

- **India (`IN`): `BLOCKED`** — Only 4 general Moneycontrol wrap-ups ingested, 0 NSE/BSE corporate disclosures, 0.00-year span (target $\ge 5,000$ events, $\ge 2.0$ years), and 0 real market bars/quotes.
- **US (`US`): `INSUFFICIENT_DATA`** — 3,038 SEC filings exist (primarily AAPL/AMZN/MSFT Form 4/header metadata; 3,041/3,042 classify `ROUTINE_ADMINISTRATIVE`), all `observed_at_utc` compressed into a 1.5s batch backfill window (`2026-11-08T09:34Z`), 0 real market ticks, 0% consensus expectations.
- **Overall Empirical Modeling Status:** **`BLOCKED`** (`docs/alpha/PHASE_5_DATA_READINESS.md` & `docs/alpha/PHASE_5_DATA_REQUIREMENTS.md`). Phase 5 empirical alpha validation awaits external historical market bars (1m OHLCV / L1 quotes + corporate action & rollover ledgers) and multi-year primary exchange announcements.
