# TickerWatch Alpha — Research Charter

> **Document Version:** 1.0.0-alpha  
> **Status:** APPROVED BASELINE — PHASE 0  
> **Author:** Quantitative Research & Architecture Team  
> **System:** TickerWatch Alpha (Predictive Financial Intelligence Engine)  
> **Target Domain:** High-Frequency / Low-Latency News-Conditioned Predictive Intelligence  

---

## 1. Executive Summary & Project Thesis

TickerWatch Alpha investigates whether artificial intelligence can establish an empirical, reproducible predictive information advantage by interpreting financial news and corporate disclosures faster and more accurately than baseline methods (keyword filters, generic sentiment dictionaries, and unconditioned price models).

### 1.1 The Central Hypothesis
Financial markets operate under conditions of bounded rationality and heterogeneous information processing speed. Public financial information is not incorporated into market prices instantaneously or frictionlessly. Rather:
1. **Semantic Complexity:** Corporate disclosures contain non-linear linguistic structures, accounting nuances, revision statements, and forward guidance that cannot be captured by surface polarity or keyword counting.
2. **Expectation Disconnect:** Market pricing reflects prevailing consensus expectations. An ostensibly positive factual development (e.g., $+15\%$ year-over-year revenue growth) can be economically bearish if the consensus expected $+25\%$.
3. **Historical Context:** Similar economic disclosures have produced asymmetric historical distributions of price action depending on macroeconomic regimes, sector positioning, and firm-level vulnerability.
4. **Latency Horizon:** Price formation following discrete unstructured events follows an information diffusion curve. A low-latency AI pipeline capable of parsing, contextualising, and conditioning predictions on market state within a tight execution budget may capture short-lived directional mispricings before broad liquidity repricing completes.

### 1.2 Non-Goals and Explicit Constraints
- **Not a Conventional Technical Analysis Engine:** TickerWatch Alpha does not seek alpha from standalone moving averages, RSI oscillators, MACD crossovers, or generic candlestick patterns. Microstructure and order-flow features serve strictly to *condition* the event-driven signal, not replace it.
- **Unproven Profitability:** Net economic profitability is explicitly treated as an unverified scientific hypothesis. The research architecture is specifically designed to falsify the thesis under realistic transaction costs, execution delay, and market impact.
- **Strictly No Options in Phase 0:** The initial strategy scope investigates directional futures only. Non-linear derivatives (options, implied volatility surfaces, skew, and Greeks) introduce multi-factor confounding variables that must not obscure the core directional evaluation.
- **Strictly Simulated:** No capital allocation, order routing, or live broker execution is authorized.

---

## 2. Core Hypotheses

Every hypothesis is formulated as an individually testable proposition with a formal null hypothesis ($H_0$), an alternative hypothesis ($H_1$), explicit mathematical variables, an empirical test protocol, and pre-committed falsification thresholds.

```
+----------------------------------------------------------------------------------------------------+
|                                    HYPOTHESIS DEPENDENCY GRAPH                                     |
|                                                                                                    |
|   +--------------------------+          +--------------------------------+                         |
|   | H1: Financial NLP        |          | H2: Historical Contextualiser  |                         |
|   | Extraction > Baselines   |          | Retrieval > Article Alone      |                         |
|   +------------+-------------+          +---------------+----------------+                         |
|                |                                        |                                          |
|                +-------------------+--------------------+                                          |
|                                    |                                                               |
|                                    v                                                               |
|                     +-------------------------------+                                              |
|                     | H3: Market Conditioning       |                                              |
|                     | NLP + Context + State > NLP   |                                              |
|                     +---------------+---------------+                                              |
|                                     |                                                              |
|                     +---------------+---------------+                                              |
|                     |                               |                                              |
|                     v                               v                                              |
|      +------------------------------+ +------------------------------+                             |
|      | H4: Latency Sensitivity      | | H5: Economic Viability       |                             |
|      | Alpha Decays with Delay      | | Positive Net P&L Post-Costs  |                             |
|      +------------------------------+ +------------------------------+                             |
+----------------------------------------------------------------------------------------------------+
```

---

### H1 — Financial Interpretation
> **Proposition:** A domain-specialised financial Natural Language Processing model extracts economically meaningful, directionally predictive information from unstructured financial disclosures that is absent from keyword-based rules or generic lexicon sentiment baselines.

- **Null Hypothesis ($H_{0,1}$):**  
  The out-of-sample directional prediction accuracy $\text{DA}(M_{\text{NLP}})$ and Mean Absolute Error $\text{MAE}(M_{\text{NLP}})$ of a compact financial Transformer on post-announcement price return $\Delta F_{t+h}$ do not exceed those of a keyword/lexicon baseline model $M_{\text{Base}}$:
  $$\mathbb{E}\left[\text{DA}(M_{\text{NLP}})\right] \le \mathbb{E}\left[\text{DA}(M_{\text{Base}})\right] \quad \text{and} \quad \mathbb{E}\left[\text{MAE}(M_{\text{NLP}})\right] \ge \mathbb{E}\left[\text{MAE}(M_{\text{Base}})\right]$$
- **Alternative Hypothesis ($H_{1,1}$):**  
  A compact financial Transformer achieves statistically significant higher directional accuracy and lower prediction error over baseline across validation partitions:
  $$\mathbb{E}\left[\text{DA}(M_{\text{NLP}})\right] > \mathbb{E}\left[\text{DA}(M_{\text{Base}})\right] \quad (p < 0.01 \text{ under Diebold-Mariano test})$$
- **Observable Variables:**
  - $E_t$: Extracted event features (entity, event class, numeric deviation from consensus $\Delta_{\text{surprise}}$, forward guidance tone).
  - $\Delta F_{t+h}$: Forward futures mid-price return over horizon $h \in \{1\text{s}, 5\text{s}, 30\text{s}, 5\text{min}\}$.
  - $\hat{y}_t \in \{-1, 0, 1\}$: Directional prediction.
  - Directional Accuracy: $\text{DA} = \frac{1}{N} \sum_{i=1}^N \mathbb{I}\left(\text{sgn}(\hat{y}_i) = \text{sgn}(\Delta F_{i, t+h})\right)$.
- **Experimental Method:**
  Evaluate a benchmark corpus of verified financial disclosures. Compare an identical classification head fed by:
  1. $M_{\text{Base}}$: Bag-of-words / Loughran-McDonald financial dictionary / keyword rules.
  2. $M_{\text{NLP}}$: Compact financial Transformer embedding (e.g., FinBERT / domain-adapted RoBERTa).
  Evaluate using purged, non-overlapping walk-forward cross-validation.
- **Evidence Supporting $H_{1,1}$:**
  $\text{DA}(M_{\text{NLP}}) - \text{DA}(M_{\text{Base}}) \ge 3.0$ percentage points with Diebold-Mariano test $p < 0.01$ and Information Coefficient (IC) $> 0.05$.
- **Evidence Falsifying $H_{1,1}$:**
  Difference in DA is indistinguishable from zero ($p \ge 0.05$) or vanishes once filtered for announcements containing obvious pre-defined positive/negative trigger words.

---

### H2 — Historical Contextualisation
> **Proposition:** Conditioning the interpretation of an incoming event on a retrieved dense representation of comparable historical events improves predictive power over using the current article in isolation.

- **Null Hypothesis ($H_{0,2}$):**  
  Conditioning the predictive model on retrieved historical event analogues $H_t$ provides zero incremental predictive information relative to the isolated event embedding $E_t$:
  $$\mathcal{I}(\Delta F_{t+h} ; E_t, H_t) \le \mathcal{I}(\Delta F_{t+h} ; E_t)$$
  where $\mathcal{I}(X; Y)$ denotes mutual information.
- **Alternative Hypothesis ($H_{1,2}$):**  
  Incorporating historical event context $H_t$ yields a strictly superior prediction distribution:
  $$\mathcal{L}_{\text{NLL}}(M(E_t, H_t)) < \mathcal{L}_{\text{NLL}}(M(E_t)) \quad (p < 0.01)$$
  where $\mathcal{L}_{\text{NLL}}$ is the negative log-likelihood of realised return distributions.
- **Observable Variables:**
  - $H_t = \text{TopK}_{k=5}\left(\text{Sim}(E_t, E_{\tau})\right)$ for historical events $\tau < t$.
  - Post-event historical trajectory features: historical drift, historical volatility expansion, historical reversal frequency.
- **Experimental Method:**
  Ablation test: Compare Model B ($M(E_t)$) with Model C ($M(E_t, H_t)$) where $H_t$ is supplied via a dense nearest-neighbour retriever constrained strictly to prior chronological history ($\tau < t - \delta_{\text{purge}}$) to prevent look-ahead bias.
- **Evidence Supporting $H_{1,2}$:**
  Statistically significant reduction in cross-entropy/NLL loss, and improved directional accuracy for ambiguous or rare event sub-types (e.g., regulatory notices, executive transitions).
- **Evidence Falsifying $H_{1,2}$:**
  Model C overfits training history, yielding worse or indistinguishable out-of-sample log-loss or Sharpe ratio compared to Model B.

---

### H3 — Market Conditioning
> **Proposition:** Jointly conditioning the predictive model on contemporaneous market state features (order-book liquidity, bid-ask spread, trailing realised volatility, and order-flow imbalance) improves forecast calibration and prevents false breakouts relative to text-only models.

- **Null Hypothesis ($H_{0,3}$):**  
  Adding contemporaneous market state $M_t$ to text and context representations $(E_t, H_t)$ does not improve risk-adjusted predictive performance:
  $$\text{Sharpe}(M(E_t, H_t, M_t)) \le \text{Sharpe}(M(E_t, H_t))$$
- **Alternative Hypothesis ($H_{1,3}$):**  
  Market state features significantly improve prediction accuracy by modulating conviction when liquidity is thin or order flow indicates pre-existing momentum/exhaustion:
  $$\text{Sharpe}(M(E_t, H_t, M_t)) > \text{Sharpe}(M(E_t, H_t)) \quad \text{and} \quad \text{BrierScore}_{\text{conditioned}} < \text{BrierScore}_{\text{unconditioned}}$$
- **Observable Variables:**
  - $M_t = [\text{Spread}_t, \text{DepthRatio}_t, \text{OFI}_{t-30\text{s}:t}, \sigma_{\text{realised}, t-5\text{m}:t}, \text{Regime}_t]$.
  - Forecast probability distribution over return buckets $P(\Delta F_{t+h} \in B_k \mid E_t, H_t, M_t)$.
- **Experimental Method:**
  Evaluate model calibration curves and simulated risk-adjusted returns with and without contemporaneous microstructure features at timestamp $t$.
- **Evidence Supporting $H_{1,3}$:**
  Model D exhibits superior probability calibration (lower Expected Calibration Error, ECE $< 0.05$) and reduces false-positive trades during low-liquidity or already-extended price states.
- **Evidence Falsifying $H_{1,3}$:**
  Microstructure features dominate the text signals, rendering the model equivalent to a standard momentum/mean-reversion indicator without incremental event intelligence.

---

### H4 — Latency Sensitivity
> **Proposition:** The predictive edge of news-derived signals decays rapidly with elapsed time. Artificial injection of processing and inference latency degrades realised trade execution quality and cumulative performance.

- **Null Hypothesis ($H_{0,4}$):**  
  Realised trade returns $\text{PnL}_{\text{net}}(\Delta t_{\text{delay}})$ are invariant to artificial processing latency within the tested window $[0\text{ms}, 5000\text{ms}]$:
  $$\frac{\partial \mathbb{E}\left[\text{PnL}_{\text{net}}\right]}{\partial (\Delta t_{\text{delay}})} = 0 \quad \text{for } \Delta t_{\text{delay}} \in [0, 5000\text{ms}]$$
- **Alternative Hypothesis ($H_{1,4}$):**  
  Realised net returns exhibit monotonic, exponential or power-law decay with respect to decision delay:
  $$\frac{\partial \mathbb{E}\left[\text{PnL}_{\text{net}}\right]}{\partial (\Delta t_{\text{delay}})} < 0, \quad \text{with significant drop-off by } \Delta t_{\text{delay}} \ge 500\text{ms}$$
- **Observable Variables:**
  - $\Delta t_{\text{delay}} \in \{0\text{ms}, 20\text{ms}, 50\text{ms}, 100\text{ms}, 500\text{ms}, 1000\text{ms}, 5000\text{ms}\}$.
  - Execution entry price $P_{\text{entry}}(\Delta t_{\text{delay}})$ simulated against full historical order-book limit order queues.
  - Realised net profit per trade: $\text{PnL}(t, h, \Delta t_{\text{delay}})$.
- **Experimental Method:**
  Replay synchronized order-book feeds. For identical prediction signals, measure execution outcomes when the simulated order arrives at $t + \Delta t_{\text{delay}}$ versus $t + 0\text{ms}$.
- **Evidence Supporting $H_{1,4}$:**
  Sharp degradation of net Sharpe ratio and executable edge as $\Delta t_{\text{delay}}$ increases from $20\text{ms}$ to $1000\text{ms}$, proving the existence of an information diffusion half-life.
- **Evidence Falsifying $H_{1,4}$:**
  Returns remain unchanged between $0\text{ms}$ and $5000\text{ms}$, or longer-horizon returns ($30\text{s}$ to $5\text{min}$) are unaffected by initial millisecond execution timing.

---

### H5 — Economic Viability
> **Proposition:** There exists a non-empty subset of event families and forecast horizons that produces positive expected net trading returns after accounting for realistic exchange fees, regulatory taxes, bid-ask spreads, and market impact.

- **Null Hypothesis ($H_{0,5}$):**  
  The expected net return after all transaction costs across all event types and horizons is non-positive:
  $$\mathbb{E}\left[R_{\text{net}} \mid E, H, M\right] \le 0$$
- **Alternative Hypothesis ($H_{1,5}$):**  
  For at least one primary event family (e.g., Earnings Surprises or Major Contract Awards), the model generates positive net returns with statistical significance:
  $$\mathbb{E}\left[R_{\text{net}} \mid E_{\text{target}}, H, M\right] > 0 \quad \text{with } t\text{-statistic} > 2.5 \text{ and Max Drawdown} < 15\%$$
- **Observable Variables:**
  - $R_{\text{gross}}$: Gross price return over holding period.
  - $C_{\text{cost}} = \text{SpreadCost} + \text{SlippageImpact} + \text{Brokerage} + \text{STT/ExchangeCharges} + \text{GST} + \text{SEBI/StampDuty}$.
  - $R_{\text{net}} = R_{\text{gross}} - C_{\text{cost}}$.
- **Experimental Method:**
  Simulated execution using empirical top-of-book depth and explicit statutory Indian transaction cost schedules (STT on futures, NSE transaction charges, SEBI turnover fees, stamp duty, GST, and broker commission).
- **Evidence Supporting $H_{1,5}$:**
  Out-of-sample annualized net Sharpe ratio $> 1.5$, profit factor $> 1.4$, and positive expectancy across at least 100 independent events.
- **Evidence Falsifying $H_{1,5}$:**
  Transaction costs and spread crossing erode gross edge, resulting in negative net P&L across all horizons and parameter configurations.

---

## 3. Core Research Questions

The research program must explicitly investigate and answer the following specific empirical questions:

### Q1: Distinguishing Positive News from Positive Surprises
*Can the model distinguish between a superficially "good" headline and a genuine positive surprise relative to market consensus?*  
- **Investigation:** Formulate a structured extraction target for reported figures ($X_{\text{reported}}$) versus prior consensus estimates ($X_{\text{consensus}}$).  
- **Test:** Compare market reaction to headlines where reported numbers are positive in absolute terms ($+10\%$ YoY) but negative relative to consensus estimates (consensus was $+18\%$). The model must assign a negative expected return.

### Q2: Bearish Reaction to Seemingly Positive Announcements
*Can the model identify circumstances where an ostensibly favourable corporate event is economically value-destroying?*  
- **Investigation:** Analyze announcements such as debt-funded expensive acquisitions, margin-dilutive contract wins, capital raises at a steep discount, or guidance downgrades disguised beneath record trailing quarters.  
- **Test:** Evaluate whether multi-task event extraction accurately flags margin degradation or shareholder dilution despite optimistic executive commentary.

### Q3: Identification of Anticipated / "Priced-In" Information
*Can the model detect that an event was already fully anticipated by market participants?*  
- **Investigation:** Measure pre-announcement price drift $\text{Drift}_{t-1\text{d}:t}$ and order-flow concentration.  
- **Test:** When pre-announcement run-up matches expected event impact, verify whether the conditioned model dampens signal conviction or anticipates a "sell the news" reversal.

### Q4: Historical Retrieval on Ambiguous Disclosures
*Does retrieving similar historical corporate disclosures improve prediction for complex, ambiguous events?*  
- **Investigation:** Evaluate events with no standard quantitative consensus (e.g., sudden CEO resignation, regulatory show-cause notice, plant shutdown).  
- **Test:** Measure whether nearest-neighbour retrieval of historical analogues provides a calibrated prior for post-event drift.

### Q5: Market Regime Sensitivity
*Does the model predict different price trajectories for identical event classifications under different market regimes?*  
- **Investigation:** Test identical quarterly earnings surprise buckets under high-volatility/bearish macro regimes (India VIX $> 20$) versus low-volatility/bullish regimes (India VIX $< 13$).  
- **Test:** Verify that conditional variance and expected horizon returns adapt to baseline market drift.

### Q6: Information Advantage Decay Half-Life
*At what exact timescale does the information advantage dissipate?*  
- **Investigation:** Evaluate empirical returns from $t = 100\text{ms}$ to $t = 5\text{min}$.  
- **Test:** Construct empirical decay curves to identify the inflection point where public order books have fully absorbed the announcement.

### Q7: Net P&L Contribution of Low-Latency Inference
*Does shaving 50–200 milliseconds off model inference produce an economically meaningful improvement in net realized P&L?*  
- **Investigation:** Profile the trade execution slip across artificial inference delay increments.  
- **Test:** Quantify whether C++ / ONNX optimizations translate to higher fill rates and lower slippage, justifying low-latency engineering investments.

---

## 4. Initial Strategy Scope: Directional Futures

### 4.1 Rationale for Futures-Only Mandate
The Phase 0–3 research agenda is strictly restricted to **directional single-stock and index futures**:
1. **Linear Delta Exposure:** Futures contracts provide a pure linear mapping to underlying asset price movements ($\Delta F \approx \Delta S$).
2. **Elimination of Confounding Volatility Pricing:** Unlike options, futures do not suffer from implied volatility crush post-earnings, theta time-decay, bid-ask spread widening across option chains, or strike liquidity fragmentation.
3. **Symmetric Execution:** Liquid futures enable frictionless short positioning with identical execution mechanics to long positions.
4. **Research Isolation:** Validating directional predictability on linear instruments is an absolute prerequisite before introducing non-linear derivatives.

### 4.2 Exclusion of Options
Options strategies are deferred to Phase 6+. Under no circumstances will options pricing, Greeks, or volatility arbitrage models be evaluated in Phase 0.

### 4.3 Absolute Prohibition on Live Trading
All models, signals, and simulated fills in Phase 0 are purely offline and backtest-bound. Live capital deployment, real broker authentication, or execution endpoint invocation is strictly prohibited.
