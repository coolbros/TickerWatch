"""Unit tests for Phase 4 Market Regime Context Builder (Work Package G)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from backend.alpha.alignment import MarketSessionStatus, TemporalAlignmentEngine
from backend.alpha.market_context import (
    LiquidityRegime,
    MarketDirection,
    MarketRegimeContextBuilder,
    VolatilityRegime,
)
from backend.alpha.schemas import DepthLevel, MarketEvent, MarketMessageType


def _make_tick(ts: datetime, bid: float, ask: float, bq: int, aq: int, inst: str = "NSE:RELIANCE:EQ") -> MarketEvent:
    return MarketEvent(
        provider="REPLAY",
        venue="NSE",
        segment="EQ",
        instrument_id=inst,
        message_type=MarketMessageType.BOOK_SNAPSHOT,
        local_received_at_utc=ts,
        local_monotonic_ns=int(ts.timestamp() * 1e9),
        bid_price=Decimal(str(bid)),
        ask_price=Decimal(str(ask)),
        bid_quantity=bq,
        ask_quantity=aq,
        depth_bids=[DepthLevel(price=Decimal(str(bid)), quantity=bq)],
        depth_asks=[DepthLevel(price=Decimal(str(ask)), quantity=aq)],
        last_traded_price=Decimal(str((bid + ask) / 2.0)),
    )


def test_market_context_excludes_future_ticks_and_computes_microstructure():
    """Verify strict as-of filtering (`<= decision_ts`) and full microstructure computation."""
    decision_ts = datetime(2026, 3, 2, 5, 0, tzinfo=timezone.utc)  # 10:30 IST open market
    events = []
    # Pre-event 30m ramp from ~2500 -> ~2510 (bullish +40bps), positive depth imbalance (400 bid vs 100 ask)
    for m in range(30, -1, -1):
        ts = decision_ts - timedelta(minutes=m)
        mid = 2500.0 + (30 - m) * 0.35
        events.append(_make_tick(ts, bid=mid - 0.5, ask=mid + 0.5, bq=400, aq=100))

    # Inject MASSIVE future crash ticks 1 second and 1 minute AFTER decision_ts that must NEVER leak
    events.append(_make_tick(decision_ts + timedelta(seconds=1), bid=2000.0, ask=2020.0, bq=10, aq=5000))
    events.append(_make_tick(decision_ts + timedelta(minutes=1), bid=1900.0, ask=1950.0, bq=5, aq=8000))

    builder = MarketRegimeContextBuilder(stale_threshold_seconds=60.0)
    ctx = builder.build("NSE:RELIANCE:EQ", events, decision_ts)

    assert ctx.excluded_future_events_count == 2
    assert ctx.pre_event_observation_count == 31
    assert ctx.latest_market_timestamp == decision_ts
    assert ctx.feed_freshness_ms == 0.0
    assert ctx.is_stale is False
    assert ctx.session_state == MarketSessionStatus.OPEN

    # Exact pre-event end mid = 2510.5 (never leaked 2010 or 1925!)
    assert ctx.mid_price == pytest.approx(2510.5, abs=0.05)
    assert ctx.bid_ask_spread == pytest.approx(1.0, abs=1e-4)
    assert ctx.depth_imbalance == pytest.approx((400 - 100) / 500, abs=1e-4)
    # Bid qty=400 > ask qty=100 => microprice skews toward ask (> mid_price)
    assert ctx.microprice_offset_bps > 0
    assert ctx.recent_return_30m > 0.003
    assert ctx.market_direction == MarketDirection.BULLISH
    assert ctx.volatility_regime == VolatilityRegime.LOW
    assert ctx.liquidity_regime == LiquidityRegime.HIGH
    assert ctx.data_quality_status == "CLEAN"


def test_market_context_integrates_with_phase2_alignment_engine_and_handles_missing():
    """Verify integration with TemporalAlignmentEngine ring buffer and out-of-hours / missing states."""
    engine = TemporalAlignmentEngine(lookback_window_seconds=2400.0)
    t_open = datetime(2026, 3, 2, 4, 30, tzinfo=timezone.utc)
    for m in range(5):
        ts = t_open + timedelta(minutes=m)
        # Wide spread + low depth -> THIN liquidity
        engine.add_market_event(_make_tick(ts, bid=995.0, ask=1005.0, bq=10, aq=15, inst="NSE:LT:EQ"))

    builder = MarketRegimeContextBuilder()
    ctx = builder.build_from_alignment_engine(
        engine=engine,
        instrument_id="NSE:LT:EQ",
        decision_timestamp=t_open + timedelta(minutes=10),  # 6 mins after last tick -> stale!
    )
    assert ctx.is_stale is True
    assert ctx.liquidity_regime == LiquidityRegime.THIN
    assert "STALE_FEED" in ctx.data_quality_status

    # Empty instrument -> explicit MISSING_MARKET_DATA without crash
    ctx_missing = builder.build_from_alignment_engine(
        engine=engine,
        instrument_id="NSE:UNKNOWN:EQ",
        decision_timestamp=datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc),  # 17:30 IST CLOSED
    )
    assert ctx_missing.data_quality_status == "MISSING_MARKET_DATA"
    assert ctx_missing.session_state == MarketSessionStatus.CLOSED
    assert ctx_missing.liquidity_regime == LiquidityRegime.CLOSED_OR_HALTED
