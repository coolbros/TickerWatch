# TickerWatch Alpha — Phase 3 Financial Entity Recognition & Disambiguation

> **Document Version:** 1.0.0-alpha  
> **Status:** APPROVED SYSTEM DESIGN — PHASE 3  
> **Authors:** Quantitative Natural Language Processing Team  
> **Module:** `backend/alpha/entities.py`  
> **Scope:** Financial Named Entity Recognition, Alias Mapping, Subsidiary Resolution, and Ticker Collision Protection

---

## 1. The Financial Disambiguation Challenge

Extracting financial entities from free-form news and disclosures cannot rely on naive substring matching. A financial intelligence engine must resolve complex edge cases:
1. **Ticker Collisions with Vocabulary:** Tickers such as `IT` (Information Technology index), `AT`, `ON`, `ALL`, `CAN`, `FOR`, or `NOW` frequently appear as lowercase pronouns or prepositions. Unchecked matching produces catastrophic false-positive trades.
2. **Subsidiary-to-Parent Transmission:** A multi-billion-dollar contract win announced by "Jio Platforms" or "Reliance Retail" affects the listed parent entity `RELIANCE` and its derivatives (`RELIANCE_FUT`).
3. **Ambiguous Names:** A mention of "Tata" could refer to Tata Consultancy Services (`TCS`), Tata Motors (`TATAMOTORS`), Tata Steel, or the unlisted holding company Tata Sons.
4. **Executive Co-occurrence:** Disclosures often name key executives (e.g. "Mukesh Ambani", "Salil Parekh", "Satya Nadella") without explicitly repeating the corporate ticker in the headline.

---

## 2. Multi-Pass Resolution Architecture

The `FinancialEntityResolver` employs a 4-pass cascading resolution algorithm:

```
+==================================================================================================+
|                                  ENTITY RESOLUTION PIPELINE                                      |
+==================================================================================================+

   [ INCOMING TEXT ] (e.g. "Jio signs nationwide 5G infrastructure deal")
           |
           v
   [ Pass 1: Multi-Word Alias Dictionary ] (Longest-match first)
   - Matches "Jio" -> Entity `JIO` (Subsidiary)
   - Resolves Parent: `RELIANCE`
           |
           +---> If 1 candidate: RESOLVED (Confidence >= 0.90)
           +---> If > 1 candidates: AMBIGUOUS (Confidence 0.50, list candidates)
           |
           v
   [ Pass 2: Ticker Matching + Collision Guard ]
   - Extracts all uppercase tokens
   - Cross-references against `TICKER_COLLISION_STOPWORDS`
   - If stopword: Requires `FINANCIAL_CONTEXT_KEYWORDS` (e.g. "shares", "results")
           |
           v
   [ Pass 3: Executive Name Co-occurrence ]
   - Scans text for canonical executive names (e.g. "Mukesh Ambani" -> `RELIANCE`)
           |
           v
   [ Pass 4: Fallback & Explicit Unresolved State ]
   - If zero matches: Emits `UNRESOLVED` (Confidence 0.0)
   - Zero instruments assigned; no hallucinated trades
```

---

## 3. Entity Classification Taxonomy

Entities are categorized into six structural types:
- `COMPANY`: Listed corporate entity with primary exchange listings (e.g. `RELIANCE`, `INFY`, `TCS`, `HDFCBANK`).
- `SUBSIDIARY`: Operating unit whose cash flows flow to a listed parent (e.g. `JIO` -> `RELIANCE`, `JLR` -> `TATAMOTORS`).
- `REGULATOR`: Securities, banking, or competition regulator whose announcements drive market-wide volatility (e.g. `RBI`, `SEBI`, `SEC`).
- `MACRO_ENTITY`: Sovereign government bodies, statistical ministries, or central banks (e.g. `FINMIN`, `INDIA_MACRO`).
- `EXECUTIVE`: High-profile corporate officers whose appointments, exits, or public commentary move prices.
- `SECTOR`: Broad industry classifications (e.g. `IT`, `BANKING`, `ENERGY`).

---

## 4. Integration with Instrument Master (Phase 2)

Once an entity is resolved to its canonical identifier, it queries the Phase 2 `InstrumentMaster` to produce structured `InstrumentMappingRecord` vectors:
- **Direct Instruments:** Underlying cash equity (`EQ`) and liquid single-stock futures (`FUTSTK`).
- **Contextual Instruments:** NIFTY 50 futures (`NSE:NIFTY_FUT:CURRENT`) with weight proportional to index market cap.
- **Ambiguous or Unresolved Disclosures:** Strictly return zero instruments, preventing erroneous signal generation.
