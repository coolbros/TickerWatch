"""Unit tests for Work Package H: evaluation metrics, alpha attribution, and cost-aware backtester."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import numpy as np
import pytest

from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.outcomes import BenchmarkAdjustmentMethod, OutcomeDataQualityStatus, OutcomeHorizon, OutcomeLabelType
from backend.alpha.predictive_evaluation import (
    AlphaAttributor,
    aggregate_fold_metrics,
    block_bootstrap_ci,
    compute_predictive_metrics,
    holm_bonferroni_adjust,
)
from backend.alpha.predictive_features import PointInTimeFeatureRow
from backend.alpha.predictive_targets import ActionableDirectionClass, PredictiveTargetRecord
from backend.alpha.prediction_uncertainty import AbstentionReason, CalibratedPrediction
from backend.alpha.research_backtest import ResearchBacktester


def _make_pred(
    event_id: str,
    inst_id: str,
    exp_ret: float,
    p_pos: float,
    abstain: bool = False,
    direction: int = 1,
) -> CalibratedPrediction:
    return CalibratedPrediction(
        event_id=event_id,
        instrument_id=inst_id,
        expected_return=exp_ret,
        median_return=exp_ret,
        p_positive=p_pos,
        p_negative=1.0 - p_pos,
        p_material_move=max(p_pos, 1.0 - p_pos),
        q10=exp_ret - 0.01,
        q25=exp_ret - 0.004,
        q50=exp_ret,
        q75=exp_ret + 0.004,
        q90=exp_ret + 0.01,
        predictive_std=0.008,
        signal_direction=0 if abstain else direction,
        abstain=abstain,
        action_state="NO_TRADE" if abstain else ("LONG" if direction > 0 else "SHORT"),
        abstain_reasons=[AbstentionReason.EXPECTED_RETURN_BELOW_COSTS] if abstain else [],
        estimated_round_trip_cost_bps=10.5,
        net_expected_edge_bps=0.0 if abstain else abs(exp_ret) * 10000.0 - 10.5,
    )


def _make_target(
    event_id: str,
    inst_id: str,
    ts: datetime,
    simple_ret: float,
    label_type: OutcomeLabelType = OutcomeLabelType.EXECUTABLE,
    cost_bps: float = 10.5,
) -> PredictiveTargetRecord:
    return PredictiveTargetRecord(
        event_id=event_id,
        instrument_id=inst_id,
        jurisdiction=MarketJurisdiction.IN,
        decision_timestamp=ts,
        horizon=OutcomeHorizon.D1,
        label_type=label_type,
        entry_timestamp=ts + timedelta(minutes=1),
        entry_price=100.0,
        exit_timestamp=ts + timedelta(hours=4),
        exit_price=100.0 * (1.0 + simple_ret),
        simple_return=simple_ret,
        log_return=simple_ret,
        benchmark_return=0.0,
        market_adjusted_return=simple_ret,
        benchmark_adjustment_method=BenchmarkAdjustmentMethod.SIMPLE_MARKET_ADJUSTED,
        cost_scenario="IN_NSE_EQUITY_CONSERVATIVE",
        transaction_cost_assumption_bps=cost_bps,
        spread_bps=2.0,
        estimated_slippage_bps=2.5,
        actionable_hurdle_bps=cost_bps + 2.0,
        y_continuous=simple_ret,
        y_direction_3class=(
            ActionableDirectionClass.LONG_POSITIVE if simple_ret > 0.00125 else ActionableDirectionClass.NEUTRAL_NO_TRADE
        ),
        y_positive_material=1 if simple_ret > 0.00125 else 0,
        y_negative_material=1 if simple_ret < -0.00125 else 0,
        y_material_move=1 if abs(simple_ret) > 0.00125 else 0,
        y_positive_raw=1 if simple_ret > 0 else 0,
        data_quality_status=OutcomeDataQualityStatus.VERIFIED,
        is_matured=True,
        is_trainable=True,
        source_provenance="SYNTHETIC_UNIT_TEST",
        is_synthetic=True,
    )


def _make_feature_row(event_id: str, inst_id: str, ts: datetime, elevated_vol: bool, first_30m: bool) -> PointInTimeFeatureRow:
    return PointInTimeFeatureRow(
        event_id=event_id,
        instrument_id=inst_id,
        jurisdiction=MarketJurisdiction.IN,
        decision_timestamp=ts,
        horizon_label=OutcomeHorizon.D1.value,
        is_synthetic=True,
        values={
            "e_fam_corp_fin": 1.0,
            "m_vol_elevated": 1.0 if elevated_vol else 0.0,
            "m_liq_thin": 0.0,
            "m_session_open": 1.0,
            "m_is_first_30m": 1.0 if first_30m else 0.0,
            "m_is_last_30m": 0.0,
        },
        provenance={"primary_family": "CORPORATE_FINANCIAL"},
    )


def test_metrics_attribution_holm_and_backtest_guards() -> None:
    rng = np.random.default_rng(7)
    n = 60
    t0 = datetime(2026, 5, 1, 4, 0, tzinfo=timezone.utc)
    y_true = rng.normal(0.0, 0.015, size=n)
    y_pos = [1 if y > 0.00125 else 0 for y in y_true]

    # Model D closely tracks ground truth; Model A has only noise
    d_preds = []
    a_noise_preds = []
    null_comp_preds = []
    targets_exec = []
    targets_react = []
    frows = []

    for i in range(n):
        ts = t0 + timedelta(hours=6 * i)
        ev = f"ev_{i}"
        inst = "NSE_INFY" if i % 2 == 0 else "NSE_TCS"
        d_hat = float(0.85 * y_true[i] + rng.normal(0, 0.002))
        a_hat = float(rng.normal(0.0, 0.012))
        d_p = float(np.clip(0.5 + d_hat * 20.0, 0.05, 0.95))
        a_p = float(np.clip(0.5 + a_hat * 10.0, 0.2, 0.8))

        # Force deterministic abstentions when |d_hat| < 20 bps
        abst = abs(d_hat) < 0.0020
        d_dir = 1 if d_hat > 0 else -1
        d_preds.append(_make_pred(ev, inst, d_hat, d_p, abstain=abst, direction=d_dir))
        a_noise_preds.append(_make_pred(ev, inst, a_hat, a_p, abstain=False, direction=(1 if a_hat > 0 else -1)))
        null_comp_preds.append(_make_pred(ev, inst, 0.0, 0.5, abstain=True, direction=0))

        targets_exec.append(_make_target(ev, inst, ts, float(y_true[i]), OutcomeLabelType.EXECUTABLE))
        targets_react.append(_make_target(ev, inst, ts, float(y_true[i]), OutcomeLabelType.REACTION))
        frows.append(_make_feature_row(ev, inst, ts, elevated_vol=(i % 3 == 0), first_30m=(i % 4 == 0)))

    # 1. Statistical metrics verification
    m_d = compute_predictive_metrics("MODEL_D", "D1", d_preds, y_true.tolist(), y_pos)
    m_a = compute_predictive_metrics("MODEL_A", "D1", a_noise_preds, y_true.tolist(), y_pos)
    assert m_d.rmse < m_a.rmse
    assert m_d.spearman_ic > 0.75
    assert m_d.roc_auc > 0.85
    assert m_d.interval_coverage_80pct is not None
    agg = aggregate_fold_metrics([m_d, m_d])
    assert agg.n_folds == 2 and agg.total_oos_samples == 2 * n

    # 2. Holm-Bonferroni & Block Bootstrap
    adj = holm_bonferroni_adjust([0.001, 0.02, 0.40])
    assert adj[0] == pytest.approx(0.003)
    assert adj[1] == pytest.approx(0.04)
    assert adj[2] == pytest.approx(0.40)

    lo, hi = block_bootstrap_ci([0.002] * 30, n_bootstrap=200, block_length=4, seed=1)
    assert lo == pytest.approx(0.002) and hi == pytest.approx(0.002)

    # 3. Attribution: Model D beats noisy Model A & Null significantly, while two equal models do not
    attr = AlphaAttributor.compare_candidate_vs_comparators(
        candidate_label="MODEL_D",
        candidate_preds=d_preds,
        comparators={"MODEL_A": a_noise_preds, "ZERO_BASELINE": null_comp_preds},
        y_continuous=y_true.tolist(),
        y_positive=y_pos,
        horizon_label=OutcomeHorizon.D1.value,
        is_synthetic=True,
        n_bootstrap=300,
    )
    assert attr.is_synthetic is True
    assert "SYNTHETIC DATA ONLY" in attr.honest_evidence_caveat
    assert all(r.statistically_significant_at_05 for r in attr.pairwise_comparisons)

    self_attr = AlphaAttributor.compare_candidate_vs_comparators(
        candidate_label="MODEL_A",
        candidate_preds=a_noise_preds,
        comparators={"MODEL_A_CLONE": a_noise_preds},
        y_continuous=y_true.tolist(),
        y_positive=y_pos,
        horizon_label=OutcomeHorizon.D1.value,
        is_synthetic=True,
        n_bootstrap=200,
    )
    assert self_attr.pairwise_comparisons[0].statistically_significant_at_05 is False

    # 4. Backtester strictly blocks REACTION labels
    with pytest.raises(ValueError, match="REJECTED non-executable"):
        ResearchBacktester.run("MODEL_D", d_preds, targets_react, frows)

    # 5. Backtester deducts costs, honors NO_TRADE abstentions, builds regime breakdowns & cost sweep
    bt = ResearchBacktester.run("MODEL_D", d_preds, targets_exec, frows)
    expected_abstains = sum(1 for p in d_preds if p.abstain)
    assert bt.abstain_count == expected_abstains
    assert bt.trade_count == n - expected_abstains
    assert bt.gross_return_total > bt.net_return_total
    assert bt.total_cost_paid_bps == pytest.approx(bt.trade_count * 10.5, rel=1e-4)
    assert len(bt.by_instrument) == 2
    assert len(bt.by_volatility_regime) >= 2
    assert len(bt.by_confidence_bucket) >= 1

    sweep = ResearchBacktester.run_cost_sensitivity_sweep("MODEL_D", d_preds, targets_exec, frows)
    assert len(sweep) == 3
    assert sweep[0].net_return_total > sweep[2].net_return_total
