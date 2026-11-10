# TickerWatch Alpha — Phase 2 Canonical Data Schemas Specification

> **Document Version:** 2.0.0-alpha  
> **Status:** APPROVED CONTRACT SPECIFICATION — PHASE 2  
> **Authors:** Quantitative Data Engineering & Systems Architecture  
> **Scope:** Canonical News Event Schema, Canonical Market Data Event Schema, and Quality Envelopes

---

## 1. Design Principles & Contract Governance

To prevent data drift, look-ahead bias, and schema corruption across distributed research pipelines, TickerWatch Alpha enforces **versioned, immutable data contracts**:
1. **Explicit Schema Versioning:** All canonical records carry an explicit `schema_version` (e.g. `2.0.0`).
2. **Lossless Temporal Provenance:** Timestamps from each processing stage (exchange, provider, local receipt, persistence) are preserved in separate fields.
3. **Exact Price Precision:** Financial prices and spreads use arbitrary-precision `Decimal` representations, never binary floating-point numbers (`float`), eliminating catastrophic rounding errors.
4. **Traceable Corrections:** Errata and updates issued by regulatory bodies or news wires never overwrite original disclosures in-place. Revisions generate new events with explicit `replaces_event_id` pointers.
5. **Separation of Concerns:** Microstructure quotes (L1/L2) are strictly separated from executed trades. Complete order-book snapshots are explicitly distinguished from incremental deltas.

---

## 2. Canonical News Event Schema (`NewsEvent`)

Every incoming announcement is normalized into the `NewsEvent` schema:

| Field Name | Type | Nullable | Description |
| :--- | :--- | :--- | :--- |
| `event_id` | `str` | No | Unique canonical event identifier (`news_evt_<uuid>`) |
| `provider_id` | `str` | No | Identifier of the originating connector (e.g. `nse_announcements`, `sec_edgar`) |
| `source_name` | `str` | No | Human-readable source (e.g. `NSE Corporate Filings`, `SEC EDGAR`) |
| `source_url` | `str` | No | Original URL or canonical filing reference |
| `canonical_article_id` | `int` | Yes | Foreign key to existing TickerWatch `articles.id` table |
| `headline` | `str` | No | Sanitized headline or subject line |
| `body_text` | `str` | Yes | Retained snippet or full text (subject to licensing restrictions) |
| `published_at_utc` | `datetime` | Yes | Disclosed publication timestamp in UTC |
| `provider_observed_at_utc` | `datetime` | Yes | Timestamp when the external provider observed or published the item |
| `local_received_at_utc` | `datetime` | No | Monotonically recorded wall-clock timestamp upon socket receipt |
| `local_monotonic_ns` | `int` | No | Host high-resolution monotonic clock tick (`time.perf_counter_ns()`) |
| `ingestion_completed_at_utc` | `datetime` | No | Wall-clock timestamp upon validation and persistent commit |
| `version` | `int` | No | Revision number (starts at `1`) |
| `is_correction` | `bool` | No | Flag indicating if this event amends or corrects an earlier filing |
| `replaces_event_id` | `str` | Yes | Identifier of the earlier event amended by this record |
| `language` | `str` | No | ISO 639-1 language code (`en`, `hi`) |
| `entity_ids` | `List[str]` | No | Normalized entity symbols (e.g. `['RELIANCE']`, `['RBI']`) |
| `instrument_mappings` | `List[InstrumentMappingRecord]` | No | Structured portfolio of directly and contextually affected instruments |
| `content_hash` | `str` | No | SHA-256 fingerprint over title, normalized URL, and body content |
| `raw_payload_ref` | `str` | Yes | Storage pointer or hash key to raw incoming payload archive |
| `schema_version` | `str` | No | Contract version: `"2.0.0"` |

---

## 3. Canonical Market Data Event Schema (`MarketEvent`)

Market data streams are decoded and normalized into `MarketEvent` records:

### 3.1 Depth Level Model (`DepthLevel`)
```json
{
  "price": "2450.50",
  "quantity": 500,
  "orders_count": 4
}
```

### 3.2 Market Event Record Specification

| Field Name | Type | Nullable | Description |
| :--- | :--- | :--- | :--- |
| `event_id` | `str` | No | Unique canonical market event identifier (`mkt_evt_<uuid>`) |
| `provider` | `str` | No | Market data vendor or protocol (e.g. `TRUDATA`, `ZERODHA_KITE`, `REPLAY`) |
| `venue` | `str` | No | Exchange venue (`NSE`, `BSE`) |
| `segment` | `str` | No | Market segment (`EQ`, `FUTIDX`, `FUTSTK`) |
| `instrument_id` | `str` | No | Canonical instrument ID (e.g. `NSE:NIFTY_FUT:CURRENT`) |
| `contract_id` | `str` | Yes | Vendor instrument token (e.g. `256265`) |
| `message_type` | `MarketMessageType` | No | `QUOTE`, `TRADE`, `BOOK_SNAPSHOT`, `BOOK_DELTA`, `SESSION_STATE`, `HEARTBEAT` |
| `exchange_timestamp_utc` | `datetime` | Yes | Exchange match-engine event timestamp in UTC |
| `provider_timestamp_utc` | `datetime` | Yes | Vendor transmission or receipt timestamp in UTC |
| `local_received_at_utc` | `datetime` | No | Local wall-clock arrival timestamp in UTC |
| `local_monotonic_ns` | `int` | No | Local monotonic timestamp in nanoseconds (`perf_counter_ns`) |
| `persisted_at_utc` | `datetime` | Yes | Timestamp when committed to storage buffer/disk |
| `sequence_number` | `int` | Yes | Strict monotonically increasing feed sequence number |
| `bid_price` | `Decimal` | Yes | Best bid price (Level 1) |
| `bid_quantity` | `int` | Yes | Best bid aggregate quantity |
| `ask_price` | `Decimal` | Yes | Best ask price (Level 1) |
| `ask_quantity` | `int` | Yes | Best ask aggregate quantity |
| `depth_bids` | `List[DepthLevel]` | No | Order book bid side (5 to 20 levels for snapshots) |
| `depth_asks` | `List[DepthLevel]` | No | Order book ask side (5 to 20 levels for snapshots) |
| `last_traded_price` | `Decimal` | Yes | Last traded price (LTP) |
| `last_traded_quantity`| `int` | Yes | Size of the last executed trade print |
| `cumulative_volume` | `int` | Yes | Day cumulative traded volume |
| `is_snapshot` | `bool` | No | True if complete book snapshot; False if incremental delta |
| `session_state` | `str` | Yes | `PRE_OPEN`, `NORMAL_OPEN`, `HALTED`, `CLOSED` |
| `raw_payload_ref` | `str` | Yes | Pointer to raw binary or JSON frame in archive |
| `schema_version` | `str` | No | Contract version: `"2.0.0"` |

---

## 4. Derived Microstructure Metric Helper Fields
For synchronous in-memory analysis and downstream feature engineering, the market event provides derived properties computed on demand:
- **`spread`**: $\text{ask\_price} - \text{bid\_price}$
- **`mid_price`**: $\frac{\text{bid\_price} + \text{ask\_price}}{2}$
- **`microprice`**: Volume-weighted mid-price:
  $$P_{\text{micro}} = \frac{P_{\text{bid}} \cdot Q_{\text{ask}} + P_{\text{ask}} \cdot Q_{\text{bid}}}{Q_{\text{bid}} + Q_{\text{ask}}}$$
- **`book_imbalance`**: $\frac{Q_{\text{bid}} - Q_{\text{ask}}}{Q_{\text{bid}} + Q_{\text{ask}}}$

---

## 5. Backward Compatibility & Extension Policy
- Canonical models inherit from Pydantic `BaseModel` with strict type validation.
- Fields added in future phases (e.g. options Greeks in Phase 10) must be optional with default `None`.
- Core timestamp and pricing fields may never be renamed or repurposed.
