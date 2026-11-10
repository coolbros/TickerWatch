# TickerWatch Alpha — Phase 3 Corpus & Data Availability Audit

> **Document Version:** 1.0.0-alpha  
> **Status:** APPROVED BASELINE AUDIT — PHASE 3  
> **Authors:** Quantitative Research & Financial NLP Engineering  
> **Repository:** `TickerWatch` (`coolbros/TickerWatch`)  
> **Scope:** Empirical Audit of Local Databases, Feed Characteristics, and Field Availability

---

## 1. Executive Summary

Phase 3 transitions TickerWatch Alpha from data capture and temporal alignment into financial event understanding. Before authoring entity-resolution logic, event classifiers, or surprise extractors, this audit establishes an empirical baseline of the data genuinely available within the system.

**Key Finding:**  
The existing local corpus contains 3,043 persisted articles dominated by US SEC EDGAR filings (3,013 rows across AAPL, AMZN, MSFT) and SEC press releases (25 rows), with a limited sample of Indian market reports (4 rows from Moneycontrol). Crucially, raw filings contain reported actuals, but lack pre-event consensus estimates and structured numerical expectations.

**Research Implication:**  
We must not attempt to train a deep financial Transformer from scratch on this corpus. Instead, Phase 3 must construct:
1. A rigorous, deterministic financial NLP baseline with grammar-based numerical extraction and alias-aware entity disambiguation.
2. A point-in-time consensus expectation model that handles absent consensus gracefully via explicit `UNAVAILABLE_EXPECTATION` states.
3. Pretrained financial language model evaluation (e.g. FinBERT, ModernBERT, DeBERTa-v3-small) profiled for CPU inference efficiency.
4. An inspectable, gold-standard annotated benchmark set.

---

## 2. Empirical Database Inventory (`tickerwatch.db`)

An audit of the operational SQLite database (`tickerwatch.db`) reveals the following table statistics:

| Table Name | Row Count | Storage Role | Data Integrity Status |
| :--- | :--- | :--- | :--- |
| `articles` | 3,043 rows | Canonical news article metadata and text snippets | Clean, WAL mode, FTS5 indexed |
| `connector_state` | 5 rows | Durable polling cursors and health metrics | Up to date, zero corruption |
| `ingestion_attempts`| 196 rows | Telemetry history (fetch/insert/skip counts) | Verified |
| `dead_letter_items` | 0 rows | Quarantined malformed incoming items | Clean (0 malformed dropped) |
| `articles_fts` | 3,043 rows | Full-text search index (SQLite FTS5) | Synchronized via SQLite triggers |
| `schema_migrations`| 2 rows | Versioned schema migration log | Clean (Migrations 001 and 002) |

---

## 3. News Corpus Distribution & Source Breakdown

Analysis of the 3,043 persisted records in `articles`:

```
+------------------------------------+----------------+---------------+-------------------------------------+
| Connector ID                       | Total Articles | Percentage    | Temporal Coverage                   |
+------------------------------------+----------------+---------------+-------------------------------------+
| sec_edgar                          | 3,013          | 99.01%        | Historical Form 4, 8-K, 10-Q (US)   |
| sec_news_press                     | 25             | 0.82%         | Oct - Nov 2026 Regulatory Releases  |
| moneycontrol_market_reports        | 4              | 0.13%         | Indian Market Daily Wrap-ups        |
| test_api_conn                      | 1              | 0.04%         | Test fixture                        |
+------------------------------------+----------------+---------------+-------------------------------------+
```

### Source Distribution Insights:
- **US Regulatory Depth (`sec_edgar`):** High depth for mega-cap tech issuers (Apple CIK `0000320193`, Amazon CIK `0001018724`, Microsoft CIK `0000789019`). Predominantly insider transactions (Form 4) and periodic financial reports (10-Q, 8-K).
- **Indian Market Representation:** The live database currently possesses minimal Indian corporate press releases because the NSE/BSE adapters built in Phase 2 remain deliberately disabled pending licensed feed arrangement.
- **Duplication Rate:** Phase 1 and Phase 2 deduplication filters achieved **0.0% unhandled duplicate insertions** in the `articles` table.

---

## 4. Field Availability & Missing Data Matrix

Every field in the canonical `articles` and Phase 2 `NewsEvent` schema was audited for availability:

| Schema Field | Availability | Fidelity / Format | Notes & Caveats |
| :--- | :--- | :--- | :--- |
| `id` / `event_id` | 100% | Integer PK / `news_evt_<hex>` | Guaranteed immutable unique identifier |
| `publisher` / `source_name`| 100% | Text string (e.g. `SEC EDGAR`) | Reliable provenance |
| `title` / `headline` | 100% | Clean text | Primary input for high-speed classification |
| `url` / `source_url` | 100% | Normalized URL | Resolvable link to filing or report |
| `published_at_utc` | 98.4% | ISO-8601 UTC timestamp | Filing/publication date from source |
| `observed_at_utc` | 100% | ISO-8601 UTC timestamp | Socket arrival time ($\tau_{\text{decision}}$) |
| `content_hash` | 100% | 64-char hex SHA-256 | Deterministic content fingerprint |
| `snippet` / `body_text` | 92.1% | Text snippet / abstract | Truncated snippet; full text archived where permitted |
| `raw_metadata` | 100% | JSON object | Contains accession number, form type, version |
| `tickers` | 99.1% | JSON list of strings | Extracted ticker tags (e.g. `['AAPL']`) |
| **Consensus Estimates ($E$)**| **0.0%** | **ABSENT** | Not provided by SEC or RSS news feeds |
| **Numerical Surprise ($S$)** | **0.0%** | **ABSENT** | Must be extracted and calculated in Phase 3 |
| **Materiality Score** | **0.0%** | **ABSENT** | Must be scored in Phase 3 |
| **Hierarchy / Event Subtype**| **0.0%** | **ABSENT** | Unstructured headline text only |

---

## 5. Audit Conclusions & Phase 3 Technical Constraints

1. **Expectation Safeguard:** Because commercial consensus estimates (e.g. FactSet, Bloomberg, Refinitiv) are not currently licensed, the numerical surprise engine must support explicit fallback states:
   - When prior consensus $E$ is verified: Compute $S = A - E$ and $S_{\%} = \frac{A - E}{|E|} \times 100$.
   - When prior consensus $E$ is unavailable: Mark surprise as `UNAVAILABLE_EXPECTATION`. Never fabricate or simulate consensus values.
2. **Deterministic-First NLP Architecture:**
   - Implement rule-based and regex-based finite-state entity resolution and numerical extraction heads first.
   - Use deterministic baselines as the ground-truth benchmark against which learned models are evaluated.
3. **Pretrained Transformer Integration:**
   - Financial classification should utilize compact, open-weights models (e.g. `ProsusAI/finbert`, `modernbert-base`) optimized for CPU inference.
   - Inference budgets must adhere to the Phase 0 architecture: $< 20\text{ms}$ on CPU. Heavy generative LLMs are strictly excluded from the hot path.
