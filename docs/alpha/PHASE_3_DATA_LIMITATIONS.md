# Phase 3 Data Limitations, Expectation Gaps, and Model Training Risks

**Document Version:** 1.0.0-alpha  
**Date:** 2026-11-10  
**Phase:** Phase 3 — Financial NLP, Entity Resolution & Numerical Surprise Detection  
**Author:** Quantitative Governance & Data Architecture  
**Status:** Canonical Research Assessment  

---

## 1. Executive Summary

A core duty of quantitative governance is to honestly document data gaps and refuse to train high-capacity deep learning models on compromised or unrepresentative datasets.

This document provides a candid audit of the data available to TickerWatch Alpha at the conclusion of Phase 3, identifies structural limitations in raw exchange filings, explains the mathematical dangers of premature model training, and outlines the data roadmap for Phase 4.

---

## 2. Legacy Corpus Distribution and Imbalance

An audit of the primary SQLite database (`tickerwatch.db`) revealed 3,043 historical articles with the following source distribution:

| Source Name | Record Count | Percentage | Geographic Market | Key Characteristics |
|:---|:---:|:---:|:---:|:---|
| **SEC EDGAR Submissions** | 3,013 | 99.01% | United States (SEC) | 8-K, 10-Q, 10-K filings for AAPL, AMZN, MSFT. High compliance formatting. |
| **SEC News Press Releases** | 25 | 0.82% | United States (SEC) | Enforcement actions and administrative announcements. |
| **Moneycontrol Financial News** | 4 | 0.13% | India (NSE/BSE) | Sparse news headlines from Phase 1 testing. |
| **Test Fixtures** | 1 | 0.04% | Synthetic | Local integration test fixture. |
| **Total** | **3,043** | **100.0%** | Mixed | **Severe Geographic & Asset Imbalance** |

### Key Vulnerabilities
1. **Zero Native NSE/BSE Filings in Legacy Store:** While the Phase 2 `NSECorporateAnnouncementsAdapter` is architected and tested, the existing SQLite store contains virtually zero historical Indian corporate announcements.
2. **US Mega-Cap Concentration:** 99% of articles cover three US technology giants (Apple, Amazon, Microsoft).
3. **No Short-Term Derivatives Linkage:** US SEC EDGAR filings cannot be causally paired with Indian `NIFTY_FUT` Level 2 tick streams without introducing massive cross-market currency, session-time, and macroeconomic confounding variables.

---

## 3. The Expectation Gap: Absence of Consensus Estimates in Raw Feeds

In financial economics, asset prices move on **surprise ($A - E$)**, not on raw reported numbers ($A$).

### The Structural Reality of Exchange Disclosures
- **What Exchanges Provide:** When a listed company files its Q3 results with NSE or BSE, the filing contains exclusively the **reported actuals ($A$)** — e.g. net profit ₹12,434 cr, operating revenue ₹64,250 cr.
- **What Exchanges DO NOT Provide:** Regulatory filings never include consensus sell-side analyst forecasts ($E$), consensus dispersion ($\sigma$), or pre-announcement expectations.
- **The Synthetic Edge Hazard:** If a system cannot observe $E$ at timestamp $\tau_{\text{decision}}$, it cannot compute surprise. Fabricating $E$ or inferring it from post-event price moves creates **synthetic alpha** that instantly collapses in live trading.

### How Phase 3 Solved This Legally & Scientifically
1. Implemented the strict `UNAVAILABLE_EXPECTATION` protocol in `backend/alpha/surprise.py`.
2. Created a point-in-time expectation store with strict look-ahead assertions ($\tau_{\text{captured}} < \tau_{\text{decision}}$).
3. For non-consensus events (contract order wins, capex investments), implemented fundamental materiality ratios relative to annual trailing revenue ($\frac{\text{Contract Value}}{\text{TTM Revenue}}$).

---

## 4. Risks of Training Deep Transformers on Small Datasets

A common failure mode in quantitative machine learning is attempting to train or fine-tune an 82M–110M parameter Transformer (e.g. BERT/RoBERTa) from scratch on a few thousand documents:

1. **Catastrophic Overfitting:** An 110M parameter model easily memorizes 3,000 headlines, achieving 99% train accuracy while collapsing on unseen live announcements.
2. **Spurious Correlation Exploitation:** The model learns that the token `"crore"` correlates with bull market drift simply because the training sample coincided with an equity rally.
3. **Latency and Jitter Degradation:** Full transformer inference on CPU requires $25 - 50\text{ ms}$, whereas our deterministic rule-head baseline executes in **$0.287\text{ ms}$ (P50)** and **$0.428\text{ ms}$ (P95)**.

### Why Our Baseline Architecture Was the Correct Decision
- We established a sub-millisecond, deterministic rule-head baseline achieving **94.23% classification accuracy** and **100% numerical extraction accuracy**.
- We designed a clean, modular abstraction layer allowing fine-tuned compact ONNX models (FinBERT-ONNX) to be plugged in when larger, representative Indian corporate datasets are assembled.

---

## 5. Strategic Data Acquisition Roadmap for Phase 4

To support Phase 4 (Historical Event Vector Retrieval) and Phase 5 (Predictive Model Training):

1. **Procure Historical Indian Announcements:** Ingest at least 2 years of official BSE/NSE corporate disclosures across Nifty 50 and Nifty Next 50 constituents.
2. **Integrate Point-in-Time Consensus Feed:** Connect an authorized institutional consensus provider (e.g. Bloomberg Estimates / FactSet / certified local broker consensus) with strict point-in-time snapshot archiving.
3. **Expand Instrument Master:** Broaden coverage to top-20 liquid single-stock futures (`RELIANCE_FUT`, `TCS_FUT`, `INFY_FUT`, `HDFCBANK_FUT`, `LT_FUT`).
