# TickerWatch Alpha — Data Requirements, Source Feasibility & Alignment Architecture

> **Document Version:** 1.0.0-alpha  
> **Status:** APPROVED BASELINE — PHASE 0  
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
