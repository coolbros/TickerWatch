# TickerWatch Connector Architecture & Reference

**Phase:** 1 of 7  
**Module:** `backend/connectors`

---

## 1. Overview & Interface Contract

TickerWatch decouples all external financial information sources via an extensible, asynchronous connector abstraction. Every connector implements `BaseConnector` in `backend/connectors/base.py`.

```python
class BaseConnector(ABC):
    @abstractmethod
    async def fetch(self, cursor: Optional[str] = None) -> FetchBatch:
        """Fetch a batch of source items starting from the provided cursor."""
        raise NotImplementedError

    @abstractmethod
    async def ack(self, cursor: str) -> None:
        """Acknowledge cursor advancement only after durable persistence."""
        raise NotImplementedError

    async def health(self) -> ConnectorHealth:
        """Return connector health and freshness state."""
        ...
```

### The Ingestion Sequence & Invariants:
1. **Cursor Reading:** Read the last committed cursor from the database (`connector_state` table).
2. **Fetch Batch:** Fetch the batch with the cursor. If the upstream provider supports conditional HTTP (ETag, Last-Modified) or sequence tokens, the connector sends them.
3. **Validation & Normalisation:** Validate fields and transform raw source payloads into canonical `SourceItem` instances.
4. **Atomic Persistence:** Write the items to `articles` and advance the cursor in `connector_state` inside a single immediate SQLite transaction.
5. **Durable Acknowledgment:** Only after the transaction commits successfully is `connector.ack(cursor)` called.
6. **Telemetry:** Log ingestion duration, counts, and errors into `ingestion_attempts`.

---

## 2. Generic RSS/Atom Connector (`RSSConnector`)

**File:** `backend/connectors/rss.py`

### Capabilities:
- **Formats:** Full support for RSS 2.0 and Atom 1.0 feeds.
- **Conditional HTTP:** Emits `If-None-Match` (ETag) and `If-Modified-Since` (Last-Modified). Handles `HTTP 304 Not Modified` without downloading redundant payloads.
- **Deduplication Lookback:** For feeds with unsequenced or out-of-order publication timestamps, maintains a rolling window of recent seen GUIDs in its cursor to prevent reprocessing across restarts.
- **Relative URL Resolution:** Resolves relative URLs in `<link>` tags against the base feed URL.
- **Content Cleaning:** Strips HTML tags, unescapes XML entities, normalises whitespace.
- **Source IDs:** Extracts `<guid>`, `<id>`, or falls back to a deterministic hash of the canonical URL or title.

### Configuration (`config/feeds.yaml`):
```yaml
feeds:
  - id: sec_news_press
    name: "SEC Press Releases"
    url: "https://www.sec.gov/news/pressreleases.rss"
    poll_interval_seconds: 600
    language: "en"
    tickers: []

  - id: yahoo_finance_top
    name: "Yahoo Finance Top Stories"
    url: "https://finance.yahoo.com/news/rssindex"
    poll_interval_seconds: 300
    language: "en"
    tickers: []
```

---

## 3. SEC EDGAR Submissions Connector (`SECConnector`)

**File:** `backend/connectors/sec.py`

### Capabilities:
- **Official API Endpoint:** Ingests from `https://data.sec.gov/submissions/CIK{cik_10_digits}.json`.
- **CIK Normalisation:** Automatically normalises CIK strings to 10-digit zero-padded format (e.g. Apple CIK `320193` -> `0000320193`).
- **SEC User-Agent Policy:** Enforces SEC automated access requirements:
  - Format: `ApplicationName ContactEmail@domain.com`
  - Fails with `SECConfigurationError` and flags status as `AUTH_ERROR` if not configured or missing an email address.
- **Rate Limiting:** Enforces an inter-request pacing interval (120ms), safely well below the SEC's 10 requests per second rate ceiling.
- **Document URL Construction:** Builds official direct links to filed documents in SEC EDGAR archives:
  `https://www.sec.gov/Archives/edgar/data/{cik_stripped}/{accession_nodash}/{primary_doc}`
  Falls back to the company EDGAR browse page if no primary document is specified.
- **Form Support:** Parses standard and amended filings (10-K, 10-Q, 8-K, 4, 10-K/A, 10-Q/A, etc.).
- **Incremental Polling:** Tracks the latest seen accession number per CIK in the cursor, terminating traversal once previous filings are encountered.

### Configuration (`.env`):
```env
SEC_ENABLED=true
SEC_USER_AGENT=TickerWatch/0.1 admin@example.com
SEC_TRACKED_CIKS=0000320193:AAPL,0001018724:AMZN,0000789019:MSFT
SEC_POLL_INTERVAL_SECONDS=600
```

---

## 4. Indian Market Feeds (NSE / BSE)

### Status & Assessment:
1. **Official Exchange Feeds:**
   - The National Stock Exchange of India (NSE) website (`nseindia.com`) does **not** provide public unauthenticated open XML RSS 2.0 feeds. Their corporate announcements are served through session-restricted REST APIs guarded by Akamai Bot Manager (`ak_bmsc` cookies).
   - In accordance with the prompt's instruction: **"If the official endpoint cannot be verified, do not invent one."**
2. **Supported Ingestion via Generic RSS:**
   - Indian financial news and corporate announcement streams are seamlessly ingested using the `RSSConnector` with verified financial feeds:
     - Moneycontrol Market Reports: `https://www.moneycontrol.com/rss/marketreports.xml`
     - Economic Times India Markets: `https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms`
   - Configured in `config/feeds.yaml` under `moneycontrol_market_reports`.
3. **Future Extension:**
   - When official BSE or NSE authorized developer feeds become available, they can be implemented as a dedicated `NSEConnector` implementing `BaseConnector` without modifying any existing ingestion or database pipelines.

---

## 5. Authoring a New Connector

To add a new data provider:
1. Inherit from `BaseConnector` in `backend/connectors/`.
2. Implement `async def fetch(self, cursor: Optional[str]) -> FetchBatch`.
3. Implement `async def ack(self, cursor: str) -> None`.
4. Return canonical `SourceItem` instances constructed via `normalize_record(...)`.
5. Register the connector in `ConnectorRegistry.load_from_config()` in `backend/connectors/registry.py`.
6. Add contract tests in `tests/contract/`.
