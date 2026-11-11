"""Operational Observability & Drift Monitoring Engine (Phase 6 WP-H).

Aggregates machine-readable telemetry across all 5 operational planes directly from
`LiveCaptureStore`, `ShadowSignalLedger`, live feed runners, and OS host (`psutil`):
1. News Health
2. Market-Data Health
3. AI Pipeline Health
4. Predictive & Feature/Calibration Drift Monitoring
5. Storage, Resource, Backpressure & Restart Recovery Health
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Dict, List, Optional, Sequence
import numpy as np
import psutil
from pydantic import BaseModel, Field

from backend.alpha.live_data_capture import LiveCaptureStore
from backend.alpha.live_market_adapters import MarketFeedTelemetry
from backend.alpha.prediction_uncertainty import compute_calibration_metrics
from backend.alpha.shadow_ledger import ShadowLedgerEntry, ShadowOutcomeRecord, ShadowSignalLedger
from backend.alpha.timestamps import get_current_utc, parse_utc_iso


class NewsHealthMetrics(BaseModel):
    configured_sources: int = 0
    enabled_sources: int = 0
    articles_received: int = 0
    total_revisions_recorded: int = 0
    revision_rate: float = 0.0
    duplicate_or_unchanged_repolls: int = 0
    source_latency_p50_sec: Optional[float] = None
    source_latency_p95_sec: Optional[float] = None
    missing_publication_ts_count: int = 0
    entity_resolution_failure_count: int = 0
    entity_resolution_failure_rate: float = 0.0
    source_status_summary: Dict[str, str] = Field(default_factory=dict)


class MarketDataHealthMetrics(BaseModel):
    feed_uptime_fraction: float = 0.0
    message_throughput_total: int = 0
    sequence_gap_count: int = 0
    duplicate_packet_count: int = 0
    out_of_order_count: int = 0
    stale_feed_incidents: int = 0
    stale_data_frequency: float = 0.0
    quote_freshness_p50_ms: Optional[float] = None
    quote_freshness_p95_ms: Optional[float] = None
    missing_instruments: List[str] = Field(default_factory=list)
    invalid_price_or_qty_count: int = 0


class AIPipelineHealthMetrics(BaseModel):
    events_evaluated: int = 0
    latency_percentiles_ms: Dict[str, Dict[str, float]] = Field(default_factory=dict)
    mean_entity_resolution_confidence: Optional[float] = None
    extraction_empty_count: int = 0
    no_trade_count: int = 0
    no_trade_frequency: float = 0.0
    prediction_confidence_quantiles: Dict[str, float] = Field(default_factory=dict)


class PredictiveMonitorMetrics(BaseModel):
    matured_evaluations: int = 0
    mean_expected_return: Optional[float] = None
    std_expected_return: Optional[float] = None
    prediction_mean_shift_z: Optional[float] = None
    feature_drift_psi_max: Optional[float] = None
    feature_drift_top: Dict[str, float] = Field(default_factory=dict)
    realized_mae: Optional[float] = None
    realized_rmse: Optional[float] = None
    calibration_ece: Optional[float] = None
    error_by_subtype: Dict[str, float] = Field(default_factory=dict)
    error_by_horizon: Dict[str, float] = Field(default_factory=dict)
    error_by_market_regime: Dict[str, float] = Field(default_factory=dict)


class StorageSystemHealthMetrics(BaseModel):
    disk_total_gb: float
    disk_free_gb: float
    disk_used_pct: float
    capture_db_bytes: int
    capture_wal_bytes: int
    ledger_db_bytes: int
    ledger_wal_bytes: int
    partitioned_gz_bytes: int
    process_ram_mb: float
    process_cpu_pct: float
    system_ram_pct: float
    queue_backpressure_drops: int = 0
    restart_recovery_events: int = 0
    data_loss_incidents: int = 0


class OperationalHealthReport(BaseModel):
    """Complete machine-readable daily operational & AI telemetry report."""

    generated_at_utc: datetime = Field(default_factory=get_current_utc)
    news_health: NewsHealthMetrics
    market_health: MarketDataHealthMetrics
    ai_health: AIPipelineHealthMetrics
    predictive_monitoring: PredictiveMonitorMetrics
    storage_system_health: StorageSystemHealthMetrics
    alerts: List[str] = Field(default_factory=list)


def _percentiles(values: Sequence[float]) -> Dict[str, float]:
    if not values:
        return {}
    arr = np.asarray(values, dtype=np.float64)
    return {
        "p50": round(float(np.percentile(arr, 50)), 4),
        "p95": round(float(np.percentile(arr, 95)), 4),
        "p99": round(float(np.percentile(arr, 99)), 4),
        "max": round(float(np.max(arr)), 4),
    }


def compute_psi_1d(ref: np.ndarray, cur: np.ndarray, n_bins: int = 5) -> float:
    """Population Stability Index (PSI) between reference and current feature slices."""
    if ref.size < 5 or cur.size < 5:
        return 0.0
    qs = np.quantile(ref, np.linspace(0.0, 1.0, n_bins + 1))
    qs[0] = -np.inf
    qs[-1] = np.inf
    # Guard against collapsed quantile edges
    unique_edges = np.unique(qs)
    if unique_edges.size < 3:
        return 0.0
    ref_counts, _ = np.histogram(ref, bins=unique_edges)
    cur_counts, _ = np.histogram(cur, bins=unique_edges)
    ref_pct = np.clip(ref_counts / float(ref.size), 1e-4, 1.0)
    cur_pct = np.clip(cur_counts / float(cur.size), 1e-4, 1.0)
    return float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))


class ShadowOperationalMonitor:
    """Builds comprehensive daily health and drift reports from persisted stores."""

    def __init__(
        self,
        capture_store: LiveCaptureStore,
        shadow_ledger: ShadowSignalLedger,
        expected_instruments: Optional[Sequence[str]] = None,
    ) -> None:
        self.capture_store = capture_store
        self.shadow_ledger = shadow_ledger
        self.expected_instruments = list(expected_instruments or ("NSE:NIFTY_FUT:CURRENT",))

    def build_report(
        self,
        source_status: Optional[Dict[str, str]] = None,
        feed_telemetries: Optional[Sequence[MarketFeedTelemetry]] = None,
        include_synthetic: bool = True,
    ) -> OperationalHealthReport:
        alerts: List[str] = []
        src_map = dict(source_status or {})

        # 1. Query capture SQLite directly cleanly via context-managed connection
        with self.capture_store._connect() as conn:
            n_news = conn.execute("SELECT COUNT(*) FROM live_news_events").fetchone()[0]
            n_revised = conn.execute("SELECT COUNT(*) FROM live_news_events WHERE latest_revision > 1").fetchone()[0]
            n_revs_total = conn.execute("SELECT COUNT(*) FROM live_news_revisions").fetchone()[0]
            news_ts_rows = conn.execute(
                "SELECT published_at_utc, local_received_at_utc FROM live_news_revisions"
            ).fetchall()

            mkt_rows = conn.execute(
                "SELECT instrument_id, exchange_timestamp_utc, local_received_at_utc, bid_price, ask_price, last_traded_price FROM live_market_snapshots"
            ).fetchall()
            conn_rows = conn.execute("SELECT state, details FROM live_connection_events").fetchall()
            inc_rows = conn.execute("SELECT incident_type FROM live_feed_incidents").fetchall()

        news_lats_sec: List[float] = []
        missing_pub = 0
        for pub_s, recv_s in news_ts_rows:
            if not pub_s:
                missing_pub += 1
                continue
            dt_pub = parse_utc_iso(pub_s)
            dt_recv = parse_utc_iso(recv_s)
            if dt_pub and dt_recv:
                news_lats_sec.append(max(0.0, (dt_recv - dt_pub).total_seconds()))

        lat_p50_sec = round(float(np.percentile(news_lats_sec, 50)), 3) if news_lats_sec else None
        lat_p95_sec = round(float(np.percentile(news_lats_sec, 95)), 3) if news_lats_sec else None

        # Market health calculations
        observed_insts = {r[0] for r in mkt_rows}
        missing_insts = [inst for inst in self.expected_instruments if inst not in observed_insts]
        freshness_ms: List[float] = []
        invalid_px_qty = 0
        for _, exch_s, recv_s, bid, ask, ltp in mkt_rows:
            if bid is not None and ask is not None and (bid <= 0 or ask <= 0 or bid > ask):
                invalid_px_qty += 1
            if ltp is not None and ltp <= 0:
                invalid_px_qty += 1
            dt_e = parse_utc_iso(exch_s)
            dt_r = parse_utc_iso(recv_s)
            if dt_e and dt_r:
                freshness_ms.append(max(0.0, (dt_r - dt_e).total_seconds() * 1000.0))

        gap_cnt = sum(1 for r in inc_rows if r[0] == "GAP")
        dup_cnt = sum(1 for r in inc_rows if r[0] == "DUPLICATE")
        ooo_cnt = sum(1 for r in inc_rows if r[0] == "OUT_OF_ORDER")
        stale_cnt = sum(1 for r in inc_rows if "STALE" in str(r[0]).upper())
        q_drops = sum(1 for r in inc_rows if "OVERFLOW" in str(r[0]).upper() or "DROP" in str(r[0]).upper())
        loss_cnt = sum(1 for r in inc_rows if "DATA_LOSS" in str(r[0]).upper())

        for ft in feed_telemetries or ():
            gap_cnt += ft.gap_count
            dup_cnt += ft.duplicate_count
            ooo_cnt += ft.out_of_order_count
            stale_cnt += ft.heartbeat_timeouts
            q_drops += ft.queue_overflow_drops

        streaming_evts = sum(1 for s, _ in conn_rows if s == "STREAMING")
        uptime_frac = (
            min(1.0, streaming_evts / float(max(1, sum(1 for s, _ in conn_rows if s in ("STREAMING", "FAILED", "AUTH_ERROR")))))
            if conn_rows
            else (1.0 if mkt_rows else 0.0)
        )
        restart_cnt = sum(1 for s, _ in conn_rows if s in ("RECONNECTING", "CONNECTING"))

        fresh_p50 = round(float(np.percentile(freshness_ms, 50)), 3) if freshness_ms else None
        fresh_p95 = round(float(np.percentile(freshness_ms, 95)), 3) if freshness_ms else None

        # 2. Shadow ledger analysis (AI Health + Predictive Monitoring)
        preds = self.shadow_ledger.list_predictions(include_synthetic=include_synthetic)
        n_preds = len(preds)
        ent_fail = 0
        ent_confs: List[float] = []
        extract_empty = 0
        abstain_cnt = 0
        exp_rets: List[float] = []
        conf_vals: List[float] = []
        stage_lats: Dict[str, List[float]] = defaultdict(list)

        for p in preds:
            er = p.event_representation or {}
            pe = er.get("primary_entity", {})
            if pe.get("resolution_state") in (None, "UNRESOLVED"):
                ent_fail += 1
            if "confidence" in pe and pe["confidence"] is not None:
                ent_confs.append(float(pe["confidence"]))
            ext = er.get("extraction", {})
            if not ext.get("metrics"):
                extract_empty += 1
            if p.no_trade_status:
                abstain_cnt += 1
            if p.expected_return is not None:
                exp_rets.append(float(p.expected_return))
            probs = p.predicted_direction_probabilities or {}
            pp = probs.get("p_positive")
            pn = probs.get("p_negative")
            if pp is not None and pn is not None:
                conf_vals.append(max(float(pp), float(pn)))
            for sname, s_ms in (p.latency_breakdown_ms or {}).items():
                stage_lats[sname].append(float(s_ms))

        # Outcomes joined by prediction_id
        realized_errs_abs: List[float] = []
        realized_errs_sq: List[float] = []
        cal_probs: List[float] = []
        cal_bins: List[int] = []
        err_sub: Dict[str, List[float]] = defaultdict(list)
        err_hor: Dict[str, List[float]] = defaultdict(list)
        err_reg: Dict[str, List[float]] = defaultdict(list)

        for p in preds:
            outs = self.shadow_ledger.get_outcomes_for_prediction(p.prediction_id)
            matured = [o for o in outs if o.outcome_matured and o.executable_realized_return is not None]
            if not matured:
                continue
            out = matured[-1]
            y_real = float(out.executable_realized_return)
            if p.expected_return is not None:
                err = abs(float(p.expected_return) - y_real)
                realized_errs_abs.append(err)
                realized_errs_sq.append(err * err)
                sub = str((p.event_representation or {}).get("classification", {}).get("primary_subtype", "UNKNOWN"))
                reg = str((p.market_regime_representation or {}).get("volatility_regime", "UNAVAILABLE"))
                err_sub[sub].append(err)
                err_hor[out.horizon_label].append(err)
                err_reg[reg].append(err)
            pp = (p.predicted_direction_probabilities or {}).get("p_positive")
            if pp is not None:
                cal_probs.append(float(pp))
                cal_bins.append(1 if y_real > 0.0 else 0)

        # Drift calculation across chronological first half vs second half
        pred_shift_z: Optional[float] = None
        if len(exp_rets) >= 10:
            half = len(exp_rets) // 2
            a1 = np.asarray(exp_rets[:half], dtype=np.float64)
            a2 = np.asarray(exp_rets[half:], dtype=np.float64)
            sd = float(np.std(a1))
            pred_shift_z = round(abs(float(np.mean(a2)) - float(np.mean(a1))) / max(sd, 1e-6), 4)

        psi_by_feat: Dict[str, float] = {}
        if n_preds >= 12:
            half = n_preds // 2
            candidate_feats = ("e_cls_confidence", "e_mat_composite", "e_surprise_z", "m_spread_bps", "m_realized_vol_bps")
            for fn in candidate_feats:
                v1 = np.asarray([p.feature_values[fn] for p in preds[:half] if p.feature_values.get(fn) is not None], dtype=np.float64)
                v2 = np.asarray([p.feature_values[fn] for p in preds[half:] if p.feature_values.get(fn) is not None], dtype=np.float64)
                psi_val = compute_psi_1d(v1, v2)
                if psi_val > 0.0:
                    psi_by_feat[fn] = round(psi_val, 4)

        cal_ece: Optional[float] = None
        if len(cal_probs) >= 5:
            cal_rep = compute_calibration_metrics(cal_probs, cal_bins)
            cal_ece = cal_rep.ece

        # 3. System & Storage health
        c_stats = self.capture_store.get_storage_stats()
        l_wal = Path(str(self.shadow_ledger.db_path) + "-wal")
        l_db_b = self.shadow_ledger.db_path.stat().st_size if self.shadow_ledger.db_path.exists() else 0
        l_wal_b = l_wal.stat().st_size if l_wal.exists() else 0

        du = shutil.disk_usage(self.capture_store.db_path.parent)
        proc = psutil.Process()
        vm = psutil.virtual_memory()

        if missing_insts:
            alerts.append(f"MISSING_INSTRUMENTS: {', '.join(missing_insts)}")
        if gap_cnt > 0:
            alerts.append(f"MARKET_SEQUENCE_GAPS_DETECTED: count={gap_cnt}")
        if q_drops > 0:
            alerts.append(f"QUEUE_BACKPRESSURE_DROPS: count={q_drops}")
        if invalid_px_qty > 0:
            alerts.append(f"INVALID_MARKET_PRICES: count={invalid_px_qty}")

        return OperationalHealthReport(
            news_health=NewsHealthMetrics(
                configured_sources=len(src_map),
                enabled_sources=sum(1 for v in src_map.values() if v.upper() in ("HEALTHY", "ENABLED", "ACTIVE")),
                articles_received=int(n_news),
                total_revisions_recorded=int(n_revs_total),
                revision_rate=round(n_revised / float(max(1, n_news)), 4) if n_news else 0.0,
                source_latency_p50_sec=lat_p50_sec,
                source_latency_p95_sec=lat_p95_sec,
                missing_publication_ts_count=missing_pub,
                entity_resolution_failure_count=ent_fail,
                entity_resolution_failure_rate=round(ent_fail / float(max(1, n_preds)), 4) if n_preds else 0.0,
                source_status_summary=src_map,
            ),
            market_health=MarketDataHealthMetrics(
                feed_uptime_fraction=round(uptime_frac, 4),
                message_throughput_total=len(mkt_rows),
                sequence_gap_count=gap_cnt,
                duplicate_packet_count=dup_cnt,
                out_of_order_count=ooo_cnt,
                stale_feed_incidents=stale_cnt,
                stale_data_frequency=round(stale_cnt / float(max(1, len(mkt_rows))), 6) if mkt_rows else 0.0,
                quote_freshness_p50_ms=fresh_p50,
                quote_freshness_p95_ms=fresh_p95,
                missing_instruments=missing_insts,
                invalid_price_or_qty_count=invalid_px_qty,
            ),
            ai_health=AIPipelineHealthMetrics(
                events_evaluated=n_preds,
                latency_percentiles_ms={k: _percentiles(v) for k, v in stage_lats.items()},
                mean_entity_resolution_confidence=round(float(np.mean(ent_confs)), 4) if ent_confs else None,
                extraction_empty_count=extract_empty,
                no_trade_count=abstain_cnt,
                no_trade_frequency=round(abstain_cnt / float(max(1, n_preds)), 4) if n_preds else 0.0,
                prediction_confidence_quantiles=_percentiles(conf_vals),
            ),
            predictive_monitoring=PredictiveMonitorMetrics(
                matured_evaluations=len(realized_errs_abs),
                mean_expected_return=round(float(np.mean(exp_rets)), 6) if exp_rets else None,
                std_expected_return=round(float(np.std(exp_rets)), 6) if exp_rets else None,
                prediction_mean_shift_z=pred_shift_z,
                feature_drift_psi_max=max(psi_by_feat.values()) if psi_by_feat else None,
                feature_drift_top=psi_by_feat,
                realized_mae=round(float(np.mean(realized_errs_abs)), 6) if realized_errs_abs else None,
                realized_rmse=round(float(math.sqrt(float(np.mean(realized_errs_sq)))), 6) if realized_errs_sq else None,
                calibration_ece=cal_ece,
                error_by_subtype={k: round(float(np.mean(v)), 6) for k, v in sorted(err_sub.items())},
                error_by_horizon={k: round(float(np.mean(v)), 6) for k, v in sorted(err_hor.items())},
                error_by_market_regime={k: round(float(np.mean(v)), 6) for k, v in sorted(err_reg.items())},
            ),
            storage_system_health=StorageSystemHealthMetrics(
                disk_total_gb=round(du.total / (1024**3), 2),
                disk_free_gb=round(du.free / (1024**3), 2),
                disk_used_pct=round((du.used / float(du.total)) * 100.0, 2),
                capture_db_bytes=c_stats.db_size_bytes,
                capture_wal_bytes=c_stats.wal_size_bytes,
                ledger_db_bytes=l_db_b,
                ledger_wal_bytes=l_wal_b,
                partitioned_gz_bytes=c_stats.partitioned_jsonl_gz_bytes,
                process_ram_mb=round(proc.memory_info().rss / (1024**2), 2),
                process_cpu_pct=round(proc.cpu_percent(interval=None), 2),
                system_ram_pct=round(float(vm.percent), 2),
                queue_backpressure_drops=q_drops,
                restart_recovery_events=restart_cnt,
                data_loss_incidents=loss_cnt,
            ),
            alerts=alerts,
        )
