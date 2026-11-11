# Phase 6 Provider Authorization & Access Matrix (`PHASE_6_AUTHORIZATION_MATRIX`)

**Audit Timestamp:** 2026-11-11  
**Workspace Environment File:** `tickerwatch/.env` (inspected safely by key name & length only; zero secrets exposed)

---

## 1. Comprehensive Access & Compliance Matrix

| Source ID | Market | Category | URL / Endpoint | Auth Mechanism | Env / Config Status | Live Probe Result (2026-11-11) | Latency / Update Profile | Licensing / Redistribution Constraints | Adapter State in Phase 6 |
|---|---|---|---|---|---|---|---|---|---|
| `rbi_press_rss` | `IN` | Central Bank / Macro Releases | `https://www.rbi.org.in/pressreleases_rss.xml` | None (Public Official RSS + ETag) | Enabled (`RBI_RSS_ENABLED`) | **HTTP `200 OK`** (`10` XML items, `123 KB`, ETag + Last-Modified) | `224.8 ms`; poll `>= 300 s`; naive IST timestamps require `Asia/Kolkata` localization | Official RBI public releases; retain source URL & attribution; respect polite polling | **ENABLED (VERIFIED LIVE)** |
| `nse_corporate_rss` | `IN` | Listed Corporate Filings | `https://nsearchives.nseindia.com/content/RSS/Corporate_Announcements.xml` | Edge/WAF protected HTTPS | `NSE_RSS_ENABLED=false` | **`ReadTimeout` (`10.1 s`)** | Unknown until accessible; `300 s` default interval | NSE terms forbid unauthorized scraping & bulk redistribution | **DISABLED (TIMEOUT / BLOCKED)** |
| `bse_notices_xml` | `IN` | Exchange Circulars / Filings | `https://www.bseindia.com/data/xml/notices.xml` | Exchange WAF | Not configured | **`HTTP 403 Forbidden`** (`402 B`) | Blocked | Requires formal BSE data/notices license | **DISABLED (HTTP 403 BLOCKED)** |
| `pib_finmin_rss` | `IN` | Govt / MOSPI / FinMin Macro | `https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Reg=3` | WAF / Firewall | Not configured | **`HTTP 403 Forbidden`** (`393 B`) | Blocked | Requires institutional access / permitted client route | **DISABLED (HTTP 403 BLOCKED)** |
| `moneycontrol_market_reports` | `IN` | Secondary Market Wrap-Up RSS | `https://www.moneycontrol.com/rss/marketreports.xml` | Scripted UA blocked | In `config/feeds.yaml` (`RSS_ENABLED=true`) | **`HTTP 403 Forbidden`** (`403 B`) | Blocked (`80.5 ms` reject) | Media copyright & commercial restrictions; low company specificity | **DEGRADED / DISABLED RECOMMENDED** |
| `sec_edgar` | `US` | US Regulatory Filings (`8-K`, `10-Q`, `Form 4`) | `https://data.sec.gov/submissions/CIK{cik}.json` | Declared `User-Agent` + `<= 10 req/s` rate limit | `SEC_ENABLED=true`, `SEC_USER_AGENT` set (`len=33`) | **HTTP `200 OK`** (`163 KB` AAPL JSON; live smoke test PASSED) | `505.7 ms`; `600 s` poll | Public EDGAR API subject to fair-access User-Agent & `10 req/s` cap | **ENABLED (VERIFIED LIVE — US)** |
| `sec_news_press` | `US` | SEC Enforcement & Rulemaking RSS | `https://www.sec.gov/news/pressreleases.rss` | Declared `User-Agent` | In `config/feeds.yaml` (`600 s`) | **HTTP `200 OK`** (`25` items; live smoke test PASSED) | `123.6 ms`; conditional GET `304` supported | Public US government regulatory releases | **ENABLED (VERIFIED LIVE — US)** |
| `yahoo_finance_top` | `US` | General Finance RSS | `https://finance.yahoo.com/news/rssindex` | N/A | In `config/feeds.yaml` | **`HTTP 404 Not Found`** | Endpoint retired/broken | Non-authoritative aggregator | **STALE / DISABLED** |
| `zerodha_kite_ws` | `IN` | L1 Quotes, Trades, 5-Depth L2, Historical 1m OHLCV | `wss://ws.kite.trade` & Kite Connect REST | OAuth API Key + Daily Request/Access Token (paid Connect subscription) | `KITE_API_KEY`, `KITE_ACCESS_TOKEN`, `KITE_API_SECRET` = `NOT_SET` | Not attempted (no credentials) | Sub-second tick streaming when subscribed | NSE/BSE broker T&Cs prohibit third-party redistribution; single-account research OK | **DISABLED SCAFFOLD (AWAITING USER KEYS)** |
| `truedata_ws` | `IN` | L1 Ticks, 1m OHLCV, Continuous Futures | TrueData authorized WebSocket/REST | Commercial Username/Password/API Key | `TRUEDATA_*` = `NOT_SET` | Not attempted (no credentials) | Tick-level when subscribed | Paid commercial license required | **DISABLED SCAFFOLD (UNSUBSCRIBED)** |

---

## 2. Immediate Operational Posture Entering Phase 6

1. **India Macro/Monetary News (`rbi_press_rss`)** and **US Regulatory Filings (`sec_edgar`, `sec_news_press`)** are **live, verified, free, and compliant**, ready for live shadow news-capture pipeline operation.
2. **Indian Corporate Disclosure (`NSE` / `BSE`)** and **Live/Historical Indian Market Data (`Kite` / `TrueData` / `Upstox` / `Dhan`)** are **explicitly blocked by unauthenticated access / missing broker subscriptions**. They are implemented as fail-closed disabled adapters with clean authentication/connection hooks so connecting a valid `KITE_API_KEY` + daily `KITE_ACCESS_TOKEN` (or vendor credentials) activates live streaming seamlessly without altering downstream Phases 2–6 modules.
3. **Rule on Empirical Claims:** Until an Indian corporate announcement source and an Indian live/historical market-data feed are connected and verified alongside 20–30+ market sessions, Phase 6 models run strictly as **observation / unvalidated diagnostic software** (`MODEL_UNVALIDATED` / `SYNTHETIC_DIAGNOSTIC_ONLY`).
