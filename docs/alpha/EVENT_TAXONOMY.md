# TickerWatch Alpha — Event Taxonomy & Task Disambiguation

> **Document Version:** 1.0.0-alpha  
> **Status:** APPROVED BASELINE — PHASE 0  
> **Author:** Financial NLP & Quantitative Research  
> **Target Scope:** Corporate Disclosures, Regulatory Filings & Macroeconomic Releases  

---

## 1. Architectural Distinction: The 5 Orthogonal Prediction Dimensions

A critical design flaw in naive financial NLP systems is collapsing multiple distinct analytical dimensions into a single scalar "sentiment score" (e.g., mapping text to a float between -1.0 and +1.0). 

TickerWatch Alpha explicitly decouples the information parsing pipeline into **five orthogonal, non-interchangeable tasks**:

```
+----------------------------------------------------------------------------------------------------+
|                                    INFORMATION EXTRACTION LAYERS                                    |
|                                                                                                    |
|   +--------------------------------------------------------------------------------------------+   |
|   | 1. EVENT CLASSIFICATION: What structural corporate action or disclosure occurred?         |   |
|   |    Categorical taxonomy (e.g., EARNINGS_RESULTS, CONTRACT_WIN, CEO_RESIGNATION)            |   |
|   +---------------------------------------------+----------------------------------------------+   |
|                                                 |                                                  |
|   +---------------------------------------------v----------------------------------------------+   |
|   | 2. SENTIMENT / TONE: What is the linguistic and qualitative framing of the release?        |   |
|   |    Linguistic polarity of management commentary (e.g., Optimistic, Defensive, Cautious)    |   |
|   +---------------------------------------------+----------------------------------------------+   |
|                                                 |                                                  |
|   +---------------------------------------------v----------------------------------------------+   |
|   | 3. SURPRISE RELATIVE TO EXPECTATIONS: How does reported data deviate from consensus?       |   |
|   |    Quantitative Delta: Delta = (Reported - Consensus) / Dispersion                         |   |
|   +---------------------------------------------+----------------------------------------------+   |
|                                                 |                                                  |
|   +---------------------------------------------v----------------------------------------------+   |
|   | 4. MATERIALITY / REVENUE RELEVANCE: What fraction of firm valuation/turnover is at stake?  |   |
|   |    Scale Metric: Log10(Deal Value / Annual Revenue) or Percent of Enterprise Value         |   |
|   +---------------------------------------------+----------------------------------------------+   |
|                                                 |                                                  |
|   +---------------------------------------------v----------------------------------------------+   |
|   | 5. PREDICTED PRICE IMPACT: Conditional price distribution over horizon h.                  |   |
|   |    Probability Distribution: P(Delta F_{t+h} | Event, History, Market State)               |   |
|   +--------------------------------------------------------------------------------------------+   |
+----------------------------------------------------------------------------------------------------+
```

1. **Event Classification:** Discrete categorization of the disclosure type regardless of tone.
2. **Sentiment / Tone:** Qualitative language polarity (can be highly positive even when reported numbers miss consensus).
3. **Surprise Relative to Expectations:** The signed deviation of reported metrics from prior consensus estimates ($\Delta_{\text{surprise}} = \frac{X_{\text{reported}} - X_{\text{consensus}}}{\sigma_{\text{consensus}}}$).
4. **Materiality:** Economic significance and scale of the announcement relative to enterprise value, annual turnover, or capital base.
5. **Predicted Price Impact:** The expected directional and volatility response of the financial contract, conditioned on market state and historical analogues.

---

## 2. Comprehensive Event Taxonomy (10 Event Classes)

### Class 1: Earnings and Quarterly Financial Results
- **Typical News Characteristics:** Press releases, exchange stock filings (Regulation 33 in India, 10-Q/8-K in US), tables with Revenue, EBITDA, PAT, EPS.
- **Relevant Entities:** Reporting Company, Auditor, Consolidated Subsidiaries.
- **Positive / Negative Interpretations:**  
  *Positive:* Strong top-line and bottom-line beats, margin expansion, sequential revenue growth.  
  *Negative:* Margin compression, high exceptional losses, rising debt servicing costs, tax write-downs.
- **Relevant Numerical Information:** Net Profit (PAT), Revenue/Turnover, Operating Margin (EBITDA %), Diluted EPS, YoY and QoQ percentage changes.
- **Anticipation State:** Highly anticipated (scheduled reporting calendar known weeks in advance; price often drifts ahead of announcement).
- **Historical Comparisons:** Prior 4 quarters of results, peer results within the same sector during the active reporting season.
- **Market Impact Mechanism:** Immediate revaluation of future earnings multiples; sharp order-book repricing and volume spikes.
- **Data Availability Concerns:** Tables often formatted as embedded PDF filings on stock exchanges (BSE/NSE corporate announcements), requiring robust tabular OCR or structured JSON feeds.

### Class 2: Revenue and Profit Guidance Revisions
- **Typical News Characteristics:** Management conference call transcripts, investor presentations, mid-quarter updates.
- **Relevant Entities:** Executive Management (CEO/CFO), Industry Sector peers.
- **Positive / Negative Interpretations:**  
  *Positive:* Upward revision of FY constant-currency revenue guidance, raised Capex plans with expanding order pipelines.  
  *Negative:* Downward revision, delayed order delivery schedules, supply chain bottleneck warnings.
- **Relevant Numerical Information:** Projected revenue range, percentage growth targets, projected margin guidance.
- **Anticipation State:** Unscheduled and low anticipation; highly volatile market response.
- **Historical Comparisons:** History of management conservatism or track record of meeting/missing stated guidance.
- **Market Impact Mechanism:** Shifts the discount curve and structural growth expectations across multi-year discounted cash flow models.
- **Data Availability Concerns:** Often released during earnings calls; real-time audio transcripts have variable publication latency (10 minutes to several hours).

### Class 3: Major Corporate Contracts and Orders
- **Typical News Characteristics:** "Company X bags Rs 5,000 Crore order from Ministry of Railways", turnkey EPC contract announcements, long-term SaaS vendor agreements.
- **Relevant Entities:** Supplier/Vendor, Client/Government Agency, Joint Venture Partners.
- **Positive / Negative Interpretations:**  
  *Positive:* High-margin order book expansion, validation of technological leadership.  
  *Negative:* Fixed-price contracts vulnerable to raw material inflation, client concentration risk, low-margin pass-through deals.
- **Relevant Numerical Information:** Total contract value (TCV), execution timeframe (e.g., 24 months), advance payments, liquidated damages clauses.
- **Anticipation State:** Moderately unanticipated, though tender bid openings may be partially known in specialized trade circles.
- **Historical Comparisons:** Historical order inflow run-rate, book-to-bill ratio.
- **Market Impact Mechanism:** Immediate positive demand shock for capital goods/EPC/IT stocks, proportional to contract size relative to annual turnover.
- **Data Availability Concerns:** Exact commercial details and margin terms are frequently redacted or labeled "undisclosed amount".

### Class 4: Mergers, Acquisitions and Strategic Transactions
- **Typical News Characteristics:** Board approvals for amalgamation, definitive agreement signing, open offer announcements, divestment of non-core assets.
- **Relevant Entities:** Acquirer, Target, Investment Bankers, Regulatory Approvers (CCI, SEBI, FTC).
- **Positive / Negative Interpretations:**  
  *Positive:* Accretive valuation multiples, strategic synergy, debt reduction through asset sale.  
  *Negative:* Dilutive share issuance, overpayment/goodwill bubble, heavy leverage required to finance buyout.
- **Relevant Numerical Information:** Deal size, enterprise valuation multiples (EV/EBITDA, P/E), swap ratios, debt burden.
- **Anticipation State:** Frequently preceded by media leaks, unusual option activity, and speculative press reports.
- **Historical Comparisons:** Conglomerate acquisition history, historical post-merger integration success.
- **Market Impact Mechanism:** Divergent price impact: target stock rallies toward offer price; acquirer stock often drops due to financing risk and deal premium.
- **Data Availability Concerns:** Multi-stage disclosures (rumour $\rightarrow$ board consideration $\rightarrow$ definitive agreement $\rightarrow$ regulatory approval) create serial, overlapping news items.

### Class 5: Regulatory and Government Decisions
- **Typical News Characteristics:** DGFT export restrictions, windfall tax adjustments, tariff revisions, SEBI/RBI policy circulars, price caps on pharmaceuticals.
- **Relevant Entities:** Government Ministry (Finance, Commerce, Petroleum), Regulatory Bodies (SEBI, RBI, CCI, FDA), Impacted Industries.
- **Positive / Negative Interpretations:**  
  *Positive:* Subsidies, Production-Linked Incentive (PLI) scheme approvals, tariff protection against imports.  
  *Negative:* Export duties, price controls, punitive compliance mandates.
- **Relevant Numerical Information:** Tariff percentages, subsidy allocations, compliance fines, effective implementation dates.
- **Anticipation State:** Partially anticipated ahead of policy meetings, but specific timing and rate thresholds are discrete shocks.
- **Historical Comparisons:** Past sector tariff shocks, historical revenue elasticities to duty adjustments.
- **Market Impact Mechanism:** Sector-wide structural repricing; simultaneous impact across multiple equity constituents.
- **Data Availability Concerns:** Official government gazettes and press information bureaus often publish with timestamp ambiguities or via unformatted press releases.

### Class 6: Material Litigation and Legal Developments
- **Typical News Characteristics:** Court rulings (Supreme Court, NCLT, High Court), tax tribunal verdicts, patent dispute outcomes, environmental green bench bans.
- **Relevant Entities:** Plaintiff, Defendant, Judicial Authority, Legal Counsel.
- **Positive / Negative Interpretations:**  
  *Positive:* Dismissal of retrospective tax demand, injunction against competitor patent infringement.  
  *Negative:* Treble damages, injunction stopping manufacturing operations, substantial penalty enforcement.
- **Relevant Numerical Information:** Disputed amount, penalty magnitude, percentage of net worth affected.
- **Anticipation State:** Court hearing dates are scheduled, but judgment timing during trading hours creates sharp binary volatility.
- **Historical Comparisons:** Precedent cases, company provisions previously set aside on balance sheet.
- **Market Impact Mechanism:** Binary re-evaluation of legal risk overhang; immediate relief rally or sudden gap-down.
- **Data Availability Concerns:** Legal proceedings are frequently reported informally by court reporters via social media/financial wires minutes before official written orders appear.

### Class 7: Management Changes (Executive Leadership)
- **Typical News Characteristics:** Sudden resignation or appointment of CEO, CFO, Managing Director, or Independent Board Directors; promoter disputes.
- **Relevant Entities:** Outgoing Executive, Incoming Executive, Nomination and Remuneration Committee.
- **Positive / Negative Interpretations:**  
  *Positive:* Appointment of highly regarded industry veteran, clean resolution of governance dispute.  
  *Negative:* Unexpected CFO resignation citing personal reasons (often a red flag for accounting irregularities), promoter-board feud.
- **Relevant Numerical Information:** Tenure remaining, severance packages, promoter shareholding voting power.
- **Anticipation State:** Scheduled retirements are fully anticipated; abrupt resignations are complete surprise shocks.
- **Historical Comparisons:** Market reaction to prior leadership changes at the firm; track record of incoming executive at prior companies.
- **Market Impact Mechanism:** Governance risk premium adjustments; multiple compression if integrity is questioned.
- **Data Availability Concerns:** Letters of resignation filed with exchanges often contain critical contextual nuances in attached PDF annexures.

### Class 8: Sector-Specific Developments
- **Typical News Characteristics:** Monthly automotive sales figures, telecom subscriber additions (TRAI data), airline passenger load factors, refinery crack spreads.
- **Relevant Entities:** Industry Associations (SIAM, FADA), Sector Leaders.
- **Positive / Negative Interpretations:**  
  *Positive:* Demand acceleration, market share gains over competitors.  
  *Negative:* Inventory build-up at dealerships, discounting pressures, rural demand slowdown.
- **Relevant Numerical Information:** Units sold, YoY growth percentage, average revenue per user (ARPU), passenger kilometers.
- **Anticipation State:** Highly anticipated scheduled releases (e.g., auto sales on the 1st of every month in India).
- **Historical Comparisons:** Seasonality patterns (festive season demand, monsoon effects).
- **Market Impact Mechanism:** Sector-wide dispersion trades; pairs-trading opportunities between market share winners and losers.
- **Data Availability Concerns:** Varying reporting standards across manufacturers; preliminary versus final figures.

### Class 9: Institutional Portfolio Changes and Index Rebalancing
- **Typical News Characteristics:** MSCI / FTSE / Nifty index reconstitution notices, bulk and block deal reports from exchange post-market data, promoter pledge disclosures.
- **Relevant Entities:** Index Providers (MSCI, NSE Indices), Institutional Funds, Promoters.
- **Positive / Negative Interpretations:**  
  *Positive:* Index inclusion triggering mandatory passive buying from index funds; promoter revocation of pledged shares.  
  *Negative:* Index exclusion, substantial promoter stake liquidation via open-market blocks.
- **Relevant Numerical Information:** Estimated passive inflow/outflow ($ millions), shares traded in block window, percentage of total paid-up capital.
- **Anticipation State:** Provisional cut-off dates are heavily analyzed by quantitative sell-side analysts; final confirmation triggers execution flows.
- **Historical Comparisons:** Historical tracking error, past price drift into effective rebalancing date.
- **Market Impact Mechanism:** Predictable liquidity flow and market-impact dynamics; structural dislocation between price and fundamentals.
- **Data Availability Concerns:** Index announcements typically occur after market close; intra-day block windows (08:45–09:00 IST and 14:05–14:20 IST) require fast feed processing.

### Class 10: Significant Macroeconomic Announcements
- **Typical News Characteristics:** Central bank monetary policy committee (MPC) statements, repo rate decisions, CPI/WPI inflation data, GDP growth prints, Federal Reserve FOMC minutes.
- **Relevant Entities:** Central Banks (RBI, Fed), Statistics Agencies (MOSPI), Monetary Policy Committee members.
- **Positive / Negative Interpretations:**  
  *Positive:* Rate cuts, liquidity easing, benign inflation, robust infrastructure spending prints.  
  *Negative:* Hawkish pauses, rate hikes, inflation surges, currency depreciation pressures.
- **Relevant Numerical Information:** Basis point change in policy rate, headline CPI percentage, GDP real growth rate, stance change ("neutral" vs "withdrawal of accommodation").
- **Anticipation State:** Highly anticipated scheduled events; extensive Bloomberg/Reuters consensus polling available.
- **Historical Comparisons:** Historical sensitivity of Nifty / Bank Nifty to rate decisions under varying inflationary regimes.
- **Market Impact Mechanism:** System-wide macro repricing affecting interest-rate sensitive sectors (banking, auto, real estate) and benchmark index futures (`NIFTY_FUT`, `BANKNIFTY_FUT`).
- **Data Availability Concerns:** Policy statements often released via live video/speech; policy resolution PDF released concurrently on RBI website.

---

## 3. Initial Research Subset: The Primary 3 Event Families

To ensure rigorous scientific discipline, **we strictly narrow the initial model development to three high-conviction event families**:

```
+----------------------------------------------------------------------------------------------------+
|                                    INITIAL RESEARCH SUBSET                                         |
|                                                                                                    |
|   +--------------------------+  +--------------------------+  +--------------------------------+   |
|   | FAMILY 1:                |  | FAMILY 2:                |  | FAMILY 3:                      |   |
|   | Corporate Quarterly      |  | Major Corporate Contract |  | High-Impact Macro Releases     |   |
|   | Earnings & Results       |  | & Order Inflow Wins      |  | (RBI Policy & CPI Prints)      |   |
|   +--------------------------+  +--------------------------+  +--------------------------------+   |
|   | * High volume of events  |  | * Discrete announcements |  | * Discrete scheduled releases  |   |
|   | * Clear consensus data   |  | * High idiosyncratic link|  | * Systemic index impact        |   |
|   | * Discrete filings       |  | * Measurable deal value  |  | * Direct NIFTY_FUT sensitivity |   |
|   +--------------------------+  +--------------------------+  +--------------------------------+   |
+----------------------------------------------------------------------------------------------------+
```

### Justification for Selected Subset:
1. **Corporate Quarterly Results (Family 1):** Provides the gold standard for testing **Surprise vs. Sentiment** ($H1$). Consensus figures are quantitatively documented, allowing us to evaluate whether the AI accurately extracts the delta between reported results and expectations.
2. **Major Corporate Contracts and Orders (Family 2):** Offers an ideal environment for testing **Materiality and Idiosyncratic Impact**. Announcements occur randomly during trading hours, providing clean, uncontaminated event windows for measuring information diffusion and latency decay ($H4$).
3. **High-Impact Macro Releases (Family 3):** Directly impacts our provisional research instrument (**`NIFTY_FUT`**). Macro announcements occur at known timestamps with widespread market attention, allowing precise synchronization of order-book market state ($H3$) and execution slip.
