"""Unit tests for Phase 5 Work Package D Target Engineering."""

from datetime import datetime, timedelta, timezone
import pytest

from backend.alpha.event_study import AnchorMode, EventStudyEngine, MarketCalendarConfig
from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.outcomes import OutcomeDataQualityStatus, OutcomeHorizon, OutcomeLabelType, PriceObservation
from backend.alpha.predictive_targets import (
    ActionableDirectionClass,
    CostAssumptionSpec,
    PredictiveTargetBuilder,
)


def test_target_builder_neutral_cost_band_and_reaction_vs_executable():
    """Verify economic NO_TRADE hurdle separates tiny friction-eaten moves and separates REACTION vs EXECUTABLE."""
    cal = MarketCalendarConfig.for_jurisdiction(MarketJurisdiction.IN)
    t0 = datetime(2026, 3, 2, 5, 0, tzinfo=timezone.utc)  # 10:30 IST
    # Construct 1m bars: jumps +0.8% between m=0 and m=1, then flat tiny +3 bps move m=1..6 (inside 10+ bps cost!)
    obs = []
    for m in range(10):
        ts = t0 + timedelta(minutes=m)
        if m == 0:
            p = 1000.0
        else:
            p = 1008.0 + (0.05 * (m - 1))  # tiny +0.30 pts (+3 bps) drift from m=1..6
        obs.append(PriceObservation(timestamp_utc=ts, price=p, bid=p - 0.15, ask=p + 0.15, volume=100.0))

    engine = EventStudyEngine(calendar=cal)
    pair = engine.compute_reaction_and_executable_pair(
        event_id="evt_t1",
        instrument_id="NSE:TCS:EQ",
        horizon=OutcomeHorizon.M5,
        event_timestamp_utc=t0,
        decision_timestamp_utc=t0,
        observations=obs,
        pipeline_latency_ms=50.0,
        executable_side=1,
        is_synthetic=True,
    )

    tb = PredictiveTargetBuilder(cost_spec=CostAssumptionSpec.indian_equity_intraday_conservative(), min_trainable_samples_per_horizon=1)
    tgt_react = tb.build_target(pair[OutcomeLabelType.REACTION], MarketJurisdiction.IN)
    tgt_exec = tb.build_target(pair[OutcomeLabelType.EXECUTABLE], MarketJurisdiction.IN)

    assert tgt_react.label_type == OutcomeLabelType.REACTION
    assert tgt_react.y_direction_3class == ActionableDirectionClass.LONG_POSITIVE
    assert tgt_react.y_positive_material == 1

    # Executable move starts at m=1 ask (~1008.15) and exits at m=6 bid (~1008.10) -> slightly negative/within cost hurdle!
    assert tgt_exec.label_type == OutcomeLabelType.EXECUTABLE
    assert tgt_exec.y_direction_3class == ActionableDirectionClass.NEUTRAL_NO_TRADE
    assert tgt_exec.y_positive_material == 0
    assert tgt_exec.y_material_move == 0

    # Horizon trainability gate rejects INSUFFICIENT_RESOLUTION & insufficient count
    low_res_obs = [
        PriceObservation(timestamp_utc=t0, price=1000.0),
        PriceObservation(timestamp_utc=t0 + timedelta(days=1), price=1010.0),
    ]
    lbl_low = engine.compute_outcome_label(
        "evt_low", "NSE:TCS:EQ", OutcomeHorizon.M5, t0, t0, low_res_obs, is_synthetic=True
    )
    tgt_low = tb.build_target(lbl_low, MarketJurisdiction.IN)
    assert tgt_low.data_quality_status == OutcomeDataQualityStatus.INSUFFICIENT_RESOLUTION
    assert tgt_low.is_trainable is False
    ok, cnt, reason = tb.check_horizon_trainability([tgt_low], OutcomeHorizon.M5, OutcomeLabelType.REACTION)
    assert ok is False and cnt == 0 and "INSUFFICIENT_RESOLUTION" in reason
