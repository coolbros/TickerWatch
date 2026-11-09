# TickerWatch Alpha — Financial Theory Learning Checklist & Domain Hierarchy

> **Document Version:** 1.0.0-alpha  
> **Status:** APPROVED BASELINE — PHASE 0  
> **Author:** Quantitative Education & Software Engineering Alignment  
> **Philosophy:** AI-Led Engineering — Pragmatic Financial Grounding Without Theory Paralysis  

---

## 1. Guiding Philosophy: AI-Led Engineering

TickerWatch Alpha is an **artificial intelligence and low-latency systems engineering project**, not a doctoral thesis in mathematical finance. 

Developers and quantitative researchers do **not** need comprehensive mastery of theoretical financial economics before writing high-performance data pipelines and training neural architectures. The curriculum is strictly triaged into three progressive tiers:
1. **Tier 1 (Mandatory Prerequisites):** Foundational concepts required to build valid loss functions, calculate returns, and prevent catastrophic backtest bugs.
2. **Tier 2 (Applied Microstructure):** Concepts absorbed iteratively during feature engineering and market simulation (Phases 2–4).
3. **Tier 3 (Deferred Theory):** Advanced theoretical topics that provide zero immediate utility and must not delay core implementation.

```
+====================================================================================================+
|                                    FINANCIAL DOMAIN CURRICULUM                                     |
+====================================================================================================+

   [ TIER 1: MUST UNDERSTAND BEFORE PREDICTIVE MODELLING ]
   * Futures Contracts & Cash Settlement
   * Bid-Ask Spread, Market vs Limit Orders, & Top-of-Book Depth
   * Linear Returns, Log Returns, & Realized Volatility
   * Leverage, Span/Exposure Margin, & Position Sizing
   * Transaction Costs (STT, Exchange Fees, GST, Stamp Duty) & Slippage
          |
          v
   [ TIER 2: LEARN DURING IMPLEMENTATION (PHASES 2-4) ]
   * Order-Flow Imbalance (OFI) & Book Pressure
   * Adverse Selection & Price Impact Models (Kyle / Almgren-Chriss)
   * Information Diffusion & Post-Earnings Announcement Drift (PEAD)
   * Futures Basis (Cost of Carry) & Calendar Rollover Rules
   * Volatility Regimes (India VIX) & Regime Switching
          |
          v
   [ TIER 3: EXPLICITLY DEFERRED (DO NOT STUDY IN PHASE 0-5) ]
   * Black-Scholes-Merton Partial Differential Equations
   * Stochastic Calculus & Ito's Lemma
   * Volatility Surface / Implied Volatility Smile Dynamics
   * Advanced Multi-Leg Execution Algorithms (TWAP/VWAP/POV)
   * FPGA Kernel RTL / Verilog Development
```

---

## 2. Tier 1: Must Understand Before Predictive Modelling

The engineering team must be fluent in these concepts before formulating training labels or writing simulation backtesters:

### 1. Futures Contracts
- **Definition:** Standardized, exchange-traded binding agreements to buy or sell an underlying asset at a predetermined price on a future expiry date.
- **Key Characteristic:** Futures trade in discrete **lot sizes** (e.g., Nifty 50 lot size $= 25$ or $50$; Reliance lot size $= 250$). Trading occurs in integer contract multiples, not fractional shares.
- **Settlement:** Index futures in India (`NIFTY_FUT`) are purely **cash-settled** in INR against the underlying spot index closing level, completely avoiding physical delivery risks.

### 2. Long and Short Positions
- **Symmetry:** Unlike equities (which require securities lending to short overnight), futures allow instantaneous, perfectly symmetric short selling. Going short simply involves selling a contract you do not own, with identical margin and fee mechanics to going long.
- **Payoff:**  
  $$\text{Long Payoff} = (P_{\text{exit}} - P_{\text{entry}}) \times \text{LotSize} \times N_{\text{contracts}}$$  
  $$\text{Short Payoff} = (P_{\text{entry}} - P_{\text{exit}}) \times \text{LotSize} \times N_{\text{contracts}}$$

### 3. Leverage and Margin
- **Notional Value:** $\text{Notional} = P_{\text{contract}} \times \text{LotSize}$. For Nifty at 24,000 with lot size 25, notional value $= 600,000\text{ INR}$.
- **Initial Margin (SPAN + Exposure):** In India, SEBI mandates upfront margin ($\sim 10\% - 12\%$ of notional). A trader needs only $\sim 65,000\text{ INR}$ in collateral to control a $600,000\text{ INR}$ contract ($\sim 9\times$ effective leverage).
- **Mark-to-Market (MTM):** Profits and losses are credited or debited daily to the collateral account. If collateral falls below maintenance requirements, a margin call triggers forced liquidation.

### 4. Market and Limit Orders
- **Limit Order:** An instruction to buy at or below a specified price, or sell at or above. Rests in the order book, adding liquidity. Pays zero spread, but suffers from execution uncertainty (risk of non-execution).
- **Market Order:** An instruction to execute immediately at the best available prevailing prices. Crosses the spread, taking liquidity. Guarantees immediate fill, but pays the bid-ask spread and risks adverse slippage.

### 5. Bid–Ask Spread and Liquidity
- **Top of Book:** The gap between the lowest price a seller will accept ($P_{\text{ask}}$) and the highest price a buyer will pay ($P_{\text{bid}}$).
- **Spread Cost:** Every aggressive trade immediately incurs a theoretical loss equal to the half-spread ($\frac{S}{2}$). In `NIFTY_FUT`, the minimum tick is 0.05 index points ($\sim 1.25\text{ INR}$ per lot).

### 6. Returns and Volatility
- **Simple Return:** $R_t = \frac{P_t - P_{t-1}}{P_{t-1}}$.
- **Log Return:** $r_t = \ln(P_t / P_{t-1})$. Preferred for continuous high-frequency time series due to additive temporal aggregation: $\sum_{t=1}^T r_t = \ln(P_T / P_0)$.
- **Realized Volatility:** Annualized standard deviation of log returns over a rolling window.

### 7. Slippage and Transaction Costs in Indian Markets
- **Gross vs. Net Return:** A strategy with a $+0.05\%$ gross edge is bankrupt if transaction costs are $0.06\%$.
- **Statutory Indian Schedule (Equity Futures):**
  - Securities Transaction Tax (STT): 0.02% on the sell turnover (raised from 0.0125% in 2024).
  - Exchange Turnover Charges (NSE): $\sim 0.00173\%$.
  - Goods & Services Tax (GST): 18% applied on brokerage and exchange charges.
  - SEBI Turnover Fee: 0.0001% ($\text{Rs } 10 \text{ per Crore}$).
  - Stamp Duty: 0.002% on the buy turnover.
  - Brokerage: Typically flat fee ($\text{Rs } 20\text{ per order}$) or $0.01\%$.

---

## 3. Tier 2: Can Learn During Implementation (Phases 2–4)

These concepts will be mastered as specific analytical features are added:

1. **Order-Flow Imbalance (OFI):** Quantifying net buying vs. selling pressure from Level 2 order book changes across consecutive quote updates.
2. **Adverse Selection:** The phenomenon where orders are filled primarily when the counterparty possesses superior information, leaving the trader with an immediate paper loss.
3. **Price Impact & Market Depth Absorption:** How large orders consume multiple price tiers in the limit order book, shifting the mid-price permanently or temporarily.
4. **Market Regimes & Volatility States:** Modeling how identical earnings surprises produce different price reactions depending on systemic macro conditions (measured via India VIX).
5. **Event Study Methodology:** Abnormal return calculations, benchmark market model subtraction ($\alpha_i = R_i - (\alpha + \beta R_m)$), and post-earnings announcement drift (PEAD).
6. **Futures Basis & Calendar Rollover:** The premium or discount of futures relative to spot ($F_t = S_t e^{(r - q)(T - t)}$) and the mechanics of rolling expiring near-month contracts to the next-month series.

---

## 4. Tier 3: Explicitly Deferred Topics

The following advanced topics are **strictly out of scope** for Phases 0–5. Developers must not expend time studying or implementing them:

1. **Advanced Options Pricing & Stochastic Volatility:** Black-Scholes, Heston models, SABR volatility smiles, local volatility surfaces, and dynamic delta-gamma-vega hedging. Options are not traded in the initial phases.
2. **Stochastic Calculus:** Measure changes, Girsanov theorem, Martingale representations. Unnecessary for discrete empirical machine learning on high-frequency time series.
3. **Complex Multi-Asset Algorithmic Execution:** Volume-Weighted Average Price (VWAP) slicing, Percentage-of-Volume (POV) schedulers, and optimal liquidation trajectories. Our initial trade sizes are small enough to execute in single aggressive or top-of-book clips.
4. **FPGA Hardware Description Languages (VHDL / Verilog):** Low-level hardware synthesis is deferred until profiling proves software execution on CPU is the limiting bottleneck.
