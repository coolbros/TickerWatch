# TickerWatch Alpha — Benchmark & Experiment Design Protocol

> **Document Version:** 1.0.0-alpha  
> **Status:** APPROVED BASELINE — PHASE 0  
> **Author:** Quantitative Research Methodology & Model Validation  
> **Scope:** Model Hierarchy, Ablation Matrix, Latency Stress-Testing & Purged Splits  

---

## 1. The 4-Tier Model Comparison Hierarchy

To scientifically isolate the incremental value added by each architectural component, TickerWatch Alpha establishes a strict comparative hierarchy across four benchmark models:

```
+====================================================================================================+
|                                    4-TIER MODEL HIERARCHY                                          |
+====================================================================================================+

   [ MODEL A: Rule-Based Baseline ]
   Inputs: Raw keyword matching / Loughran-McDonald lexicon sentiment score.
   Logic: Static sign thresholding (e.g., if positive_word_count > negative then LONG).
   Role: Sets minimum baseline performance; falsifies trivial NLP value.
          |
          v
   [ MODEL B: Financial NLP Transformer ]
   Inputs: Ingestion text -> Compact Domain Transformer (FinBERT / ModernBERT).
   Outputs: Multi-task Event Class + Numeric Surprise Delta + Directional Score.
   Role: Measures incremental contribution of semantic and syntactic understanding.
          |
          v
   [ MODEL C: Financial NLP + Historical Event Retrieval ]
   Inputs: Model B representations + Top-K retrieved prior event trajectories (HNSW).
   Outputs: Event representation conditioned on historical price reaction priors.
   Role: Measures incremental contribution of contextual memory.
          |
          v
   [ MODEL D: Full Fusion (NLP + Historical Context + Market Microstructure) ]
   Inputs: Model C representations + Contemporaneous Order Book (L2 Depth, OFI, Spread, Vol).
   Outputs: Multi-horizon conditional distribution P(Delta F_{t+h} | E, H, M).
   Role: Complete TickerWatch Alpha production predictive system.
```

---

## 2. Systematic Ablation Matrix

To ensure that reported performance gains are not spurious artifacts of multi-parameter overfitting, the experiment protocol mandates **six systematic ablation studies**:

| Ablation ID | Description | Component Removed / Altered | Scientific Question Addressed |
|---|---|---|---|
| **ABL-1** | **No Surprise Detection** | Replace numeric expectation delta $(\Delta_{\text{surprise}})$ with raw headline polarity | Does calculating surprise relative to consensus provide value beyond surface tone? |
| **ABL-2** | **No Entity Disambiguation** | Assign signals without resolving subsidiary/parent corporate entity hierarchy | Does fine-grained corporate structure mapping prevent misattributed trades? |
| **ABL-3** | **No Historical Retrieval** | Drop historical context vector $H_t$ (reverting Model C/D to Model B) | Does memory of prior market reactions improve prediction on rare/ambiguous events? |
| **ABL-4** | **No Microstructure State** | Drop contemporaneous order-book features $M_t$ (spread, depth imbalance, OFI) | Does market state prevent trading into illiquid traps or exhausted momentum? |
| **ABL-5** | **Artificial Latency Stress** | Inject synthetic delay $\Delta t \in \{0, 20, 50, 100, 500, 1000, 5000\text{ms}\}$ | How rapidly does the actionable trading edge decay over time? |
| **ABL-6** | **Model Capacity Scaling** | Compare compact Transformer (60M–110M params) against large LLM (1B–7B params) | Does increased parameter scale improve financial alpha, or merely increase inference latency? |

---

## 3. Latency Degradation Scenarios

To measure the empirical decay half-life of news intelligence ($H4$), the simulation engine executes an automated latency stress test across **seven distinct execution delay scenarios**:

$$\text{Scenario } k: \quad t_{\text{order\_arrival}} = t_{\text{pred}} + \Delta t_k, \quad \Delta t_k \in \{0\text{ms}, 20\text{ms}, 50\text{ms}, 100\text{ms}, 500\text{ms}, 1000\text{ms}, 5000\text{ms}\}$$

```
+----------------------------------------------------------------------------------------------------+
|                                    LATENCY DEGRADATION PROFILING                                   |
|                                                                                                    |
|   Net Sharpe                                                                                       |
|      ^                                                                                             |
|  2.5 |     * [0 ms: Theoretical Zero-Latency Frontier]                                             |
|      |      \                                                                                      |
|  2.0 |       * [20 ms: High-Performance C++/ONNX Target]                                           |
|      |        \                                                                                    |
|  1.5 |         * [50 ms: Python-Optimized Target]                                                  |
|      |          \                                                                                  |
|  1.0 |           \                                                                                 |
|      |            * [100 ms: Standard Broker WebSocket Roundtrip]                                  |
|  0.5 |             \                                                                               |
|      |              * [500 ms: Slow Inference]                                                     |
|  0.0 |---------------+--------------------------------* [1000 ms: Alpha Breakeven Threshold]       |
|      |                                                 \                                           |
| -0.5 |                                                  * [5000 ms: Negative Net Alpha Post-Costs] |
|      +----------------------------------------------------------------------------------------->   |
|      0ms   20ms   50ms     100ms         500ms        1000ms                5000ms       Delay     |
+----------------------------------------------------------------------------------------------------+
```

### Empirical Profiling Metrics:
1. **Executable Spread Capture Ratio:** Percentage of top-of-book depth remaining available upon order arrival.
2. **Slippage Elasticity ($\beta_{\text{slip}}$):** Rate of execution price degradation per millisecond of delay:
   $$\beta_{\text{slip}} = \frac{\partial \text{Slippage}}{\partial (\Delta t)}$$
3. **Alpha Half-Life ($\tau_{1/2}$):** The elapsed time at which gross realized returns decline to $50\%$ of the zero-latency benchmark.

---

## 4. Multi-Dimensional Evaluation Framework

Model performance is evaluated across eleven distinct statistical, economic, and operational dimensions:

| Dimension | Primary Metric | Target / Benchmark |
|---|---|---|
| **1. Event Extraction Quality** | Macro F1-score across 10 event classes | $> 0.85$ on held-out gold annotations |
| **2. Entity Resolution Accuracy** | Exact-match Accuracy on ISIN / NSE Ticker mapping | $> 0.98$ (zero tolerance for entity misattribution) |
| **3. Surprise Detection Quality** | Mean Absolute Error (MAE) on extracted vs true surprise delta | $\text{MAE} < 0.25 \times \sigma_{\text{consensus}}$ |
| **4. Forecast Calibration** | Expected Calibration Error (ECE) & Brier Score | $\text{ECE} < 0.05$ across predicted probabilities |
| **5. Directional Accuracy (DA)** | Proportion of correct sign forecasts: $\frac{1}{N} \sum \mathbb{I}(\text{sgn}(\hat{y}) = \text{sgn}(\Delta F))$ | $> 56.0\%$ ($p < 0.01$ under binomial test) |
| **6. Return Prediction Error** | Root Mean Squared Error (RMSE) on forward returns | Significant improvement over drift-only baseline |
| **7. Net Executable P&L** | Cumulative net profit post-spread, fees, and statutory Indian taxes | Monotonically positive; Profit Factor $> 1.4$ |
| **8. Maximum Drawdown (MDD)** | Peak-to-trough net equity decline | $\text{MDD} < 12.0\%$ |
| **9. Performance by Event Family** | Net Sharpe partitioned across Earnings, Orders, and Macro | Identifies which event classes drive economic edge |
| **10. Performance by Market Regime** | Net P&L partitioned by Volatility (India VIX $< 13, 13-18, > 18$) | Confirms robustness across bull, bear, and crisis regimes |
| **11. Performance by Horizon ($h$)** | Net P&L curve across $h \in \{1\text{s}, 5\text{s}, 30\text{s}, 5\text{min}\}$ | Determines optimal trade holding period |

---

## 5. Temporal Leakage Prevention: Purged Walk-Forward Cross-Validation

Standard random $k$-fold cross-validation is **strictly prohibited** in financial time series, as shuffling observations leaks future market regimes into past training sets.

```
+====================================================================================================+
|                              PURGED WALK-FORWARD CROSS-VALIDATION                                  |
+====================================================================================================+

   TIMELINE (Years 1 to 3):
   [==== FOLD 1 TRAIN ====]--[PURGE]--[FOLD 1 TEST]
   
   [========= FOLD 2 TRAIN ========]--[PURGE]--[FOLD 2 TEST]
   
   [============== FOLD 3 TRAIN =============]--[PURGE]--[FOLD 3 TEST]
   
   <------- IN-SAMPLE EXPANDING WINDOW ------->           <--- OUT-OF-SAMPLE --->
```

### Strict Partitioning Protocols:
1. **Chronological Expanding Windows:** The training dataset expands monotonically forward in time. Testing occurs exclusively on subsequent, unseen future chronological intervals.
2. **The Purging Window ($\delta_{\text{purge}}$):** A mandatory temporal buffer (e.g., 5 trading days) is inserted between the end of the training partition and the start of the validation partition. This guarantees that overlapping event horizons and post-earnings drift do not cross partition boundaries.
3. **The Embargo Window ($\delta_{\text{embargo}}$):** An additional post-test buffer to prevent auto-correlated market state leakage into subsequent walk-forward folds.
4. **Historical Retrieval Purging:** When Model C or Model D performs nearest-neighbour retrieval on historical events, the retrieval index is dynamically restricted to events with timestamps $\tau \le t_{\text{train\_cutoff}}$. Retrieving an analogue that occurred in the test window is an automatic validation failure.

---

## 6. Strict Phase 0 Scope Boundary

> **Notice:** Model training, hyperparameter optimization, vector index generation, and backtesting are **strictly excluded from Phase 0**. This document serves solely as the immutable research specification to be implemented during Phases 2, 3, 4, and 5.
