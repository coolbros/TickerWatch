"""Cost-Aware Research Backtester & Regime Breakdown Engine (Phase 5 WP-H).

Strictly separates research signal evaluation from live execution (NO broker routing,
NO live capital deployment). Enforces consumption of `OutcomeLabelType.EXECUTABLE`
labels ONLY, honors explicit `NO_TRADE` abstention states, deducts brokerage, exchange/SEBI
charges, STT/stamp/GST, bid-ask spread, and slippage/latency penalties on every trade,
and provides breakdowns by event family, instrument, volatility regime, time of day,
and calibrated confidence bucket plus transaction-cost stress sweeps.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence
import numpy as np
from pydantic import BaseModel, Field

from backend.alpha.outcomes import OutcomeHorizon, OutcomeLabelType
from backend.alpha.predictive_features import PointInTimeFeatureRow
from backend.alpha.predictive_targets import CostAssumptionSpec, PredictiveTargetRecord
from backend.alpha.prediction_uncertainty import CalibratedPrediction


class TradeExecutionRecord(BaseModel):
    """Single simulated historical research trade on an EXECUTABLE horizon label."""

    event_id: str
    instrument_id: str
    decision_timestamp: str
    entry_timestamp: Optional[str] = None
    exit_timestamp: Optional[str] = None
    direction: int
    action_state: str
    p_positive: float
    p_negative: float
    gross_return: float
    total_cost_bps: float
    net_return: float
    holding_minutes: float
    event_family: str
    volatility_regime: str
    time_of_day_bucket: str
    confidence_bucket: str
    is_synthetic: bool


class BacktestBreakdownRow(BaseModel):
    """Aggregated performance slice across one regime/taxonomy category."""

    category_key: str
    observation_count: int
    trade_count: int
    win_rate: float
    gross_mean_bps: float
    net_mean_bps: float
    net_total_return: float
    total_cost_bps: float


class BacktestSensitivityPoint(BaseModel):
    """Stress-test outcome under elevated slippage / cost / latency assumptions."""

    scenario_label: str
    cost_multiplier: float
    extra_latency_slippage_bps: float
    trade_count: int
    net_return_total: float
    net_mean_bps: float
    win_rate: float
    sharpe_ratio: float
    max_drawdown: float


class ResearchBacktestReport(BaseModel):
    """Full cost-adjusted research simulation report."""

    model_label: str
    horizon_label: str
    is_synthetic: bool
    label_type_enforced: str = OutcomeLabelType.EXECUTABLE.value

    observation_count: int
    trade_count: int
    abstain_count: int
    abstention_rate: float
    exposure_fraction: float

    gross_return_total: float
    net_return_total: float
    gross_return_mean_bps: float
    net_return_mean_bps: float
    total_cost_paid_bps: float
    avg_cost_per_trade_bps: float

    win_rate: float
    avg_win_bps: float
    avg_loss_bps: float
    profit_factor: float
    event_sharpe_ratio: float
    trade_sharpe_ratio: float
    annualized_sharpe_ratio: float
    max_drawdown: float
    turnover: float
    avg_holding_minutes: float
    tail_loss_p05_bps: float

    by_event_family: List[BacktestBreakdownRow] = Field(default_factory=list)
    by_instrument: List[BacktestBreakdownRow] = Field(default_factory=list)
    by_volatility_regime: List[BacktestBreakdownRow] = Field(default_factory=list)
    by_time_of_day: List[BacktestBreakdownRow] = Field(default_factory=list)
    by_confidence_bucket: List[BacktestBreakdownRow] = Field(default_factory=list)
    trades: List[TradeExecutionRecord] = Field(default_factory=list)


def _horizon_default_minutes(h: OutcomeHorizon) -> float:
    return {
        OutcomeHorizon.M1: 1.0,
        OutcomeHorizon.M5: 5.0,
        OutcomeHorizon.M30: 30.0,
        OutcomeHorizon.H1: 60.0,
        OutcomeHorizon.D1: 375.0,
        OutcomeHorizon.D5: 1875.0,
    }[h]


def _classify_event_family(row: Optional[PointInTimeFeatureRow]) -> str:
    if row is None:
        return "UNKNOWN"
    prov = row.provenance or {}
    if "primary_family" in prov:
        return str(prov["primary_family"])
    v = row.values
    if v.get("e_fam_corp_fin", 0.0) == 1.0:
        return "CORPORATE_FINANCIAL"
    if v.get("e_fam_corp_op", 0.0) == 1.0:
        return "CORPORATE_OPERATIONAL"
    if v.get("e_fam_macro", 0.0) == 1.0:
        return "MACROECONOMIC"
    if v.get("e_fam_market", 0.0) == 1.0:
        return "MARKET_SYSTEMIC"
    return "OTHER_OR_ROUTINE"


def _classify_vol_regime(row: Optional[PointInTimeFeatureRow]) -> str:
    if row is None:
        return "UNKNOWN"
    v = row.values
    if v.get("m_liq_thin", 0.0) == 1.0:
        return "THIN_OR_ILLIQUID"
    if v.get("m_vol_elevated", 0.0) == 1.0:
        return "ELEVATED_VOL"
    return "NORMAL_VOL"


def _classify_time_of_day(row: Optional[PointInTimeFeatureRow]) -> str:
    if row is None:
        return "UNKNOWN"
    v = row.values
    if v.get("m_session_open", 0.0) != 1.0:
        return "OFF_HOURS_NEXT_OPEN"
    if v.get("m_is_first_30m", 0.0) == 1.0:
        return "FIRST_30M"
    if v.get("m_is_last_30m", 0.0) == 1.0:
        return "LAST_30M"
    return "MID_SESSION"


def _classify_confidence_bucket(pred: CalibratedPrediction) -> str:
    conf = max(pred.p_positive, pred.p_negative)
    if conf >= 0.70:
        return "HIGH_>=0.70"
    if conf >= 0.60:
        return "MEDIUM_0.60-0.70"
    return "LOW_<0.60"


def _compute_max_drawdown(returns_seq: np.ndarray) -> float:
    if returns_seq.shape[0] == 0:
        return 0.0
    equity = np.cumprod(1.0 + np.clip(returns_seq, -0.99, 10.0))
    peaks = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    drawdowns = (peaks - equity) / np.maximum(peaks, 1e-12)
    return float(np.max(drawdowns))


class ResearchBacktester:
    """Deterministic cost-adjusted research simulator operating strictly on EXECUTABLE labels."""

    @staticmethod
    def assert_executable_targets(targets: Sequence[PredictiveTargetRecord]) -> None:
        for t in targets:
            if t.label_type != OutcomeLabelType.EXECUTABLE:
                raise ValueError(
                    f"Backtester REJECTED non-executable target for event={t.event_id}: "
                    f"got label_type={t.label_type.value}. Backtesting must NEVER use REACTION labels."
                )

    @classmethod
    def run(
        cls,
        model_label: str,
        predictions: Sequence[CalibratedPrediction],
        targets: Sequence[PredictiveTargetRecord],
        feature_rows: Optional[Sequence[PointInTimeFeatureRow]] = None,
        cost_multiplier: float = 1.0,
        extra_latency_slippage_bps: float = 0.0,
        events_per_year: float = 252.0,
    ) -> ResearchBacktestReport:
        n = len(predictions)
        if n == 0 or len(targets) != n:
            raise ValueError("predictions and executable targets must be non-empty and equal length.")
        if feature_rows is not None and len(feature_rows) != n:
            raise ValueError("feature_rows length must match predictions length when supplied.")

        cls.assert_executable_targets(targets)

        # Sort chronologically by decision_timestamp to compute realistic equity curves & drawdown
        order = sorted(range(n), key=lambda idx: targets[idx].decision_timestamp)
        horizon_label = targets[order[0]].horizon.value
        is_synth = any(t.is_synthetic for t in targets)

        gross_stream = np.zeros(n, dtype=np.float64)
        net_stream = np.zeros(n, dtype=np.float64)

        trades: List[TradeExecutionRecord] = []
        obs_categories: List[Dict[str, str]] = []
        abstain_count = 0

        for pos_idx, idx in enumerate(order):
            pred = predictions[idx]
            tgt = targets[idx]
            frow = feature_rows[idx] if feature_rows is not None else None

            if pred.event_id != tgt.event_id:
                raise ValueError(
                    f"Alignment mismatch at idx={idx}: prediction event {pred.event_id} != target event {tgt.event_id}"
                )

            fam = _classify_event_family(frow)
            vol_r = _classify_vol_regime(frow)
            tod = _classify_time_of_day(frow)
            cbkt = _classify_confidence_bucket(pred)
            cats = {
                "event_family": fam,
                "instrument_id": tgt.instrument_id,
                "volatility_regime": vol_r,
                "time_of_day": tod,
                "confidence_bucket": cbkt,
            }
            obs_categories.append(cats)

            if (
                pred.abstain
                or pred.action_state == "NO_TRADE"
                or pred.signal_direction == 0
                or not tgt.is_trainable
                or tgt.simple_return is None
            ):
                abstain_count += 1
                continue

            base_cost_bps = (
                float(tgt.transaction_cost_assumption_bps)
                if tgt.transaction_cost_assumption_bps is not None
                else CostAssumptionSpec.indian_nifty_fut_conservative().total_round_trip_cost_bps(
                    tgt.spread_bps
                )
            )
            applied_cost_bps = max(0.0, base_cost_bps * float(cost_multiplier) + float(extra_latency_slippage_bps))
            gross_r = float(pred.signal_direction) * float(tgt.simple_return)
            net_r = gross_r - (applied_cost_bps / 10000.0)

            gross_stream[pos_idx] = gross_r
            net_stream[pos_idx] = net_r

            if tgt.entry_timestamp is not None and tgt.exit_timestamp is not None:
                hold_min = max(0.0, (tgt.exit_timestamp - tgt.entry_timestamp).total_seconds() / 60.0)
            else:
                hold_min = _horizon_default_minutes(tgt.horizon)

            trades.append(
                TradeExecutionRecord(
                    event_id=tgt.event_id,
                    instrument_id=tgt.instrument_id,
                    decision_timestamp=tgt.decision_timestamp.isoformat(),
                    entry_timestamp=tgt.entry_timestamp.isoformat() if tgt.entry_timestamp else None,
                    exit_timestamp=tgt.exit_timestamp.isoformat() if tgt.exit_timestamp else None,
                    direction=int(pred.signal_direction),
                    action_state=pred.action_state,
                    p_positive=float(pred.p_positive),
                    p_negative=float(pred.p_negative),
                    gross_return=round(gross_r, 8),
                    total_cost_bps=round(applied_cost_bps, 4),
                    net_return=round(net_r, 8),
                    holding_minutes=round(hold_min, 2),
                    event_family=fam,
                    volatility_regime=vol_r,
                    time_of_day_bucket=tod,
                    confidence_bucket=cbkt,
                    is_synthetic=tgt.is_synthetic,
                )
            )

        trade_count = len(trades)
        gross_total = float(np.prod(1.0 + np.clip(gross_stream, -0.99, 10.0)) - 1.0)
        net_total = float(np.prod(1.0 + np.clip(net_stream, -0.99, 10.0)) - 1.0)

        ev_mean = float(np.mean(net_stream))
        ev_std = float(np.std(net_stream, ddof=1)) if n > 1 else 0.0
        event_sharpe = (ev_mean / ev_std) if ev_std > 1e-12 else 0.0
        ann_sharpe = event_sharpe * math.sqrt(max(1.0, float(events_per_year)))

        if trade_count > 0:
            tr_gross = np.asarray([t.gross_return for t in trades], dtype=np.float64)
            tr_net = np.asarray([t.net_return for t in trades], dtype=np.float64)
            tr_costs = np.asarray([t.total_cost_bps for t in trades], dtype=np.float64)
            wins = tr_net[tr_net > 0.0]
            losses = tr_net[tr_net <= 0.0]

            gross_mean_bps = float(np.mean(tr_gross) * 10000.0)
            net_mean_bps = float(np.mean(tr_net) * 10000.0)
            total_cost_bps = float(np.sum(tr_costs))
            avg_cost_bps = float(np.mean(tr_costs))
            win_rate = float(np.mean(tr_net > 0.0))
            avg_win_bps = float(np.mean(wins) * 10000.0) if wins.shape[0] > 0 else 0.0
            avg_loss_bps = float(np.mean(losses) * 10000.0) if losses.shape[0] > 0 else 0.0

            gross_wins = float(np.sum(wins))
            gross_losses = float(abs(np.sum(losses)))
            profit_factor = (gross_wins / gross_losses) if gross_losses > 1e-12 else (99.0 if gross_wins > 0.0 else 0.0)

            tr_std = float(np.std(tr_net, ddof=1)) if trade_count > 1 else 0.0
            trade_sharpe = (float(np.mean(tr_net)) / tr_std) if tr_std > 1e-12 else 0.0
            avg_hold = float(np.mean([t.holding_minutes for t in trades]))
            tail_p05 = float(np.percentile(tr_net, 5.0) * 10000.0)
        else:
            gross_mean_bps = 0.0
            net_mean_bps = 0.0
            total_cost_bps = 0.0
            avg_cost_bps = 0.0
            win_rate = 0.0
            avg_win_bps = 0.0
            avg_loss_bps = 0.0
            profit_factor = 0.0
            trade_sharpe = 0.0
            avg_hold = 0.0
            tail_p05 = 0.0

        max_dd = _compute_max_drawdown(net_stream)
        turnover = (2.0 * trade_count) / float(n)  # round-trip entry + exit per trade / observation

        def _build_breakdown(cat_field: str) -> List[BacktestBreakdownRow]:
            obs_counts: Dict[str, int] = {}
            for c in obs_categories:
                key = c[cat_field]
                obs_counts[key] = obs_counts.get(key, 0) + 1

            field_attr = {
                "event_family": "event_family",
                "instrument_id": "instrument_id",
                "volatility_regime": "volatility_regime",
                "time_of_day": "time_of_day_bucket",
                "confidence_bucket": "confidence_bucket",
            }[cat_field]

            rows: List[BacktestBreakdownRow] = []
            for key in sorted(obs_counts.keys()):
                sub_trades = [t for t in trades if getattr(t, field_attr) == key]
                if sub_trades:
                    sg = np.asarray([t.gross_return for t in sub_trades], dtype=np.float64)
                    sn = np.asarray([t.net_return for t in sub_trades], dtype=np.float64)
                    sc = np.asarray([t.total_cost_bps for t in sub_trades], dtype=np.float64)
                    wr = float(np.mean(sn > 0.0))
                    gm = float(np.mean(sg) * 10000.0)
                    nm = float(np.mean(sn) * 10000.0)
                    nt = float(np.prod(1.0 + np.clip(sn, -0.99, 10.0)) - 1.0)
                    tc = float(np.sum(sc))
                else:
                    wr = gm = nm = nt = tc = 0.0
                rows.append(
                    BacktestBreakdownRow(
                        category_key=key,
                        observation_count=obs_counts[key],
                        trade_count=len(sub_trades),
                        win_rate=round(wr, 4),
                        gross_mean_bps=round(gm, 4),
                        net_mean_bps=round(nm, 4),
                        net_total_return=round(nt, 6),
                        total_cost_bps=round(tc, 4),
                    )
                )
            return rows

        return ResearchBacktestReport(
            model_label=model_label,
            horizon_label=horizon_label,
            is_synthetic=is_synth,
            observation_count=n,
            trade_count=trade_count,
            abstain_count=abstain_count,
            abstention_rate=round(abstain_count / float(n), 6),
            exposure_fraction=round(trade_count / float(n), 6),
            gross_return_total=round(gross_total, 8),
            net_return_total=round(net_total, 8),
            gross_return_mean_bps=round(gross_mean_bps, 4),
            net_return_mean_bps=round(net_mean_bps, 4),
            total_cost_paid_bps=round(total_cost_bps, 4),
            avg_cost_per_trade_bps=round(avg_cost_bps, 4),
            win_rate=round(win_rate, 6),
            avg_win_bps=round(avg_win_bps, 4),
            avg_loss_bps=round(avg_loss_bps, 4),
            profit_factor=round(profit_factor, 4),
            event_sharpe_ratio=round(event_sharpe, 6),
            trade_sharpe_ratio=round(trade_sharpe, 6),
            annualized_sharpe_ratio=round(ann_sharpe, 6),
            max_drawdown=round(max_dd, 6),
            turnover=round(turnover, 6),
            avg_holding_minutes=round(avg_hold, 2),
            tail_loss_p05_bps=round(tail_p05, 4),
            by_event_family=_build_breakdown("event_family"),
            by_instrument=_build_breakdown("instrument_id"),
            by_volatility_regime=_build_breakdown("volatility_regime"),
            by_time_of_day=_build_breakdown("time_of_day"),
            by_confidence_bucket=_build_breakdown("confidence_bucket"),
            trades=trades,
        )

    @classmethod
    def run_cost_sensitivity_sweep(
        cls,
        model_label: str,
        predictions: Sequence[CalibratedPrediction],
        targets: Sequence[PredictiveTargetRecord],
        feature_rows: Optional[Sequence[PointInTimeFeatureRow]] = None,
    ) -> List[BacktestSensitivityPoint]:
        scenarios = [
            ("BASE_CONSERVATIVE_1.0x", 1.0, 0.0),
            ("STRESS_1.5x_PLUS_3BPS_LATENCY", 1.5, 3.0),
            ("STRESS_2.0x_PLUS_8BPS_LATENCY", 2.0, 8.0),
        ]
        pts: List[BacktestSensitivityPoint] = []
        for label, mult, lat in scenarios:
            rep = cls.run(
                model_label=model_label,
                predictions=predictions,
                targets=targets,
                feature_rows=feature_rows,
                cost_multiplier=mult,
                extra_latency_slippage_bps=lat,
            )
            pts.append(
                BacktestSensitivityPoint(
                    scenario_label=label,
                    cost_multiplier=mult,
                    extra_latency_slippage_bps=lat,
                    trade_count=rep.trade_count,
                    net_return_total=rep.net_return_total,
                    net_mean_bps=rep.net_return_mean_bps,
                    win_rate=rep.win_rate,
                    sharpe_ratio=rep.annualized_sharpe_ratio,
                    max_drawdown=rep.max_drawdown,
                )
            )
        return pts
