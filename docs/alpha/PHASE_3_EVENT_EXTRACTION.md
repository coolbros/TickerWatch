# Phase 3 Structured Financial Event and Numerical Extraction

**Document Version:** 1.0.0-alpha  
**Date:** 2026-11-10  
**Phase:** Phase 3 — Financial NLP, Entity Resolution & Numerical Surprise Detection  
**Status:** Canonical Implementation Specification  

---

## 1. Motivation and Problem Statement

Financial market news and exchange disclosures express quantitative catalysts using diverse lexical structures, region-specific number systems, and complex financial terminology. For an automated event-conditioned predictive model to learn effectively:
1. All monetary quantities must be parsed into normalized base currency units (e.g. converting Indian Crores and Lakhs into standard IEEE 754 floating-point numbers).
2. Accounting conventions such as parenthesized losses `(Rs 120 cr)` must be recognized as negative numbers.
3. Numerical ranges (`between Rs 500 cr and Rs 600 cr`) must preserve both boundary endpoints and nominal midpoints.
4. Surrounding context must determine the metric type (Revenue vs PAT vs Margin vs Order Win) and direction of change (growth vs contraction).

This module (`backend/alpha/event_extraction.py`) provides the structured extraction layer for TickerWatch Alpha.

---

## 2. Supported Number Systems and Unit Normalization

### Indian Numbering System
The Indian numbering system groups thousands differently from international conventions:
- **1 Lakh ($10^5$):** $1,00,000$ base units. Represented as `lakh`, `lakhs`, `lac`, `lacs`.
- **1 Crore ($10^7$):** $1,00,00,000$ base units. Represented as `crore`, `crores`, `cr`.
- **1 Arab ($10^9$):** $1,00,00,00,000$ base units.

### International Numbering System
- **Thousand ($10^3$):** `k`, `thousand`.
- **Million ($10^6$):** `m`, `mn`, `million`.
- **Billion ($10^9$):** `b`, `bn`, `billion`.
- **Trillion ($10^{12}$):** `t`, `tn`, `trillion`.

### Multipliers Table
$$\text{Normalized Value} = \text{Raw Quantity} \times \text{Multiplier} \times (\text{Sign})$$

| Unit Token | Multiplier | Standard Representation |
|:---|:---|:---|
| `lakh` / `lac` | $100,000$ | $10^5$ |
| `crore` / `cr` | $10,000,000$ | $10^7$ |
| `k` / `thousand` | $1,000$ | $10^3$ |
| `million` / `mn` | $1,000,000$ | $10^6$ |
| `billion` / `bn` | $1,000,000,000$ | $10^9$ |
| `trillion` / `tn` | $1,000,000,000,000$ | $10^{12}$ |

---

## 3. Disambiguation and Financial Semantics

### Lexical Polysemy Handling: "Contract" (Noun) vs "Contracts" (Verb)
A critical challenge in financial NLP is word-sense disambiguation:
- **Noun ("Order / Deal"):** `"L&T bags contract worth Rs 2,500 crore"` $\rightarrow$ `CONTRACT_VALUE`, `INCREASE`.
- **Verb ("Shrink / Compress"):** `"Operating margin contracts 75 bps"` $\rightarrow$ `MARGIN`, `DECREASE`.

The extraction engine prevents false positive order-win extractions by enforcing distance-weighted keyword clustering and associating inflectional forms ("contracts", "contraction") with negative directional momentum.

### Accounting Losses and Negative Figures
Standard corporate reporting writes losses in parentheses:
- `(Rs 120 cr)` is parsed as $-1.2 \times 10^9\text{ INR}$.
- Preceding or trailing tokens like `loss of Rs 50 cr` or `net loss` invert sign to negative.

### Numerical Ranges
Disclosures often state guidance or deal sizes as intervals:
- Example: `"between Rs 500 cr and Rs 600 cr"`
- Output: `is_range = True`, `range_low = 5,000,000,000.0`, `range_high = 6,000,000,000.0`, `normalized_value = 5,500,000,000.0` (midpoint).

---

## 4. Extracted Output Schema

Every extracted metric satisfies the strict `ExtractedMetricValue` Pydantic model:

```python
class ExtractedMetricValue(BaseModel):
    metric_type: FinancialMetricType     # NET_PROFIT_PAT, REVENUE, EBITDA, MARGIN, CONTRACT_VALUE, CAPEX, etc.
    raw_text: str                       # Exact verbatim text span
    normalized_value: float             # Floating-point number in base currency
    currency: CurrencyUnit              # INR, USD, EUR, GBP, JPY, UNKNOWN
    unit: str                           # "cr", "lakh", "million", "%", "bps"
    is_percentage: bool                 # True if percentage
    is_basis_points: bool               # True if basis points
    is_range: bool                      # True if low-high interval
    range_low: Optional[float]          # Interval floor
    range_high: Optional[float]         # Interval ceiling
    comparison_period: Optional[TemporalComparison] # YOY, QOQ, FULL_YEAR
    direction: DirectionChange          # INCREASE, DECREASE, UNCHANGED, UNKNOWN
    start_char: int                     # 0-indexed char start in input string
    end_char: int                       # 0-indexed char end in input string
    confidence: float                   # Extraction confidence [0.0, 1.0]
```

---

## 5. Execution Benchmarks

The extractor uses compiled regular expressions and distance-weighted window scoring:
- **P50 Latency:** $< 0.12\text{ ms}$ per headline.
- **P99 Latency:** $< 0.45\text{ ms}$ per disclosure.
- **Zero External Network Calls:** 100% offline, reproducible, thread-safe.
