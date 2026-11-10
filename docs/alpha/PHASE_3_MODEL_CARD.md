# Model Card — TickerWatch Alpha Financial NLP & Event Engine (v1.0.0-alpha)

Following standard ML model reporting guidelines (Mitchell et al., *Model Cards for Model Reporting*, 2019).

---

## 1. Model Details

- **Model Name:** TickerWatch Alpha Financial Event NLP & Extraction Engine
- **Model Version:** `1.0.0-alpha`
- **Model Date:** 2026-11-10
- **Model Type:** Hybrid Hierarchical Financial Classifier and Numerical Extraction Engine with Deterministic Rule-Head Baseline and Modular FinBERT-ONNX Interface.
- **Primary Maintainer:** Quantitative Governance & Engineering Architecture
- **Repository:** `TickerWatch` (`coolbros/TickerWatch`)
- **License:** Proprietary Research Baseline; sub-components leverage open Apache-2.0 / MIT patterns.

---

## 2. Intended Use

### In-Scope Uses
- Parsing and structuring real-time and historical financial news disclosures.
- Disambiguating company names, tickers, subsidiaries, and executives into canonical financial instruments.
- Categorizing disclosures into a 5-family, 25-subtype hierarchical financial event taxonomy.
- Normalizing quantitative quantities (INR Lakh/Crore, USD/EUR Million/Billion, percentages, basis points, ranges, and parenthesized losses).
- Computing causal point-in-time financial surprise ($S = A - E, S_{\%}, Z$).
- Estimating composite event materiality $[0.0, 1.0]$ and assigning impact tiers (Routine, Moderate, High).
- Providing machine-readable `FinancialEventRepresentation` vectors for Phase 4 retrieval and Phase 5 predictive models.

### Out-of-Scope / Prohibited Uses
- Direct automated trade execution without downstream risk and cost modeling.
- General-purpose open-domain chatbot interactions.
- Options volatility surface modeling or non-linear derivative pricing.
- Retail investment recommendations or financial advisory services.

---

## 3. Factors and Calibration Data

- **Evaluation Dataset:** 52 hand-curated, multi-source financial test cases representing:
  - Indian corporate filings (NSE/BSE announcements for large-caps like Reliance, TCS, Infosys, HDFC Bank, ICICI Bank, L&T, SBI, Airtel, ITC, Kotak).
  - Indian macroeconomic releases (RBI MPC monetary policy decisions, CPI inflation, SEBI enforcement penalties).
  - Routine administrative notices (board meeting intimations, trading window closures, share certificate duplicates).
  - Adversarial English stopword collisions ("IT", "AT", "NOW", "CAN").
  - US SEC EDGAR filings (Apple, Amazon, Microsoft).

---

## 4. Quantitative Metrics and Empirical Performance

All benchmarks measured on standard 64-bit CPU (4 cores, 2.8 GHz, Windows 11 host environment):

| Metric | Measured Baseline | Target Gate |
|:---|:---:|:---:|
| **Entity Precision** | 92.11% | $\ge 85.0\%$ |
| **Entity Recall** | 94.59% | $\ge 85.0\%$ |
| **Entity F1 Score** | **93.33%** | $\ge 85.0\%$ |
| **Ticker Collision False-Positive Rate** | **0.00%** | $\le 5.0\%$ |
| **Event Classification Accuracy** | **94.23%** | $\ge 85.0\%$ |
| **Numerical Extraction Accuracy** | **100.00%** | $\ge 85.0\%$ |
| **Materiality Tier Accuracy** | **76.92%** | N/A |
| **Execution Latency (P50)** | **0.287 ms** | $< 2.0\text{ ms}$ |
| **Execution Latency (P95)** | **0.428 ms** | $< 5.0\text{ ms}$ |
| **Execution Latency (P99)** | **4.604 ms** | $< 10.0\text{ ms}$ |
| **Process Memory RSS** | **32.18 MB** | $< 200.0\text{ MB}$ |

---

## 5. Causal Integrity & Falsification Safeguards

1. **Zero Look-Ahead Bias:** Expectations registered with timestamp $\ge \tau_{\text{decision}}$ strictly raise `ValueError`.
2. **Missing Expectation Honesty:** If no sell-side consensus estimate was recorded prior to the event timestamp, the model outputs `UNAVAILABLE_EXPECTATION`. It never fabricates, hallucinates, or silently imputes values.
3. **Division-by-Zero Protection:** Percentage surprise explicitly guards against zero denominators ($|E| < 1\times 10^{-9}$), falling back safely to the sign of absolute surprise.
4. **Negative Consensus Handling:** By evaluating $S_{\%} = \frac{A - E}{|E|} \times 100$ using $|E|$ in the denominator, companies reporting smaller losses than expected are correctly classified as positive surprise beats.

---

## 6. Caveats and Limitations

- **Expectation Coverage:** Consensus estimates are currently absent from raw public exchange feeds. An authorized commercial consensus provider (e.g. FactSet / Bloomberg) is required for full point-in-time surprise computation.
- **Corpus Imbalance:** The legacy `tickerwatch.db` database is 99% US SEC EDGAR filings. Dedicated live scraping/feed subscriptions to NSE/BSE announcements are needed for large-scale Indian market backtesting.
