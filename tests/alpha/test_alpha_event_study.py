"""Unit tests for Phase 4 Event-Study Engine & Outcome Labels (Work Package E).

All synthetic bar series constructed here are explicitly marked `is_synthetic=True`
for deterministic testing of market calendar mechanics and causal guards.
"""

from datetime import date, datetime, timedelta, timezone
import math
import pytest

from backend.alpha.event_study import (
    AnchorMode,
    EventStudyEngine,
    FuturesRolloverMethod,
    FuturesRolloverSpec,
    MarketCalendarConfig,
    select_study_instruments,
)
from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.nlp_pipeline import get_nlp_pipeline
from backend.alpha.outcomes import (
    OutcomeDataQualityStatus,
    OutcomeHorizon,
    PriceObservation,
)


def _generate_intraday_1m_bars(
    start_ist_date: date,
    num_days: int,
    base_price: float = 1000.0,
    daily_drift: float = 0.0,
    holidays=None,
) -> list[PriceObservation]:
    """Generate clean 1-minute synthetic bars during NSE open hours (03:45-10:00 UTC)."""
    cal = MarketCalendarConfig.for_jurisdiction(MarketJurisdiction.IN, holidays=holidays)
    obs = []
    d = start_ist_date
    days_added = 0
    price = base_price
    while days_added < num_days:
        if cal.is_trading_day(d):
            open_utc = datetime(d.year, d.month, d.day, 3, 45, tzinfo=timezone.utc)
            for m in range(0, 376):  # 375 mins from 09:15 to 15:30 IST
                ts = open_utc + timedelta(minutes=m)
                p = price * (1.0 + 0.0001 * math.sin(m / 15.0))
                obs.append(
                    PriceObservation(
                        timestamp_utc=ts,
                        price=round(p, 4),
                        bid=round(p - 0.25, 4),
                        ask=round(p + 0.25, 4),
                        volume=1500.0,
                    )
                )
            price = price * (1.0 + daily_drift)
            days_added += 1
        d += timedelta(days=1)
    return obs


def test_event_study_intraday_returns_mfe_mae_and_benchmark():
    """Verify exact intraday simple/log return, MFE/MAE, spread, RV, and benchmark excess return."""
    start_ts = datetime(2026, 3, 2, 4, 30, tzinfo=timezone.utc)  # 10:00 IST Monday
    obs = []
    bench_obs = []
    # Build 65 1-minute bars: 10:00 -> 11:04 IST
    # At m=0: P=100.0; ramp up to 106.0 at m=15 (MFE=+0.06), dip to 98.0 at m=30 (MAE=-0.02), settle at 104.0 at m=60 (+0.04)
    for m in range(0, 65):
        ts = start_ts + timedelta(minutes=m)
        if m == 0:
            p = 100.0
        elif m <= 15:
            p = 100.0 + (6.0 * m / 15.0)
        elif m <= 30:
            p = 106.0 - (8.0 * (m - 15) / 15.0)
        else:
            p = 98.0 + (6.0 * (m - 30) / 30.0) if m <= 60 else 104.0

        obs.append(
            PriceObservation(
                timestamp_utc=ts,
                price=round(p, 4),
                bid=round(p - 0.05, 4),
                ask=round(p + 0.05, 4),
                volume=100.0,
            )
        )
        # Benchmark grows flat +1% linearly over 60 mins
        bp = 20000.0 + (200.0 * min(m, 60) / 60.0)
        bench_obs.append(PriceObservation(timestamp_utc=ts, price=bp))

    engine = EventStudyEngine(calendar=MarketCalendarConfig.for_jurisdiction(MarketJurisdiction.IN))
    lbl_1h = engine.compute_outcome_label(
        event_id="evt_intraday_1",
        instrument_id="NSE:TCS:EQ",
        horizon=OutcomeHorizon.H1,
        event_timestamp_utc=start_ts,
        decision_timestamp_utc=start_ts,
        observations=obs,
        benchmark_instrument="NSE:NIFTY_FUT:CURRENT",
        benchmark_observations=bench_obs,
        is_synthetic=True,
    )

    assert lbl_1h.data_quality_status == OutcomeDataQualityStatus.VERIFIED
    assert lbl_1h.simple_return == pytest.approx(0.04, abs=1e-4)
    assert lbl_1h.raw_log_return == pytest.approx(math.log(1.04), abs=1e-4)
    assert lbl_1h.benchmark_return == pytest.approx(0.01, abs=1e-4)
    assert lbl_1h.benchmark_adjusted_return == pytest.approx(0.03, abs=1e-4)
    assert lbl_1h.max_favorable_excursion == pytest.approx(0.06, abs=1e-4)
    assert lbl_1h.max_adverse_excursion == pytest.approx(-0.02, abs=1e-4)
    assert lbl_1h.realized_volatility is not None and lbl_1h.realized_volatility > 0
    assert lbl_1h.avg_spread_bps is not None and lbl_1h.avg_spread_bps > 0
    assert lbl_1h.total_volume == pytest.approx(6000.0, abs=1.0)
    assert lbl_1h.overnight_gap_included is False
    assert lbl_1h.is_synthetic is True


def test_event_study_resolution_gate_blocks_intraday_on_daily_bars():
    """Verify daily bars reject 1m/5m/30m/1h with INSUFFICIENT_RESOLUTION while computing 1d/5d."""
    cal = MarketCalendarConfig.for_jurisdiction(MarketJurisdiction.IN)
    d = date(2026, 3, 2)  # Monday
    daily_obs = []
    price = 1500.0
    for _ in range(10):
        # 15:30 IST close = 10:00 UTC
        daily_obs.append(
            PriceObservation(
                timestamp_utc=datetime(d.year, d.month, d.day, 10, 0, tzinfo=timezone.utc),
                price=price,
            )
        )
        price += 15.0
        d = cal.next_trading_day(d, 1)

    engine = EventStudyEngine(calendar=cal)
    decision_ts = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)
    outcomes = engine.compute_multi_horizon_outcomes(
        event_id="evt_daily_only",
        instrument_id="NSE:INFY:EQ",
        event_timestamp_utc=decision_ts,
        decision_timestamp_utc=decision_ts,
        observations=daily_obs,
        is_synthetic=True,
    )

    for h in (OutcomeHorizon.M1, OutcomeHorizon.M5, OutcomeHorizon.M30, OutcomeHorizon.H1):
        assert outcomes[h].data_quality_status == OutcomeDataQualityStatus.INSUFFICIENT_RESOLUTION
        assert outcomes[h].simple_return is None

    assert outcomes[OutcomeHorizon.D1].data_quality_status == OutcomeDataQualityStatus.VERIFIED
    assert outcomes[OutcomeHorizon.D1].simple_return == pytest.approx(15.0 / 1500.0, abs=1e-4)
    assert outcomes[OutcomeHorizon.D5].data_quality_status == OutcomeDataQualityStatus.VERIFIED
    assert outcomes[OutcomeHorizon.D5].simple_return == pytest.approx(75.0 / 1500.0, abs=1e-4)


def test_event_study_after_hours_overnight_gap_and_holiday_skip():
    """Verify after-hours announcement captures overnight gap and skips holidays/weekends on 1d/5d."""
    # Friday 2026-03-06 close is 10:00 UTC (15:30 IST).
    # Monday 2026-03-09 is declared a holiday -> Next trading day is Tuesday 2026-03-10!
    holidays = {date(2026, 3, 9)}
    cal = MarketCalendarConfig.for_jurisdiction(MarketJurisdiction.IN, holidays=holidays)

    bars = _generate_intraday_1m_bars(date(2026, 3, 6), num_days=7, base_price=1000.0, holidays=holidays)
    # Apply explicit +5% gap up starting Tuesday 2026-03-10 open (03:45 UTC) onward
    gap_cutoff = datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc)
    gapped_bars = []
    for o in bars:
        if o.timestamp_utc >= gap_cutoff:
            gapped_bars.append(o.model_copy(update={"price": round(o.price * 1.05, 4)}))
        else:
            gapped_bars.append(o)

    # Event announced Friday 18:00 IST (12:30 UTC), after market close
    decision_ts = datetime(2026, 3, 6, 12, 30, tzinfo=timezone.utc)

    engine_pre = EventStudyEngine(calendar=cal, anchor_mode=AnchorMode.PRE_EVENT_AS_OF)
    lbl_1d = engine_pre.compute_outcome_label(
        event_id="evt_after_hrs",
        instrument_id="NSE:RELIANCE:EQ",
        horizon=OutcomeHorizon.D1,
        event_timestamp_utc=decision_ts,
        decision_timestamp_utc=decision_ts,
        observations=gapped_bars,
        is_synthetic=True,
    )

    assert lbl_1d.data_quality_status == OutcomeDataQualityStatus.VERIFIED
    assert lbl_1d.overnight_gap_included is True
    assert lbl_1d.session_boundary_crossed is True
    # Should land on Tuesday 2026-03-10 close (skipping weekend & Monday holiday!)
    assert lbl_1d.outcome_end_price_ts.date() == date(2026, 3, 10)
    assert lbl_1d.simple_return == pytest.approx(0.05, abs=0.005)

    # Now verify POST_DECISION_EXECUTABLE enters AFTER open (Tuesday 09:15 IST), not capturing the overnight gap
    engine_exec = EventStudyEngine(calendar=cal, anchor_mode=AnchorMode.POST_DECISION_EXECUTABLE)
    lbl_exec = engine_exec.compute_outcome_label(
        event_id="evt_after_hrs",
        instrument_id="NSE:RELIANCE:EQ",
        horizon=OutcomeHorizon.D1,
        event_timestamp_utc=decision_ts,
        decision_timestamp_utc=decision_ts,
        observations=gapped_bars,
        is_synthetic=True,
    )
    assert lbl_exec.outcome_start_price_ts >= datetime(2026, 3, 10, 3, 45, tzinfo=timezone.utc)
    assert abs(lbl_exec.simple_return) < 0.005  # Intraday drift after gap open


def test_event_study_futures_rollover_rejection_and_adjustment():
    """Never silently concatenate futures across expiry without a FuturesRolloverSpec."""
    t0 = datetime(2026, 3, 26, 4, 30, tzinfo=timezone.utc)  # March expiry day morning
    obs = []
    # 0..30m on NIFTY26MARFUT (~22000), 31..65m on NIFTY26APRFUT (~22150 = +150 rollover basis)
    for m in range(65):
        ts = t0 + timedelta(minutes=m)
        if m <= 30:
            obs.append(PriceObservation(timestamp_utc=ts, price=22000.0 + m, contract_id="NIFTY26MARFUT"))
        else:
            obs.append(PriceObservation(timestamp_utc=ts, price=22150.0 + m, contract_id="NIFTY26APRFUT"))

    engine_no_spec = EventStudyEngine(calendar=MarketCalendarConfig.for_jurisdiction(MarketJurisdiction.IN))
    lbl_reject = engine_no_spec.compute_outcome_label(
        event_id="evt_roll_1",
        instrument_id="NSE:NIFTY_FUT:CURRENT",
        horizon=OutcomeHorizon.H1,
        event_timestamp_utc=t0,
        decision_timestamp_utc=t0,
        observations=obs,
        is_synthetic=True,
    )
    assert lbl_reject.data_quality_status == OutcomeDataQualityStatus.ROLLOVER_INVALID
    assert lbl_reject.simple_return is None
    assert lbl_reject.contract_rollover_applied is False

    # Now supply documented Panama spread adjustment (+150.0 points between APR and MAR)
    spec = FuturesRolloverSpec(
        from_contract_id="NIFTY26MARFUT",
        to_contract_id="NIFTY26APRFUT",
        method=FuturesRolloverMethod.ADDITIVE_SPREAD_BACK_ADJUST,
        price_spread_to_minus_from=150.0,
    )
    engine_with_spec = EventStudyEngine(
        calendar=MarketCalendarConfig.for_jurisdiction(MarketJurisdiction.IN),
        rollover_specs=[spec],
    )
    lbl_adj = engine_with_spec.compute_outcome_label(
        event_id="evt_roll_1",
        instrument_id="NSE:NIFTY_FUT:CURRENT",
        horizon=OutcomeHorizon.H1,
        event_timestamp_utc=t0,
        decision_timestamp_utc=t0,
        observations=obs,
        is_synthetic=True,
    )
    assert lbl_adj.data_quality_status == OutcomeDataQualityStatus.ROLLOVER_ADJUSTED
    assert lbl_adj.contract_rollover_applied is True
    assert lbl_adj.start_contract_id == "NIFTY26MARFUT"
    assert lbl_adj.end_contract_id == "NIFTY26APRFUT"
    # True underlying return = +60 points over 22000 = +0.002727 (NOT +210/22000!)
    assert lbl_adj.simple_return == pytest.approx(60.0 / 22000.0, abs=1e-5)


def test_event_study_halts_corporate_actions_and_maturity_guards():
    """Verify HALTED_TRADING, CORPORATE_ACTION_ADJUSTED, and IMMATURE enforcement."""
    t0 = datetime(2026, 3, 2, 5, 0, tzinfo=timezone.utc)
    # 1. Halt test
    halted_obs = [
        PriceObservation(timestamp_utc=t0, price=500.0),
        PriceObservation(timestamp_utc=t0 + timedelta(minutes=5), price=505.0, is_halted=True),
    ]
    engine = EventStudyEngine(calendar=MarketCalendarConfig.for_jurisdiction(MarketJurisdiction.IN))
    lbl_halt = engine.compute_outcome_label(
        event_id="evt_halt",
        instrument_id="NSE:LT:EQ",
        horizon=OutcomeHorizon.M5,
        event_timestamp_utc=t0,
        decision_timestamp_utc=t0,
        observations=halted_obs,
        is_synthetic=True,
    )
    assert lbl_halt.data_quality_status == OutcomeDataQualityStatus.HALTED_TRADING
    assert lbl_halt.simple_return is None

    # 2. Corporate action (2:1 stock split at t0+3m: raw price drops 1000->510 while factor doubles 1.0->2.0)
    ca_obs = [
        PriceObservation(timestamp_utc=t0 + timedelta(minutes=m), price=(1000.0 if m < 3 else 510.0), corporate_action_factor=(1.0 if m < 3 else 2.0))
        for m in range(7)
    ]
    lbl_ca = engine.compute_outcome_label(
        event_id="evt_split",
        instrument_id="NSE:TCS:EQ",
        horizon=OutcomeHorizon.M5,
        event_timestamp_utc=t0,
        decision_timestamp_utc=t0,
        observations=ca_obs,
        is_synthetic=True,
    )
    assert lbl_ca.data_quality_status == OutcomeDataQualityStatus.CORPORATE_ACTION_ADJUSTED
    # Adjusted 1000 -> 1020 => +2% return (not -49%!)
    assert lbl_ca.simple_return == pytest.approx(0.02, abs=1e-5)

    # 3. Causal Maturity enforcement (evaluation_decision_ts before endpoint)
    lbl_immature = engine.compute_outcome_label(
        event_id="evt_split",
        instrument_id="NSE:TCS:EQ",
        horizon=OutcomeHorizon.M5,
        event_timestamp_utc=t0,
        decision_timestamp_utc=t0,
        observations=ca_obs,
        evaluation_decision_ts=t0 + timedelta(minutes=2),
        is_synthetic=True,
    )
    assert lbl_immature.data_quality_status == OutcomeDataQualityStatus.IMMATURE
    assert lbl_immature.simple_return is None
    assert lbl_immature.is_matured_at(t0 + timedelta(minutes=2)) is False
    assert lbl_ca.is_matured_at(t0 + timedelta(minutes=5)) is True


def test_primary_vs_contextual_instrument_selection():
    """Corporate events pick cash equity / SSF primary; macro RBI selects NIFTY_FUT primary."""
    pipeline = get_nlp_pipeline()
    corp_rep = pipeline.process_raw_text(
        headline="TCS Q3 net profit rises 12% to Rs 12000 crore",
        local_received_at=datetime(2026, 3, 2, 5, 0, tzinfo=timezone.utc),
    )
    prim_corp, ctx_corp, bench_corp = select_study_instruments(corp_rep, MarketJurisdiction.IN)
    assert prim_corp == "NSE:TCS:EQ"
    assert bench_corp == "NSE:NIFTY_FUT:CURRENT"
    assert "NSE:NIFTY_FUT:CURRENT" in ctx_corp or bench_corp == "NSE:NIFTY_FUT:CURRENT"

    macro_rep = pipeline.process_raw_text(
        headline="RBI MPC holds repo rate steady at 6.50 percent",
        local_received_at=datetime(2026, 3, 2, 5, 0, tzinfo=timezone.utc),
    )
    prim_macro, _, _ = select_study_instruments(macro_rep, MarketJurisdiction.IN)
    assert prim_macro == "NSE:NIFTY_FUT:CURRENT"


def test_reaction_vs_executable_spread_latency_and_pre_event_beta():
    """
    Verify A4 (REACTION vs EXECUTABLE labels with pipeline latency, bid/ask spread, next-session open)
    and A5 (pre-event rolling OLS beta strictly excluding post-event prices).
    """
    from backend.alpha.outcomes import BenchmarkAdjustmentMethod, OutcomeLabelType

    cal = MarketCalendarConfig.for_jurisdiction(MarketJurisdiction.IN)
    decision_ts = datetime(2026, 3, 2, 5, 0, tzinfo=timezone.utc)
    obs = []
    bench = []
    p_stock = 1000.0
    p_bench = 20000.0
    for m in range(-20, 15):
        ts = decision_ts + timedelta(minutes=m)
        if m < 0:
            rb = 0.004 if (m % 2 == 0) else -0.002
            p_bench = p_bench * (1.0 + rb)
            p_stock = p_stock * (1.0 + 2.0 * rb)
        elif m == 0:
            p_bench = p_bench * 1.001
            p_stock = p_stock * 1.010
        elif m == 1:
            p_bench = p_bench * 1.001
            p_stock = p_stock * 1.012
        else:
            p_bench = p_bench * 1.001
            p_stock = p_stock * 1.005

        obs.append(
            PriceObservation(
                timestamp_utc=ts,
                price=round(p_stock, 4),
                bid=round(p_stock - 1.0, 4),
                ask=round(p_stock + 1.0, 4),
                volume=500.0,
            )
        )
        bench.append(PriceObservation(timestamp_utc=ts, price=round(p_bench, 4)))

    engine = EventStudyEngine(
        calendar=cal,
        benchmark_mode=BenchmarkAdjustmentMethod.PRE_EVENT_ROLLING_BETA,
        beta_lookback_obs=20,
        beta_min_samples=10,
    )
    pair = engine.compute_reaction_and_executable_pair(
        event_id="evt_pair_a4_a5",
        instrument_id="NSE:TCS:EQ",
        horizon=OutcomeHorizon.M5,
        event_timestamp_utc=decision_ts,
        decision_timestamp_utc=decision_ts,
        observations=obs,
        pipeline_latency_ms=100.0,
        executable_side=1,
        benchmark_instrument="NSE:NIFTY_FUT:CURRENT",
        benchmark_observations=bench,
        is_synthetic=True,
    )
    lbl_react = pair[OutcomeLabelType.REACTION]
    lbl_exec = pair[OutcomeLabelType.EXECUTABLE]

    assert lbl_react.label_type == OutcomeLabelType.REACTION
    assert lbl_exec.label_type == OutcomeLabelType.EXECUTABLE
    assert lbl_exec.outcome_start_price_ts > lbl_react.decision_timestamp_utc
    assert lbl_exec.entry_quote_type == "EXEC_ASK"
    assert lbl_exec.entry_spread_bps_cost is not None and lbl_exec.entry_spread_bps_cost > 15.0
    assert lbl_exec.simple_return < lbl_react.simple_return

    assert lbl_react.benchmark_adjustment_method == BenchmarkAdjustmentMethod.PRE_EVENT_ROLLING_BETA
    assert lbl_react.estimated_beta == pytest.approx(2.0, abs=0.05)
    assert lbl_react.beta_sample_count is not None and lbl_react.beta_sample_count >= 10
    assert lbl_react.beta_window_end_ts <= lbl_react.outcome_start_price_ts

    # Verify next-session open skipping stray off-hours tick for EXECUTABLE mode
    off_hrs_decision = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)  # 17:30 IST after close
    next_open = datetime(2026, 3, 3, 3, 45, tzinfo=timezone.utc)  # 09:15 IST next day
    ah_obs = [
        PriceObservation(timestamp_utc=datetime(2026, 3, 2, 9, 55, tzinfo=timezone.utc) + timedelta(minutes=k), price=1000.0)
        for k in range(5)
    ] + [
        PriceObservation(timestamp_utc=datetime(2026, 3, 2, 12, 5, tzinfo=timezone.utc), price=1040.0),  # Stray off-hours print
    ] + [
        PriceObservation(
            timestamp_utc=next_open + timedelta(minutes=k),
            price=1050.0 + k,
            bid=1049.5 + k,
            ask=1050.5 + k,
        )
        for k in range(10)
    ]
    exec_ah = EventStudyEngine(calendar=cal, anchor_mode=AnchorMode.POST_DECISION_EXECUTABLE).compute_outcome_label(
        event_id="evt_ah_exec",
        instrument_id="NSE:TCS:EQ",
        horizon=OutcomeHorizon.M5,
        event_timestamp_utc=off_hrs_decision,
        decision_timestamp_utc=off_hrs_decision,
        observations=ah_obs,
        is_synthetic=True,
    )
    assert exec_ah.outcome_start_price_ts == next_open
    assert exec_ah.entry_quote_type == "NEXT_SESSION_OPEN"
