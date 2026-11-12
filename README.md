# TickerWatch & TickerWatch Alpha

> **Local-First Financial Information Ingestion, Causal Event Intelligence, Predictive Modeling & Shadow Validation**
>
> Testing the event-conditioned predictive hypothesis: $P(\Delta F_{t+h} \mid E_t, H_t, M_t)$


---

## Project Status at a Glance

| Track | Scope | Software Gate | Empirical Market Validation Gate | Latest Merge Commit |
|---|---|---|---|---|
| **Phase 0–1** Ingestion Foundation | RSS/Atom & SEC EDGAR connectors, SQLite WAL ACID persistence, FastAPI dashboard & REST API | **Complete** | Operational (`3,042` US/IN records in `tickerwatch.db`) | [`f495e17`](https://github.com/coolbros/TickerWatch/pull/1) (`PR #1`) |
| **Phase 2** Alignment & Storage | Sub-second as-of joins, 10-stage monotonic clocks, `.jsonl.gz` partitions, deterministic replay | **Complete** | Verified offline & benchmarked | `7b1e290` |
| **Phase 3** Financial NLP ($E_t$) | Entity resolution, 5-family/25-subtype taxonomy, Lakh/Crore/USD extraction, causal surprise & materiality | **Complete** | Verified (`94.23%` Cls Acc, `0.428 ms` p95) | `701e5eb` |
| **Phase 4** Historical Retrieval ($H_t, M_t$) | Jurisdiction-isolated SQLite store, ONNX `bge-small-en-v1.5` (`[CLS]`-512) + BM25, session-aware event study | **Complete** | Verified (`0.9830` nDCG@5 dense, `0` leaks) | [`66b9328`](https://github.com/coolbros/TickerWatch/pull/2) (`PR #2`) |
| **Phase 5** Predictive Modeling & Attribution | 87-feature E/H/M registry, dual `REACTION`/`EXECUTABLE` targets, pure-NumPy Models A–E, Platt/Isotonic + `NO_TRADE`, purged walk-forward, Holm-Bonferroni attribution | **Complete** | **`BLOCKED` on real data** (verified on labeled `is_synthetic=True` & zero-signal null suites) | [`5d603be`](https://github.com/coolbros/TickerWatch/pull/3) (`PR #3`) |
| **Phase 6** Live Acquisition & Shadow Validation | Authorized RBI/SEC live adapters, fail-closed market scaffolds, dynamic futures resolver, SQLite trigger-immutable shadow ledger, L2/limit/OHLCV paper simulator, 5-plane telemetry & `0.0`-diff replay parity | **Complete** | **`COLLECTING_OBSERVATION_WINDOW` (1/20 min sessions)** — RBI RSS live (`HTTP 200`); live Indian market ticks & NSE/BSE filings await vendor/exchange access | [`e0c0470`](https://github.com/coolbros/TickerWatch/pull/4) (`PR #4`) |

> [!IMPORTANT]
> **Scientific Honesty & Operational Boundaries:**
> - **Strict Synthetic Quarantine:** Models trained on synthetic datasets are permanently stamped `is_synthetic=True` / `SYNTHETIC_DIAGNOSTIC_ONLY`, cannot achieve `MODEL_ELIGIBLE`, and are never presented as evidence of live market alpha.
> - **Zero Secret or WAF Bypass Policy:** Providers lacking credentials or blocked by WAF (`NSE`, `BSE`, `Zerodha Kite`, `TrueData`) are kept as fail-closed disabled adapters rather than scraped or spoofed with mock data.

---

## Core Architecture

```text
  [ Authorized Live Feeds ]            [ Market Data Streams / Replay ]
  - RBI Press Releases (IST -> UTC)    - Kite 184B L2 Decoder (fail-closed if unset)
  - SEC EDGAR & Press RSS (US / ET)    - Partitioned `.jsonl.gz` + SQLite Snapshots
                \                                   /
                 +----------------+----------------+
                                  |
                                  v  decision_timestamp = local_received_at_utc
             [ LiveCaptureStore & PersistentHistoricalEventStore (SQLite WAL) ]
                                  |
         +------------------------+------------------------+
         |                        |                        |
         v                        v                        v
  [ Phase 3 NLP (E_t) ]   [ Phase 4 Vector (H_t) ]   [ Market Regime (M_t) ]
  - Entities & Taxonomy   - BGE-small ONNX + BM25   - Strictly <= decision_ts
  - Currency & Surprise   - Matured Outcomes Only   - Spread, Vol, L2 Depth
         \                        |                        /
          +-----------------------+-----------------------+
                                  |
                                  v
         [ Phase 5 Feature Contract (87 Features) & Predictive Gate ]
         - Ablations A (M), B (E), C (E+H), E (E+M), D (E+H+M)
         - Validation-only Platt / Isotonic / Split-Conformal [q10, q90]
         - 7-Rule NO_TRADE Abstention & ModelEligibilityAuditor
                                  |
                                  v
         [ Immutable ShadowSignalLedger (SQLite Triggers + SHA-256) ]
         - Appends predictions before outcome maturation
         - Tracks executable paper fills (L2 / L1 / Limit Queue / OHLCV)
         - Live-vs-Replay Parity (0.0-diff) & 5-Plane Drift Monitoring CLI
```

Key architectural guarantees:
- **Zero Look-Ahead Bias:** Every historical query, outcome maturity check (`outcome_end_ts <= decision_timestamp`), market snapshot (`<= decision_timestamp`), and RBI (`Asia/Kolkata`) / SEC (`America/New_York`) timestamp conversion is causally enforced.
- **Transactional & Ledger Immutability:** Phase 1 ingestion commits articles and connector cursors inside atomic `BEGIN IMMEDIATE` transactions; Phase 6 `LiveCaptureStore` and `ShadowSignalLedger` enforce SQLite `BEFORE UPDATE` / `BEFORE DELETE` triggers plus per-record SHA-256 checksums.
- **Zero Heavy ML / Cluster Dependencies:** Runs locally on CPU with `numpy`, `onnxruntime`, `pydantic`, `fastapi`, `httpx`, `feedparser`, and `sqlite3` (no PyTorch, scikit-learn, Kafka, or Redis required).

---

## Quickstart

### 1. Prerequisites
- **Python 3.11+** (verified on Python `3.12.10`)
- **`uv`** (recommended) or standard `pip`

### 2. Installation
```powershell
git clone https://github.com/coolbros/TickerWatch.git
cd TickerWatch

# Create virtual environment and install pinned lockfile dependencies
uv venv --python 3.12
uv pip install --system-certs -r requirements.lock
```

### 3. Configuration
Copy `.env.example` to `.env`:
```powershell
Copy-Item .env.example .env
```
Set your identifiable contact string in `.env` per SEC EDGAR fair-access policy (and inspect `config/feeds.yaml` for RSS sources):
```env
SEC_USER_AGENT=TickerWatch/0.1 admin@example.com
NSE_RSS_ENABLED=false
```
> Market-data keys (`KITE_API_KEY`, `KITE_ACCESS_TOKEN`, etc.) are intentionally unset by default. Keep them unset unless you hold authorized read-only subscriptions.

---

## Verification & Benchmarks

### Automated Test Suite
The repository excludes external network tests by default via `addopts = "-m 'not live'"` in `pyproject.toml`:

```powershell
# Run complete offline test suite (unit, contract, integration, API, Phases 1-6)
.\.venv\Scripts\python.exe -m pytest -v
```
- **Observed Result:** **`211 passed, 3 deselected`** (Windows note: run `pytest-asyncio` tests in a normal host shell rather than network/loopback-restricted sandboxes where Windows `socketpair()` blocks).

```powershell
# Opt-in live smoke checks against authorized public feeds (RBI Press RSS, SEC Press RSS, SEC EDGAR)
.\.venv\Scripts\python.exe -m pytest -v -m live
```
- **Observed Result:** **`3 passed, 211 deselected`**.

### Research & Operational CLI Scripts

| Command | Purpose |
|---|---|
| `.\.venv\Scripts\python.exe scripts/run_ingestion.py [--connector sec_edgar]` | Run immediate Phase 1 RSS / SEC ingestion cycle into `tickerwatch.db` |
| `.\.venv\Scripts\python.exe scripts/inspect_database.py` | Inspect persisted articles, connector cursors, and WAL state |
| `.\.venv\Scripts\python.exe scripts/check_connectors.py` | Probe connector health and freshness states |
| `.\.venv\Scripts\python.exe scripts/alpha_pipeline.py {inspect\|replay\|quality\|export}` | Run Phase 2 clock audit, deterministic tick replay, quality audit, or dataset export |
| `.\.venv\Scripts\python.exe scripts/alpha_nlp_benchmark.py` | Benchmark Phase 3 NLP entity/classification/extraction accuracy & latency |
| `.\.venv\Scripts\python.exe scripts/alpha_historical_benchmark.py` | Benchmark Phase 4 ONNX/BM25 retrieval nDCG@5 & causal event-study checks |
| `.\.venv\Scripts\python.exe scripts/alpha_phase5_experiment.py` | Run real-data readiness gate + labeled synthetic ablation & zero-signal null suite |
| `.\.venv\Scripts\python.exe scripts/alpha_shadow_report.py --output-json report.json` | Generate Phase 6 5-plane operational health & drift summary CLI report |

---

## FastAPI Dashboard & REST Service

Start the local web dashboard and API server:
```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```
Open `http://127.0.0.1:8000/` for the built-in dashboard or `http://127.0.0.1:8000/docs` for Swagger OpenAPI docs.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Interactive local financial intelligence web dashboard |
| `GET` | `/health` | Consolidated system and connector health status |
| `GET` | `/connectors` | Registered connectors, cursors, failure counters |
| `GET` | `/connectors/{connector_id}/health` | Detailed health telemetry for one connector |
| `GET` | `/articles` | Paginated normalized articles (`?limit=50&offset=0&ticker=...&connector_id=...`) |
| `GET` | `/articles/{article_id}` | Retrieve single canonical article record |
| `GET` | `/stats/ingestion` | Aggregate ingestion counts and dead-letter statistics |
| `POST` | `/connectors/{connector_id}/trigger` | Manually trigger a single connector ingestion pass |

---

## Repository Structure

```text
tickerwatch/
├── backend/
│   ├── main.py & config.py                # FastAPI factory, Pydantic Settings, JSON logging
│   ├── api/ & monitoring/                 # REST endpoints, HTML dashboard, health & stage timers
│   ├── connectors/                        # BaseConnector, RSS/Atom, SEC EDGAR JSON, registry
│   ├── ingestion/                         # Transaction orchestrator, normalizer, SHA-256 dedup, retry
│   ├── database/                          # SQLite WAL engine, repository, migrations (001, 002)
│   └── alpha/
│       ├── alignment.py, replay.py,       # Phase 2 temporal as-of alignment, compressed storage,
│       │   storage.py, schemas.py         #   instruments & quality engine
│       ├── entities.py, event_*.py,       # Phase 3 NLP entities, classifier, extraction,
│       │   surprise.py, materiality.py    #   point-in-time surprise & materiality
│       ├── historical_*.py, outcomes.py,  # Phase 4 SQLite WAL vector store, BGE-small ONNX,
│       │   event_study.py, market_context.py  calendar-aware event studies & M_t regime
│       ├── data_readiness.py, feature_*.py, # Phase 5 87-feature E/H/M registry, targets, models,
│       │   predictive_*.py, walk_forward.py,  uncertainty calibration, purged CV, Holm-Bonferroni
│       │   research_backtest.py, model_registry.py  attribution, backtester & SHA-256 registry
│       └── live_*.py, shadow_*.py         # Phase 6 RBI/SEC/Kite adapters, live universe,
│                                          #   trigger-immutable ledger, paper simulator, parity
├── config/
│   ├── feeds.example.yaml & feeds.yaml    # Configured RSS feed definitions
├── scripts/                               # CLI ingestion, benchmarks, Phase 5 experiment & shadow report
├── tests/                                 # 211 offline + 3 opt-in live tests across Phases 1–6
└── docs/
    ├── PHASE_1_AUDIT.md ... DATABASE.md   # Phase 1 foundational specs
    └── alpha/                             # Full research & governance record (Phases 0–6)
```

---

## Governance & Key Documentation

- **System Architecture & Decisions:** [`docs/alpha/ARCHITECTURE.md`](docs/alpha/ARCHITECTURE.md) | [`docs/alpha/DECISIONS.md`](docs/alpha/DECISIONS.md) (ADR-001 through ADR-029) | [`docs/alpha/PHASE_GATES.md`](docs/alpha/PHASE_GATES.md)
- **Data Requirements & Audit Gaps:** [`docs/alpha/DATA_REQUIREMENTS.md`](docs/alpha/DATA_REQUIREMENTS.md) | [`docs/alpha/PHASE_5_DATA_READINESS.md`](docs/alpha/PHASE_5_DATA_READINESS.md) | [`docs/alpha/PHASE_6_AUTHORIZATION_MATRIX.md`](docs/alpha/PHASE_6_AUTHORIZATION_MATRIX.md)
- **Phase Reports & Runbooks:** [`docs/alpha/PHASE_3_REPORT.md`](docs/alpha/PHASE_3_REPORT.md) | [`docs/alpha/PHASE_4_REPORT.md`](docs/alpha/PHASE_4_REPORT.md) | [`docs/alpha/PHASE_5_REPORT.md`](docs/alpha/PHASE_5_REPORT.md) | [`docs/alpha/PHASE_6_REPORT.md`](docs/alpha/PHASE_6_REPORT.md) | [`docs/alpha/PHASE_6_OPERATIONAL_RUNBOOK.md`](docs/alpha/PHASE_6_OPERATIONAL_RUNBOOK.md)
