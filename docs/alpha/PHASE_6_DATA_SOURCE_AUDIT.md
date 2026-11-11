# Phase 6 Data Source Discovery, Access & Probe Audit (`PHASE_6_DATA_SOURCE_AUDIT`)

**Date:** 2026-11-11  
**Author:** Agastya Varshney (`agastyavarshney@gmail.com`)  
**Branch:** `phase/6-live-shadow-validation`  
**Scope:** Authorized read-only evaluation of Indian and US financial news, regulatory disclosure feeds, and market-data providers reachable from the host environment without purchasing paid subscriptions or bypassing exchange/WAF protections.

---

## 1. Executive Summary

- **Indian Regulatory & Macro News (1 Verified Live Source):**
  - **RBI Press Releases RSS** (`https://www.rbi.org.in/pressreleases_rss.xml`) is **live, publicly accessible without credentials, and verified (`HTTP 200 OK`, `224.8 ms`, `123,413 bytes`, `10` entries)** with HTTP conditional polling headers (`ETag` + `Last-Modified`).
  - **Critical Timestamp Discovery:** Live inspection on `2026-11-11` showed RBI emits naive `published` strings in local IST without an explicit offset (e.g., `Wed, 11 Nov 2026 18:10:00` alongside `Last-Modified: Wed, 11 Nov 2026 12:51:05 GMT`). Direct ingestion MUST localize naive strings with `Asia/Kolkata` (`18:10 IST -> 12:40 UTC`) to prevent a **5.5-hour future look-ahead error**.
- **Indian Corporate Disclosure & News Blockers (Honest Access Gaps):**
  - **NSE Corporate Announcements RSS** (`https://nsearchives.nseindia.com/content/RSS/Corporate_Announcements.xml`, disabled via `NSE_RSS_ENABLED=false` in `.env`): Timed out (`ReadTimeout` after `10.1 s`) under polite HTTP GET. Retained as an explicitly disabled adapter; exchange WAF protections were **not** circumvented.
  - **BSE Notices XML** (`https://www.bseindia.com/data/xml/notices.xml`): Returned `HTTP 403 Forbidden` (`402 bytes` HTML block). Do not scrape; requires formal exchange/vendor feed authorization.
  - **Moneycontrol Market Reports RSS** (`https://www.moneycontrol.com/rss/marketreports.xml`): Returned `HTTP 403 Forbidden` (`403 bytes` HTML block) to scripted user-agent requests.
  - **PIB / Ministry of Finance RSS** (`https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Reg=3`): Returned `HTTP 403 Forbidden` (`393 bytes`).
  - **Listed Company IR Feeds:** Deferred as opt-in per-issuer RSS/Atom endpoints where explicit corporate IR feeds allow automated polling.
- **US Regulatory Feeds (2 Verified Live Sources via Existing Config):**
  - **SEC EDGAR Submissions JSON API** (`https://data.sec.gov/submissions/CIK{cik}.json`): **Verified live (`HTTP 200 OK`, `505.7 ms`, `163,787 bytes`)** using configured `SEC_USER_AGENT` in `.env` (`len=33`) and `SECRateLimiter` (`<= 10 req/s`). Opt-in live smoke test passed (`test_live_sec_edgar_submissions`).
  - **SEC Official Press Releases RSS** (`https://www.sec.gov/news/pressreleases.rss`): **Verified live (`HTTP 200 OK`, `123.6 ms`, `18,580 bytes`, `25` entries)**. Opt-in live smoke test passed (`test_live_sec_press_releases_rss`).
  - **Yahoo Finance Top Stories RSS** (`https://finance.yahoo.com/news/rssindex`, in `config/feeds.yaml`): Returned `HTTP 404 Not Found` (endpoint retired/moved).
- **Market Data Providers (`BLOCKED` — Zero Active Credentials):**
  - Inspection of `.env` and process environment variables confirmed **zero active broker or vendor credentials** (`KITE_API_KEY`, `KITE_ACCESS_TOKEN`, `UPSTOX_ACCESS_TOKEN`, `DHAN_ACCESS_TOKEN`, `FYERS_ACCESS_TOKEN`, `TRUEDATA_*`, `GLOBALDATAFEEDS_API_KEY`, `BREEZE_API_KEY`, `ANGEL_API_KEY`, `NSE_API_KEY`, `BSE_API_KEY`, `POLYGON_API_KEY`, `ALPACA_API_KEY`, `TIINGO_API_KEY` are all `NOT_SET`).
  - Phase 2 [`KiteConnectBinaryDecoder`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/market_data.py#L252) (8-byte LTP & 184-byte L2 5-depth packet decoder) and `MockStreamingMarketDataProvider` exist, and Phase 6 wraps live market feeds in explicitly disabled adapters until authorized API subscriptions/keys are supplied by the user.

---

## 2. Detailed News & Disclosure Source Assessment

### 2.1 RBI Press Releases RSS (`rbi_press_rss`) — **VERIFIED AUTHORIZED & ACTIVE**
- **Official Source:** Reserve Bank of India (`https://www.rbi.org.in/pressreleases_rss.xml`)
- **Access Mechanism:** Public unauthenticated HTTPS XML RSS 2.0 feed supporting HTTP conditional `ETag` (`W/"..."`) and `Last-Modified` / `304 Not Modified`.
- **Authentication:** None required (public central-bank regulatory notices).
- **Data Fields:** `title`, `link` (e.g., `?prid=...` unique press-release ID), `published` (naive IST string), `summary`.
- **Publication vs. Provider Timestamp:** `published` arrives in `Asia/Kolkata` local time without TZ offset -> strictly converted via `parse_source_timestamp_to_utc(..., default_tz=IST_ZONE)`. HTTP `Last-Modified` gives server UTC header.
- **Historical Archive:** Rolling live RSS window (~10 most recent releases per poll); older releases require continuous capture or authorized archive imports.
- **Rate Limits & Licensing:** Polite polling interval (`>= 300 s`, conditional GET on ETag). Official RBI statutory releases for informational/research use; preserve attribution & link URLs.
- **Observed Latency & Reliability:** `224.8 ms` HTTP round-trip; high reliability for monetary-policy, liquidity, and banking-sector announcements.

### 2.2 NSE Corporate Announcements RSS (`nse_corporate_rss`) — **BLOCKED (`ReadTimeout`)**
- **Official Source:** National Stock Exchange of India (`https://nsearchives.nseindia.com/content/RSS/Corporate_Announcements.xml`)
- **Access Mechanism:** HTTPS XML RSS referenced in Phase 2 [`NSECorporateAnnouncementsAdapter`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/news_capture.py#L89). Disabled in `.env` (`NSE_RSS_ENABLED=false`).
- **Observed Behavior:** `ReadTimeout` at `10.1 s` against direct HTTPS request.
- **Policy Decision:** Retained as an **explicitly disabled adapter**; do not spoof cookies or bypass Akamai edge rules. Unblocking requires stable network/whitelist routing or NSE authorized vendor data product (`NSE Data & Analytics`).

### 2.3 BSE Corporate Notices (`bse_notices_xml`) & PIB FinMin (`pib_finmin_rss`) — **BLOCKED (`HTTP 403`)**
- **BSE Notices XML (`https://www.bseindia.com/data/xml/notices.xml`):** `HTTP 403 Forbidden` (`402 bytes` HTML block). Requires formal BSE corporate-feed licensing or vendor redistribution.
- **PIB FinMin RSS (`https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Reg=3`):** `HTTP 403 Forbidden` (`393 bytes`). Disabled unless institutional whitelisting is granted.

### 2.4 Moneycontrol & Yahoo Finance Legacy RSS (`config/feeds.yaml`) — **DEGRADED / INACCESSIBLE**
- **Moneycontrol (`moneycontrol_market_reports`):** `HTTP 403 Forbidden` (`80.5 ms`); historical `tickerwatch.db` held only 4 earlier wrap-ups (`2024-04-23`). Not a reliable live company disclosure source.
- **Yahoo Finance (`yahoo_finance_top`):** `HTTP 404 Not Found` (`250.7 ms`); endpoint deprecated.

### 2.5 US SEC EDGAR Submissions & Press Releases (`sec_edgar`, `sec_news_press`) — **VERIFIED AUTHORIZED & ACTIVE**
- **Official Source:** US Securities and Exchange Commission (`https://data.sec.gov/submissions/CIK{cik}.json` & `https://www.sec.gov/news/pressreleases.rss`).
- **Authentication:** Declared compliant `User-Agent` string configured in `.env` (`SEC_USER_AGENT` set, `len=33`) + strict `<= 10 req/s` shared [`SECRateLimiter`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/connectors/sec.py#L25).
- **Timestamp Semantics:** `acceptanceDateTime` / RSS `published` in US Eastern (`America/New_York` EST/EDT) or UTC ISO; filing date strings (`filingDate`) mapped conservatively to `17:30 ET` post-close unless exact `acceptanceDateTime` exists.
- **Historical Archive:** Full per-CIK submission JSON archive (~1,000 recent filings per tracked CIK).
- **Observed Latency:** `505.7 ms` JSON API / `123.6 ms` RSS (`test_live_smoke.py`: 2/2 passed in `1.22 s`).
- **Research Isolation:** Kept strictly partitioned under `US` jurisdiction and excluded from Indian market-data modeling targets unless matched US data exists.

---

## 3. Market-Data Feed Audit

| Provider / Interface | Intended Capabilities | Auth / Credential Audit | Status in Host Workspace |
|---|---|---|---|
| **Zerodha Kite Connect WebSocket** (`KiteConnectBinaryDecoder`) | L1 Quotes, Trades, 5-level L2 Depth (`184B` packets), NSE EQ / FUTIDX / FUTSTK | `KITE_API_KEY`, `KITE_ACCESS_TOKEN`, `KITE_API_SECRET` all `NOT_SET` (plus daily OAuth token rotation & paid Connect + Historical API add-on required) | **SCAFFOLDED / DISABLED** — Binary frame decoder verified offline; awaits user API subscription & daily session token |
| **TrueData / GlobalDataFeeds WebSockets** | Tick-by-tick NSE/BSE L1 + 1m historical OHLCV + continuous futures | `TRUEDATA_USERNAME`, `TRUEDATA_PASSWORD`, `GLOBALDATAFEEDS_API_KEY` all `NOT_SET` | **DISABLED** — Commercial data-vendor subscription not present |
| **Broker REST+WS (Upstox / Dhan / Fyers / ICICI Breeze / Angel One)** | Retail/Pro broker WebSocket + historical 1m bars for INR equities/futures | All respective env tokens `NOT_SET` | **NOT CONFIGURED** — Disabled pending explicit user broker credentials |
| **NSE / BSE Authorized Exchange Direct Feeds** | Exchange-direct TBT/L2, official EOD bhavcopy, corporate action ledgers | `NSE_API_KEY`, `BSE_API_KEY` `NOT_SET`; commercial exchange data license required for commercial live distribution | **BLOCKED** — Do not scrape web endpoints |
| **US Data Vendors (Polygon / Alpaca / Tiingo)** | US L1 quotes, trades, 1m historical bars | `POLYGON_API_KEY`, `ALPACA_API_KEY`, `TIINGO_API_KEY` all `NOT_SET` | **NOT CONFIGURED** — Awaits subscription if US intraday research is activated |
| **Phase 2 Deterministic Replay & Mock Stream** | Local `.jsonl.gz` tick replay & fault-injected offline testing | None (local files / offline mocks) | **ACTIVE FOR REPLAY & OFFLINE TESTS** — Strictly tagged `REPLAY` / synthetic when fixtures used |

> [!IMPORTANT]
> Consistent with project ethics and engineering governance, **no paid data subscriptions were purchased, zero credentials were fabricated or printed, zero exchange WAF protections were circumvented, and mocked feeds are strictly quarantined from live production status.**
