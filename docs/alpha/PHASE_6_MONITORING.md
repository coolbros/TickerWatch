# Phase 6 Operational Observability & Drift Monitoring (`PHASE_6_MONITORING`)

**Modules:**
- [`backend/alpha/shadow_monitoring.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/shadow_monitoring.py)
- [`scripts/alpha_shadow_report.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/scripts/alpha_shadow_report.py)

---

## 1. Five Monitored Operational Planes

1. **News Health:** Active/disabled provider status, articles & revisions recorded, revision frequency, source publication-to-receipt latency (`p50`/`p95`), missing publication timestamp count, entity resolution failure rate.
2. **Market-Data Health:** Feed connection uptime fraction, total throughput, sequence gaps, duplicates, out-of-order packets, staleness frequency, quote freshness (`p50`/`p95` ms), missing expected instruments, and invalid quote anomalies (`bid > ask` or `<= 0`).
3. **AI Pipeline Health:** Monotonic per-stage latency percentiles (`p50`/`p95`/`p99` ms), mean entity resolution confidence, empty numerical extraction count, `NO_TRADE` abstention frequency, and confidence quantiles.
4. **Predictive & Drift Monitoring:** Realized MAE/RMSE across matured executable outcomes, calibration ECE, mean prediction shift z-score (`prediction_mean_shift_z`), Population Stability Index (`compute_psi_1d`) across core E/M features, and error breakdown by event subtype, horizon, and market regime.
5. **Storage & System Health:** SQLite DB + WAL sizes, partitioned `.jsonl.gz` volume, free disk space (`GB`), process RSS memory & CPU% (`psutil`), queue backpressure drop counters, reconnect restart count, and explicit data loss incidents.

## 2. CLI Invocation

```powershell
.\.venv\Scripts\python.exe scripts/alpha_shadow_report.py --output-json docs/alpha/daily_health_sample.json
```
