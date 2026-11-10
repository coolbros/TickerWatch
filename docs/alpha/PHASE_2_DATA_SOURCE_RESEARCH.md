# TickerWatch Alpha — Phase 2 Data Source Research & Access Validation

> **Document Version:** 1.0.0-alpha  
> **Status:** APPROVED RESEARCH BASELINE — PHASE 2  
> **Authors:** Quantitative Engineering & Market Microstructure Team  
> **Target Jurisdiction:** Indian Financial Markets (National Stock Exchange of India / Bombay Stock Exchange) & Global Regulatory Filings (SEC EDGAR)

---

## 1. Executive Summary & Research Mandate

TickerWatch Alpha requires authoritative, legally compliant, and temporally precise data sources across two complementary streams:
1. **News Intelligence:** Corporate announcements, earnings releases, board disclosures, order wins, and macroeconomic releases.
2. **Market Intelligence:** Contemporaneous quotes (Level 1 / Level 2 depth), trade prints, and order-book state snapshots.

Phase 1 established that the public Yahoo Finance RSS endpoint returned HTTP 404 and was permanently retired. The SEC EDGAR connector was hardened with strict rate-limiting (10 req/s), CIK normalization, and exponential backoff.

Phase 2 investigates the authoritative landscape for Indian financial news and market data. This document formally catalogues, classifies, and evaluates candidate sources against rigorous operational criteria: latency, timestamp provenance, rate limits, licensing, and access gates.

---

## 2. Work Package A: News Source Evaluation

### A.1 NSE Corporate Announcements & RSS Feeds
- **Provider:** National Stock Exchange of India Ltd. (NSE).
- **Coverage:** Official corporate announcements, financial results, press releases, board meetings, and exchange circulars for all listed companies.
- **Delivery Protocol:**
  - *Public Web Portal Endpoint:* `https://www.nseindia.com/api/corporate-announcements` (JSON REST API).
  - *Archival RSS Feeds:* `https://nsearchives.nseindia.com/content/RSS/Corporate_Announcements.xml` and category-specific feeds.
- **Publication Timestamp Availability:** Yes. Exchange filings carry the exchange acknowledgement timestamp (`an_dt` / `attchmntText`) formatted in IST (`DD-Mon-YYYY HH:mm:ss`).
- **Provider Receipt Timestamp Availability:** Available in JSON metadata as exchange upload time.
- **Update and Correction Behaviour:** When a corporate filing is amended or clarified, NSE publishes a new record referencing the original subject line with prefix "Clarification / Revision" and a new filing ID. Original records are not overwritten in place.
- **Rate Limits & WAF Characteristics:**
  - NSE employs enterprise bot protection (Akamai Bot Manager / Cloudflare WAF).
  - Direct HTTP scraping from cloud IP ranges (AWS, GCP, Azure) frequently encounters HTTP 403 Forbidden or TLS fingerprint challenges.
  - Requires session cookie initialization via root domain before calling API routes.
- **Latency Characteristics:**
  - RSS feed update polling interval: $\approx 180\text{s}$ to $300\text{s}$.
  - Web portal JSON API: $\approx 30\text{s}$ to $60\text{s}$ polling cycle.
  - Push-based streaming: Not available on public web endpoints.
- **Historical Archive Availability:** Extensive archive available via NSE Archives portal (`nsearchives.nseindia.com`) dating back over 15 years.
- **Licensing & Permissible Storage:**
  - Public web browsing terms prohibit automated mass extraction and commercial redistribution without written agreement.
  - Commercial feeds require licensing through NSE Data & Analytics Ltd.
- **Integration Complexity:** Moderate (requires header spoofing mitigation, cookie refresh loop, or commercial direct integration).
- **Cost:** Public web endpoints are free but brittle; official commercial feeds require enterprise licensing.
- **Access Classification:** `Officially available but subscription required` for automated high-reliability streaming; public feed is `Unverified / Sandbox-Blocked` due to Akamai WAF constraints.

### A.2 BSE Corporate Announcements & API Feeds
- **Provider:** BSE Limited (formerly Bombay Stock Exchange).
- **Coverage:** All equity disclosures filed under SEBI (Listing Obligations and Disclosure Requirements) Regulations, 2015 (LODR).
- **Delivery Protocol:**
  - *BSE API Endpoint:* `https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w` (JSON API).
  - *BSE Corporate RSS:* `https://www.bseindia.com/data/xml/corporate_announcements.xml`.
- **Publication Timestamp Availability:** Yes. BSE records the exact filing time (`NEWS_DT`, format `YYYY-MM-DDTHH:mm:ss`).
- **Provider Receipt Timestamp Availability:** Yes, recorded in BSE system audit logs.
- **Update and Correction Behaviour:** Disclosures are assigned unique announcement IDs (`NEWS_ID`). Amendments generate a distinct `NEWS_ID` linking to the original subject.
- **Rate Limits & Bot Mitigation:**
  - BSE endpoints enforce IP rate limiting ($\approx 30$ requests per minute).
  - Less aggressive WAF blocking compared to NSE, but subject to throttling and captchas under burst requests.
- **Latency Characteristics:** Polling latency $\approx 60\text{s}$ to $120\text{s}$.
- **Historical Archive Availability:** Searchable archive via BSE Corporate Disclosures portal.
- **Licensing & Permissible Storage:** Internal research storage permitted under fair dealing; redistribution prohibited without BSE Data Direct licensing.
- **Integration Complexity:** Low to Moderate.
- **Cost:** Free for low-frequency public queries; commercial feed requires BSE Market Data agreement.
- **Access Classification:** `Officially available but subscription required` for enterprise SLAs.

### A.3 SEC EDGAR (Existing Infrastructure — Maintained)
- **Provider:** US Securities and Exchange Commission.
- **Coverage:** US public company submissions (8-K, 10-Q, 10-K, Form 4) for dual-listed ADRs and US macro/earnings context.
- **Delivery Protocol:** Submissions JSON REST API (`https://data.sec.gov/submissions/CIK{cik}.json`) and RSS feeds.
- **Publication Timestamp Availability:** Yes (`acceptanceDateTime` to millisecond resolution).
- **Provider Receipt Timestamp Availability:** Yes (`filingDate`, `acceptanceDateTime`).
- **Rate Limits:** Strictly enforced at $\le 10\text{ req/sec}$ with declared custom `User-Agent`.
- **Status:** **Verified and currently accessible** (Hardened and passing 100% in Phase 1 test suite).

### A.4 Financial News Providers (Streaming & Push)
- **Reuters Machine Readable News (MRN) / Bloomberg Event-Driven Feeds:**
  - Ultra-low latency ($< 10\text{ms}$ push via UDP multicast or dedicated WebSocket).
  - Rich entity tagging, sentiment flags, structured numeric tables.
  - Licensing: Enterprise contracts starting at \$5,000–\$25,000/month.
  - Access Classification: `Requires commercial agreement`.
- **Cogencis / Informist Financial Wire:**
  - Leading real-time Indian financial news wire with dedicated corporate earnings and debt market coverage.
  - Delivery: Terminal or specialized TCP push feed.
  - Access Classification: `Requires commercial agreement`.
- **Moneycontrol / Economic Times RSS & Push Feeds:**
  - Syndicated RSS feeds (e.g. `https://www.moneycontrol.com/rss/marketreports.xml`).
  - Delay: 2 to 15 minutes after official exchange disclosure.
  - Suitable only for secondary context, not low-latency discovery.
  - Access Classification: `Verified and currently accessible` for public RSS.

---

## 3. Work Package A: Market Data Source Evaluation

### B.1 NSE Data & Analytics (Official Exchange Feeds)
NSE Data & Analytics (subsidiary of NSE) licenses official real-time and historical market data:
- **Level 1 (L1):** Best Bid and Ask (BBO), Last Traded Price (LTP), Daily High/Low, Cumulative Volume. Broadcast at 1-second intervals.
- **Level 2 (L2):** 5 levels of bid and ask depth with price, quantity, and orders count. Broadcast every 1 second or 500ms.
- **Level 3 (L3):** 20 levels of market depth. High-throughput multicast feed.
- **Tick-by-Tick (TBT):** Raw individual order insertions, modifications, cancellations, and trade executions directly from the match engine. Sub-millisecond latency. Available via 1G/10G co-location cross-connects at NSE colocation facility (Bandra-Kurla Complex) or point-of-presence (POP).
- **Licensing & Usage Restrictions:**
  - Exchange data policies strictly categorize use cases: *Display Only*, *Non-Display Trading / Algorithmic*, and *Data Redistribution*.
  - Storing tick data for quantitative model training requires a specific *Internal Non-Display & Analytics* license.
- **Access Classification:** `Requires commercial agreement`. High capital expenditure (monthly exchange data royalties $\approx$ ₹1,50,000–₹5,000,000+).

### B.2 Authorised Real-Time Data Vendors
- **TruData:**
  - Authorised NSE/BSE real-time data vendor.
  - Protocols: Ultra-fast WebSocket streaming (JSON and binary) and historical REST API.
  - Feeds: Level 1 and Level 2 (5-depth) tick-by-tick feeds.
  - Latency: $\approx 20\text{ms}$–$100\text{ms}$ from exchange gateway.
  - Timestamps: Exchange timestamp (to second/millisecond), provider receipt timestamp, sequence IDs.
  - Cost: Developer plans $\approx$ ₹2,000–₹10,000/month.
  - Access Classification: `Available through an authorised vendor` (Requires account subscription and API key).
- **Global DataFeeds (NimbleDataPro):**
  - Authorised data vendor offering low-latency WebSocket streaming for NSE Cash, F&O, and Currencies.
  - Access Classification: `Available through an authorised vendor`.

### B.3 Broker-Provided Streaming APIs
- **Zerodha Kite Connect WebSocket API:**
  - Architecture: Binary WebSocket protocol (`wss://ws.kite.trade`).
  - Modes:
    1. `LTP`: Last traded price only (8 bytes per tick).
    2. `Quote`: LTP, LTT, volume, buy/sell quantities, open, high, low, close (28 or 32 bytes).
    3. `Full`: Quote + 5-depth Level 2 bid/ask book (184 bytes per tick).
  - Timestamps: Exchange Last Traded Timestamp (`exchange_timestamp`) with 1-second resolution; feed tick packet generated on change.
  - Throttles: Broadcast rate capped at max 1 quote update per instrument per second by the broker ticker server.
  - Gap Detection: Broker stream does not provide strict exchange sequence numbers; integrity monitored via local timestamp monotonicity and heartbeat intervals.
  - Terms: Designed for client execution and personal display; algorithmic storage and redistribution subject to Kite Connect Developer Terms.
  - Access Classification: `Requires credentials` (API Key + API Secret + Daily Request Token, ₹2,000/month).
- **Upstox API v2 / Angel One SmartAPI:**
  - Protobuf/binary WebSockets providing 5-depth Level 2 quotes. Similar 1-second throttle.
  - Access Classification: `Requires credentials`.

### B.4 Historical Market Data Providers
- **NSE Official Historical Data Products:**
  - Daily EOD Bhavcopy (free, official).
  - Historical 1-minute and tick-by-tick data archives (purchasable on physical media or secure cloud bucket).
- **TruData Historical Data API:**
  - 1-minute bars and tick history accessible via REST API.

---

## 4. Comprehensive Source Comparison Matrix

| Source Category | Provider | Delivery Protocol | Depth / Resolution | Timestamps Available | Gap Detection | Licensing Tier | Access Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **News** | NSE Corporate Portal | REST / RSS | Full text + PDF link | Filing Time (IST) | No | Free Public (WAF restricted) | Subscription Required |
| **News** | BSE Corporate Portal | REST / RSS | Full text + PDF link | Filing Time (IST) | No | Free Public (Rate-limited) | Subscription Required |
| **News** | SEC EDGAR | REST / JSON | Full 8-K/10-Q/Filings | Acceptance ms (UTC) | Sequence CIK | Free Regulatory (10 req/s) | **Verified & Accessible** |
| **News** | Financial Wires | TCP Push / Multicast | Flash headlines & tables | Provider ms (UTC) | Yes | Commercial (\$5k+/mo) | Commercial Agreement |
| **Market Data** | NSE Data & Analytics | Multicast UDP / Co-lo | L1, L2, L3, TBT | Exchange $\mu\text{s}$ (IST) | Packet Seq No | Direct Exchange Commercial | Commercial Agreement |
| **Market Data** | TruData | WebSocket (Binary/JSON) | L1 + L2 (5-depth) | Exchange ms + Provider ms | Provider Seq No | Authorized Vendor (\$50–\$150/mo)| Authorized Vendor |
| **Market Data** | Zerodha Kite Connect | WebSocket (Binary) | L1 + L2 (5-depth) | Exchange sec + Local ms | Heartbeat / Ping | Retail Broker API (₹2k/mo) | Requires Credentials |
| **Market Data** | Replay Engine (Internal)| In-memory / File stream| L1 + L2 + Ticks | Exchange ms + Local $\mu\text{s}$ | Monotonic Counter | Internal Research / MIT | **Verified & Accessible** |

---

## 5. Source-Selection Gate & Phase 2 Recommendation

### Gate Decision & Ethical Guardrails:
1. **No Circumvention:** The system shall never scrape WAF-protected exchange endpoints using evasion techniques, bot bypassers, or unauthenticated token harvesting.
2. **No Unauthorised Subscriptions:** Zero commercial contracts, paid developer keys, or credentials shall be purchased or configured without explicit user authorization.
3. **Honest Connectivity Representation:** Live streaming market feeds shall remain explicitly marked `UNVERIFIED_NO_CREDENTIALS` until genuine live credentials and network tunnels are verified.
4. **Architectural Decoupling:**
   - Implement the **MarketDataProvider** and **NewsCaptureAdapter** interfaces as first-class provider abstractions.
   - Implement a fully deterministic, high-throughput **DeterministicReplayProvider** capable of replaying authentic historical market tick datasets and recorded news fixtures with microsecond-level clock fidelity.
   - Implement a production-grade **KiteConnectWebSocketAdapter** and **TruDataWebSocketAdapter** scaffold that faithfully maps binary and JSON broker packets to canonical schemas the moment credentials are provided.
   - Keep SEC EDGAR operational as a verified live baseline news connector.
