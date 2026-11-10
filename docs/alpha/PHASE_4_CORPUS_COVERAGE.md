# Phase 4 Historical Event Corpus Coverage & Gap Analysis

**Document Version:** 1.0.0-alpha  
**Date:** 2026-11-10  
**Phase:** Phase 4 — Historical Event Vector Retrieval & Contextual Intelligence  
**Author:** Agastya Varshney <agastyavarshney@gmail.com>  
**Status:** Empirical Corpus Audit & Expansion Blueprint  

---

## 1. Empirical Corpus Audit

An exhaustive audit of the TickerWatch local SQLite database (`tickerwatch.db`) and research dataset repository (`data/research_datasets/`) establishes the current baseline inventory of financial event text.

```
+---------------------------------------------------------------------------------------------------------+
|                                    CURRENT HISTORICAL EVENT CORPUS INVENTORY                            |
+------------------------------+---------------+---------------+--------------------+---------------------+
| Feed / Source Connector      | Jurisdiction  | Total Records | Entity Scope       | Date Span           |
+------------------------------+---------------+---------------+--------------------+---------------------+
| `sec_edgar`                  | US            | 3,013         | AAPL, AMZN, MSFT   | 2020-01 to 2026-11  |
| `sec_news_press`             | US            | 25            | SEC Regulatory     | 2026-10 to 2026-11  |
| `moneycontrol_market_reports`| IN            | 4             | General Market     | 2026-11             |
| `test_api_conn` (synthetic)  | GLOBAL        | 1             | Test Issuer        | N/A                 |
+------------------------------+---------------+---------------+--------------------+---------------------+
| TOTAL OPERATIONAL ARTICLES   |               | 3,043         | 3 US, 0 Indian Co. |                     |
+------------------------------+---------------+---------------+--------------------+---------------------+
```

### Key Observations:
1. **Severe Geographic Asymmetry:** 99.8% of historical text records originate from the US market (primarily SEC EDGAR Form 4 and periodic reports). Indian market representation is virtually absent (0.13%, 4 market summary wrap-ups).
2. **Entity Concentration:** The US corpus covers only three mega-cap issuers: Apple Inc. (`AAPL`), Amazon.com Inc. (`AMZN`), and Microsoft Corporation (`MSFT`).
3. **Consensus Forecast Void:** 0.0% of existing records possess pre-event consensus estimates ($E$).
4. **Historical Market Data Gap:** While Phase 2 implemented synthetic and benchmark market alignment feeds, extensive tick-level or 1-minute historical OHLCV data for Indian equities is not currently bundled in the repo.

---

## 2. Research Implications & Explicit Guardrails

The empirical findings dictate clear methodological guardrails for Phase 4:

### Rule 1: No Cross-Jurisdiction Analogue Substitution
Under no circumstances may US corporate events (e.g. an Apple 8-K contract win) be retrieved as historical analogues for an Indian corporate event (e.g. a Larsen & Toubro order win). The market microstructures, liquidity regimes, retail participation ratios, currency dynamics, and price discovery mechanisms differ fundamentally. Datasets must be strictly partitioned:
- Namespace `CORPUS_IN`: Indian equity, derivatives, and macroeconomic announcements.
- Namespace `CORPUS_US`: US equity and macroeconomic filings.

### Rule 2: Explicit Handling of Absent Consensus
Because consensus expectations are missing from raw regulatory feeds, the numerical surprise engine must continue to output explicit `UNAVAILABLE_EXPECTATION` states. Retrieval models must not assume surprise features are populated for every event.

### Rule 3: Insufficient-Evidence Safeguard in Contextual Distributions
When querying historical analogues for Indian stocks with sparse coverage, the contextual feature engine must output `insufficient_evidence=True` rather than synthesizing an artificial distribution from 1 or 2 isolated points.

---

## 3. Corpus Expansion Roadmap

To support robust statistical testing in Phase 4 and Phase 5 predictive modelling, the following dataset expansion roadmap is prioritized:

```
+--------------------------------------------------------------------------------------------------------+
|                                    CORPUS EXPANSION MILESTONES & TARGETS                               |
+-----------+--------------------+-------------------------+--------------------+------------------------+
| Milestone | Target Market      | Proposed Source         | Target Size        | Event Scope            |
+-----------+--------------------+-------------------------+--------------------+------------------------+
| Stage 1   | Indian Equities    | NSE Corporate Filings   | 5,000 events       | NIFTY 50 constituents  |
|           |                    | Authorized Archive      | (FY22 - FY26)      | Earnings, Capex, Wins  |
+-----------+--------------------+-------------------------+--------------------+------------------------+
| Stage 2   | Indian Macro       | RBI Press Bulletins &   | 150 releases       | Policy Rates, Inflation|
|           |                    | MOSPI Releases (PIB)    | (2018 - 2026)      | CPI, GDP, IIP          |
+-----------+--------------------+-------------------------+--------------------+------------------------+
| Stage 3   | US Equities        | SEC EDGAR 8-K / 10-Q    | 25,000 events      | S&P 100 Tech/Industrial|
|           |                    | Bulk Institutional Dump | (2018 - 2026)      | Item 2.02, 1.01, 5.02  |
+-----------+--------------------+-------------------------+--------------------+------------------------+
| Stage 4   | Consensus Data     | Broker Consensus        | 2,000 events       | EPS / Revenue Consensus|
|           |                    | Aggregation             | (FY24 - FY26)      | for NIFTY 50 Earnings  |
+-----------+--------------------+-------------------------+--------------------+------------------------+
```

---

## 4. Dataset Versioning & Manifest Standard

All imported historical event corpora must be accompanied by an immutable JSON manifest:

```json
{
  "dataset_id": "alpha_corpus_in_v1",
  "jurisdiction": "IN",
  "version": "1.0.0",
  "record_count": 5000,
  "date_range_start_utc": "2022-04-01T00:00:00Z",
  "date_range_end_utc": "2026-03-31T23:59:59Z",
  "sha256_checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "provenance": {
    "provider": "NSE Data & Analytics / Official Disclosures",
    "ingested_by": "NSECorporateAnnouncementImporter",
    "license": "Authorized Internal Quantitative Research"
  }
}
```

This ensures full reproducibility for all downstream vector indexing and event-study calculations.
