# Phase 4 Historical Data Acquisition & Market Sources Research

**Document Version:** 1.0.0-alpha  
**Date:** 2026-11-10  
**Phase:** Phase 4 — Historical Event Vector Retrieval & Contextual Intelligence  
**Author:** Agastya Varshney <agastyavarshney@gmail.com>  
**Status:** Canonical Research Specification  

---

## 1. Executive Summary

A predictive research system conditioning future market behavior on historical analogues:
$$P(\Delta F_{t+h} \mid E_t, H_t, M_t)$$
requires an expansive, accurately timestamped historical event corpus ($H_t$). 

Phase 3 established that the current TickerWatch local database contains 3,043 articles overwhelmingly dominated by US SEC EDGAR filings for three mega-cap issuers (`AAPL`, `AMZN`, `MSFT`), with virtually non-existent Indian corporate coverage (4 news articles) and a complete absence of structured consensus expectations.

To establish genuine historical intelligence, this research document evaluates available institutional, exchange, and regulatory data sources across both the **Indian** and **US** markets. It articulates explicit licensing boundaries, access protocols, rate limits, retention rights, timestamp precision, and provider-neutral import interfaces.

---

## 2. Indian Market Data Sources Research

```
+----------------------------------------------------------------------------------------------------------------+
|                                    INDIAN HISTORICAL DATA SOURCES AUDIT                                        |
+----------------------+--------------------+--------------------+-----------------------+-----------------------+
| Source / Platform    | Regulatory Status  | Coverage Scope     | Dissemination Lag     | Institutional Access  |
+----------------------+--------------------+--------------------+-----------------------+-----------------------+
| NSE Corporate        | Official Exchange  | All NSE Listed     | Sub-second broadcast  | NSE Data & Analytics  |
| Announcements Feed   | Disclosures        | (~2,200 equities)  | timestamp provided    | (Feed License Req.)   |
+----------------------+--------------------+--------------------+-----------------------+-----------------------+
| BSE Corporate        | Official Exchange  | All BSE Listed     | Millisecond-precision | BSE Data Feed /       |
| Disseminations       | Filings (XBRL/PDF) | (~5,400 equities)  | dissemination time    | Direct Member API     |
+----------------------+--------------------+--------------------+-----------------------+-----------------------+
| RBI Monetary Policy  | Central Bank       | Repo rate, MPC,    | Scheduled bi-monthly; | Public Press Releases |
| Statements           | Official (Govt)    | SLR/CRR, Banking   | exact second embargo  | & Speeches Archive    |
+----------------------+--------------------+--------------------+-----------------------+-----------------------+
| MOSPI Macroeconomic  | Official Govt.     | CPI Inflation,     | Scheduled 17:30 IST;  | Open Data Govt Portal |
| Statistics (PIB)     | Statistics Office  | IIP, GDP Quarterly | monthly releases      | (data.gov.in / PIB)   |
+----------------------+--------------------+--------------------+-----------------------+-----------------------+
| Company Investor     | Issuer Official    | Earnings decks,    | Post-market or market | Issuer IR Portals /   |
| Relations (IR)       | Disclosures        | call transcripts   | hours; PDF / webcasts | Stock Exchange Depots |
+----------------------+--------------------+--------------------+-----------------------+-----------------------+
```

### 2.1 National Stock Exchange of India (NSE)

- **Coverage Scope:** Mandated continuous corporate disclosures under Regulation 30 of SEBI (LODR) Regulations, 2015 for ~2,200 listed companies. Includes board meeting outcomes, earnings declarations, dividend recommendations, major contract wins, credit rating adjustments, M&A announcements, and capital expenditures.
- **Timestamp Fidelity:** High. The official exchange feed records `BROADCAST_DATE_TIME` (second precision: `DD-Mon-YYYY HH:MM:SS`) when the exchange disseminates the filing to the public.
- **Access Protocol & Licensing:**
  - *Public Portal:* NSE archives HTML/PDF disclosures via `nsearchives.nseindia.com`. Automated unauthenticated scraping is strictly prohibited under NSE Terms of Use and subject to IP rate limiting, WAF blocking, and legal action.
  - *Authorized Institutional Access:* NSE Data & Analytics Ltd provides authorized real-time and historical corporate announcement feeds (`Corporate Announcements Datafeed`) via TCP/IP multicast or SFTP. Requires commercial data licensing.
- **Historical Depth:** Archives extend back over 15 years in structured and PDF formats.

### 2.2 Bombay Stock Exchange (BSE)

- **Coverage Scope:** Complete universe of ~5,400 listed equities. Provides machine-readable XBRL instances for financial results alongside PDF attachments.
- **Timestamp Fidelity:** Excellent. BSE dissemination records carry `DISSEMINATION_TIME` down to the second/millisecond (`DD/MM/YYYY HH:MM:SS.mmm`).
- **Access Protocol & Licensing:**
  - *Public Portal:* BSE corporate announcement board exposes search by scrip code and date range. Scraping public endpoints violates BSE Terms of Service.
  - *Authorized Institutional Access:* BSE Market Data Feed Services offers dedicated Corporate Announcement feeds via API and SFTP.

### 2.3 Reserve Bank of India (RBI) & Macro Releases

- **Coverage Scope:** Official Monetary Policy Committee (MPC) rate decisions, bi-monthly policy statements, Repo/Reverse Repo rate adjustments, Cash Reserve Ratio (CRR) updates, Forex reserve bulletins, and regulatory directions to commercial banks.
- **Timestamp Fidelity:** Fixed scheduled announcement embargoes (typically 10:00 IST on MPC decision day). Press releases published immediately upon embargo lift.
- **Access Protocol & Licensing:** Public domain government information published under the Open Data License India. Freely downloadable for research and commercial analytical use with proper attribution.

### 2.4 Ministry of Statistics and Programme Implementation (MOSPI) / PIB

- **Coverage Scope:** Consumer Price Index (CPI) inflation, Index of Industrial Production (IIP), and quarterly Gross Domestic Product (GDP) growth rates.
- **Timestamp Fidelity:** Fixed scheduled releases (typically 17:30 IST on the 12th of each month for CPI/IIP).
- **Access Protocol & Licensing:** Open Government Data Platform India (`data.gov.in`) and Press Information Bureau (`pib.gov.in`). Unrestricted non-exclusive use.

---

## 3. US Market Data Sources Research

```
+----------------------------------------------------------------------------------------------------------------+
|                                       US HISTORICAL DATA SOURCES AUDIT                                         |
+----------------------+--------------------+--------------------+-----------------------+-----------------------+
| Source / Platform    | Regulatory Status  | Coverage Scope     | Dissemination Lag     | Institutional Access  |
+----------------------+--------------------+--------------------+-----------------------+-----------------------+
| SEC EDGAR System     | Mandatory Federal  | All US Public Co.  | Real-time dissemination| Free HTTPS REST API  |
| (Forms 8-K, 10-Q/K)  | Securities Filings | (~7,000 issuers)   | acceptance timestamp  | (User-Agent header)   |
+----------------------+--------------------+--------------------+-----------------------+-----------------------+
| SEC Enforcement      | Regulatory Legal   | Administrative     | Irregular intraday;   | Official Public SEC   |
| & Litigation Feed    | Releases           | litigation actions | minutes to hours      | RSS / Atom Feeds      |
+----------------------+--------------------+--------------------+-----------------------+-----------------------+
| Federal Reserve      | Central Bank       | FOMC rate decision,| Scheduled 14:00 ET;   | Federal Reserve API / |
| Economic Data (FRED) | Official Data      | macro indicators   | exact second embargo  | St. Louis FRED Portal |
+----------------------+--------------------+--------------------+-----------------------+-----------------------+
| Commercial Wire Feeds| Commercial News    | Global corporate   | Sub-second wire       | Enterprise License    |
| (PRN, BusinessWire)  | Syndication        | press releases     | dissemination         | (Direct Feed Access)  |
+----------------------+--------------------+--------------------+-----------------------+-----------------------+
```

### 3.1 SEC EDGAR System (Electronic Data Gathering, Analysis, and Retrieval)

- **Coverage Scope:** Comprehensive federal repository. Crucial forms for event retrieval:
  - `Form 8-K`: Unscheduled material corporate events (Item 2.02 Earnings, Item 1.01 Material Agreements, Item 5.02 Officer Changes, Item 1.03 Bankruptcy).
  - `Form 10-Q / 10-K`: Quarterly and annual audited financial statements with full XBRL taggings.
  - `Form 4`: Section 16 insider transactions.
- **Timestamp Fidelity:** Absolute gold standard. SEC EDGAR records the exact `acceptance_datetime` (e.g. `2026-01-30 16:30:12 UTC`) when the filing passed SEC technical ingest and became available to the dissemination server.
- **Access Protocol & Licensing:** Public domain. SEC provides free access to historical filings via the official EDGAR API (`https://data.sec.gov/`). Rate limited to 10 requests per second with mandatory `User-Agent: Sample Company Name AdminContact@<sample company domain>.com` header. No copyright restrictions.

### 3.2 SEC Regulatory Enforcement & Litigation

- **Coverage Scope:** Accounting fraud charges, insider trading civil actions, market manipulation orders, and Wells notices.
- **Timestamp Fidelity:** Publication timestamp on SEC website (`sec.gov/litigation`).
- **Access Protocol & Licensing:** Free public Atom/RSS feed and historical web archive.

---

## 4. Legal, Licensing & Ethical Guardrails

TickerWatch Alpha adheres to strict quantitative engineering standards regarding data acquisition:

1. **Zero Unauthorized Scraping:** No headless browser automation or evasion of rate limiters against exchange websites (NSE, BSE).
2. **Zero Commercial Subscription Purchases Without Authorization:** No API key purchases or paid data vendor contracts without explicit user approval.
3. **Strict Separation of Synthetic Test Fixtures:** Synthetic test scenarios utilized in offline unit tests must be explicitly quarantined and labelled `is_synthetic=True`. Synthetic records must never contaminate empirical research corpora.
4. **Honest Source Accessibility Reporting:** A data source is only marked as "connected" if automated, authorized end-to-end ingestion is functioning.

---

## 5. Provider-Neutral Ingestion Architecture

To ensure that authorized datasets can be ingested without coupling core retrieval algorithms to vendor-specific payload formats, Work Package B implements the provider-neutral ingestion framework (`backend/alpha/historical_import.py`):

```
+-----------------------------------------------------------------------------+
|                     PROVIDER-NEUTRAL INGESTION FRAMEWORK                    |
+-----------------------------------------------------------------------------+
                                      |
         +----------------------------+----------------------------+
         |                                                         |
         v                                                         v
 [ Indian Ingest Pipeline ]                               [ US Ingest Pipeline ]
         |                                                         |
  +------+------+                                           +------+------+
  |      |      |                                           |      |      |
  v      v      v                                           v      v      v
 NSE    BSE    RBI                                         SEC    SEC    FED
 Corp  Filings Macro                                       8-K    10-Q   Macro
  |      |      |                                           |      |      |
  +------+------+                                           +------+------+
         |                                                         |
         v                                                         v
 [ NSECorporateAnnouncementImporter ]                     [ SECFilingsImporter ]
 [ BSEFilingsImporter ]                                   [ GenericJSONLImporter ]
 [ RBIMacroReleaseImporter ]                                       |
         |                                                         |
         +----------------------------+----------------------------+
                                      |
                                      v
                        [ HistoricalImportRecord ]
                        - Strict Causal Validation
                        - system_available_at >= event_timestamp
                        - Explicit MarketJurisdiction (IN / US)
                        - Immutable SHA-256 Fingerprint
                                      |
                                      v
                        [ Persistent Historical Store ]
```

### Ingestion Contract Guarantees:
- **Causality Verification:** Automatic assertion that `system_available_at_utc >= event_timestamp_utc`. Look-ahead violations trigger immediate rejection.
- **Namespace Isolation:** Datasets are strictly partitioned by `MarketJurisdiction` (`IN` vs `US`). US corporate events can never be retrieved as analogues for Indian market equity events.
- **Deterministic Fingerprinting:** Content hashes prevent duplicate insertions across revised filings.
