"""Point-in-Time Market Regime & Microstructure Context Builder.

Integrates with Phase 2 `MarketEvent` and `TemporalAlignmentEngine` primitives to construct
a strictly causal (`local_received_at_utc <= decision_timestamp`) market-state representation
(`MarketRegimeContext`) kept completely decoupled from NLP features for Phase 5 ablation studies.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Sequence
from pydantic import BaseModel, Field

from backend.alpha.alignment import (
    MarketSessionStatus,
    TemporalAlignmentEngine,
    get_market_session_status,
)
from backend.alpha.event_study import MarketCalendarConfig
from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.schemas import MarketEvent


class MarketDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    UNAVAILABLE = "UNAVAILABLE"


class VolatilityRegime(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    UNAVAILABLE = "UNAVAILABLE"


class LiquidityRegime(str, Enum):
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    THIN = "THIN"
    CLOSED_OR_HALTED = "CLOSED_OR_HALTED"
    UNAVAILABLE = "UNAVAILABLE"


class MarketRegimeContext(BaseModel):
    """
    Isolated point-in-time market microstructure and regime state constructed
    exclusively from observations available at or before `decision_timestamp`.
    """

    instrument_id: str
    benchmark_instrument_id: Optional[str] = None
    decision_timestamp: datetime
    latest_market_timestamp: Optional[datetime] = None
    pre_event_observation_count: int = 0
    excluded_future_events_count: int = 0

    # Microstructure & Quote State
    mid_price: Optional[float] = None
    last_traded_price: Optional[float] = None
    bid_ask_spread: Optional[float] = None
    bid_ask_spread_bps: Optional[float] = None
    top_of_book_bid_qty: Optional[int] = None
    top_of_book_ask_qty: Optional[int] = None
    top_of_book_total_depth: Optional[int] = None
    l2_total_bid_depth: Optional[int] = None
    l2_total_ask_depth: Optional[int] = None
    depth_imbalance: Optional[float] = Field(None, ge=-1.0, le=1.0)
    microprice: Optional[float] = None
    microprice_offset_bps: Optional[float] = None

    # Pre-Event Dynamics & Volatility
    recent_return_1m: Optional[float] = None
    recent_return_5m: Optional[float] = None
    recent_return_30m: Optional[float] = None
    realized_volatility_bps: Optional[float] = None
    benchmark_recent_return_30m: Optional[float] = None

    # Regime Classifications
    feed_freshness_ms: Optional[float] = Field(None, ge=0.0)
    is_stale: bool = False
    session_state: MarketSessionStatus = MarketSessionStatus.CLOSED
    market_direction: MarketDirection = MarketDirection.UNAVAILABLE
    volatility_regime: VolatilityRegime = VolatilityRegime.UNAVAILABLE
    liquidity_regime: LiquidityRegime = LiquidityRegime.UNAVAILABLE

    data_quality_status: str = "CLEAN"
    schema_version: str = "4.0.0"


class MarketRegimeContextBuilder:
    """
    Constructs `MarketRegimeContext` strictly from market observations arriving
    at or before `decision_timestamp` (`local_received_at_utc <= decision_timestamp`).
    """

    def __init__(
        self,
        stale_threshold_seconds: float = 60.0,
        volatility_window_seconds: float = 1800.0,  # 30 minutes pre-event window
        jurisdiction: MarketJurisdiction = MarketJurisdiction.IN,
        calendar: Optional[MarketCalendarConfig] = None,
    ) -> None:
        self.stale_threshold_seconds = stale_threshold_seconds
        self.volatility_window_seconds = volatility_window_seconds
        self.jurisdiction = jurisdiction
        self.calendar = calendar or MarketCalendarConfig.for_jurisdiction(jurisdiction)

    @staticmethod
    def _extract_reference_price(ev: MarketEvent) -> Optional[float]:
        if ev.mid_price is not None and ev.mid_price > 0:
            return float(ev.mid_price)
        if ev.last_traded_price is not None and ev.last_traded_price > 0:
            return float(ev.last_traded_price)
        return None

    def _compute_return_over_lookback(
        self,
        pre_events: Sequence[MarketEvent],
        decision_ts: datetime,
        latest_price: Optional[float],
        lookback_seconds: float,
    ) -> Optional[float]:
        if latest_price is None or latest_price <= 0:
            return None
        target_ts = decision_ts - timedelta(seconds=lookback_seconds)
        earlier = [e for e in pre_events if e.local_received_at_utc <= target_ts]
        if earlier:
            p0 = self._extract_reference_price(earlier[-1])
        else:
            # If window spans partial lookback with >=2 points, require at least 50% of lookback covered
            first = pre_events[0]
            span = (pre_events[-1].local_received_at_utc - first.local_received_at_utc).total_seconds()
            if span < lookback_seconds * 0.5:
                return None
            p0 = self._extract_reference_price(first)

        if p0 is None or p0 <= 0:
            return None
        return round((latest_price / p0) - 1.0, 6)

    def _resolve_session_state(
        self,
        decision_ts: datetime,
        latest_event: Optional[MarketEvent],
    ) -> MarketSessionStatus:
        if latest_event and latest_event.session_state:
            try:
                return MarketSessionStatus(latest_event.session_state.upper())
            except ValueError:
                pass
        if self.jurisdiction == MarketJurisdiction.IN and not self.calendar.holidays:
            return get_market_session_status(decision_ts)
        return MarketSessionStatus.OPEN if self.calendar.is_in_session(decision_ts) else MarketSessionStatus.CLOSED

    def build_from_alignment_engine(
        self,
        engine: TemporalAlignmentEngine,
        instrument_id: str,
        decision_timestamp: datetime,
        benchmark_instrument_id: Optional[str] = None,
    ) -> MarketRegimeContext:
        """Build point-in-time MarketRegimeContext directly from Phase 2 TemporalAlignmentEngine buffers."""
        events = list(engine._market_history.get(instrument_id, ()))
        bench_events = (
            list(engine._market_history.get(benchmark_instrument_id, ()))
            if benchmark_instrument_id
            else None
        )
        return self.build(
            instrument_id=instrument_id,
            market_events=events,
            decision_timestamp=decision_timestamp,
            benchmark_instrument_id=benchmark_instrument_id,
            benchmark_events=bench_events,
        )

    def build(
        self,
        instrument_id: str,
        market_events: Sequence[MarketEvent],
        decision_timestamp: datetime,
        benchmark_instrument_id: Optional[str] = None,
        benchmark_events: Optional[Sequence[MarketEvent]] = None,
    ) -> MarketRegimeContext:
        """
        Compute causal point-in-time MarketRegimeContext.
        Any MarketEvent with `local_received_at_utc > decision_timestamp` is strictly excluded.
        """
        target_upper = instrument_id.strip().upper()
        matching_inst = [e for e in market_events if e.instrument_id.strip().upper() == target_upper]

        # Strict causal gate: local_received_at_utc <= decision_timestamp
        pre_events = sorted(
            (e for e in matching_inst if e.local_received_at_utc <= decision_timestamp),
            key=lambda e: e.local_received_at_utc,
        )
        excluded_future = sum(1 for e in matching_inst if e.local_received_at_utc > decision_timestamp)

        session_state = self._resolve_session_state(decision_timestamp, pre_events[-1] if pre_events else None)

        if not pre_events:
            return MarketRegimeContext(
                instrument_id=instrument_id,
                benchmark_instrument_id=benchmark_instrument_id,
                decision_timestamp=decision_timestamp,
                excluded_future_events_count=excluded_future,
                session_state=session_state,
                liquidity_regime=(
                    LiquidityRegime.CLOSED_OR_HALTED
                    if session_state in (MarketSessionStatus.CLOSED, MarketSessionStatus.HALTED)
                    else LiquidityRegime.UNAVAILABLE
                ),
                data_quality_status="MISSING_MARKET_DATA",
            )

        latest = pre_events[-1]
        assert latest.local_received_at_utc <= decision_timestamp

        freshness_ms = max(0.0, (decision_timestamp - latest.local_received_at_utc).total_seconds() * 1000.0)
        is_stale = (freshness_ms / 1000.0) > self.stale_threshold_seconds

        mid_p = float(latest.mid_price) if latest.mid_price is not None else None
        ref_p = self._extract_reference_price(latest)
        ltp = float(latest.last_traded_price) if latest.last_traded_price is not None else None

        spread_val = float(latest.spread) if latest.spread is not None else None
        spread_bps: Optional[float] = None
        if spread_val is not None and mid_p and mid_p > 0:
            spread_bps = round((spread_val / mid_p) * 10000.0, 3)

        bid_qty = latest.bid_quantity
        ask_qty = latest.ask_quantity
        tot_depth: Optional[int] = None
        imbalance: Optional[float] = None

        if bid_qty is not None and ask_qty is not None:
            tot_depth = bid_qty + ask_qty
            if tot_depth > 0:
                imbalance = round((bid_qty - ask_qty) / tot_depth, 4)

        l2_bid_depth = sum(lvl.quantity for lvl in latest.depth_bids) if latest.depth_bids else None
        l2_ask_depth = sum(lvl.quantity for lvl in latest.depth_asks) if latest.depth_asks else None
        if imbalance is None and l2_bid_depth is not None and l2_ask_depth is not None:
            l2_tot = l2_bid_depth + l2_ask_depth
            if l2_tot > 0:
                imbalance = round((l2_bid_depth - l2_ask_depth) / l2_tot, 4)

        micro_p = float(latest.microprice) if latest.microprice is not None else None
        micro_offset_bps: Optional[float] = None
        if micro_p is not None and mid_p is not None and mid_p > 0:
            micro_offset_bps = round(((micro_p - mid_p) / mid_p) * 10000.0, 3)

        # Recent Causal Returns
        ret_1m = self._compute_return_over_lookback(pre_events, decision_timestamp, ref_p, 60.0)
        ret_5m = self._compute_return_over_lookback(pre_events, decision_timestamp, ref_p, 300.0)
        ret_30m = self._compute_return_over_lookback(pre_events, decision_timestamp, ref_p, 1800.0)

        # Realized volatility in bps over lookback window strictly <= decision_timestamp
        vol_cutoff = decision_timestamp - timedelta(seconds=self.volatility_window_seconds)
        vol_prices = [
            p for p in (
                self._extract_reference_price(e)
                for e in pre_events
                if e.local_received_at_utc >= vol_cutoff
            )
            if p is not None and p > 0
        ]
        rv_bps: Optional[float] = None
        if len(vol_prices) >= 3:
            logs = [math.log(vol_prices[i] / vol_prices[i - 1]) for i in range(1, len(vol_prices))]
            rv_bps = round(math.sqrt(sum(r * r for r in logs)) * 10000.0, 3)

        # Optional Benchmark pre-event 30m return strictly <= decision_timestamp
        bench_ret_30m: Optional[float] = None
        if benchmark_instrument_id and benchmark_events:
            b_upper = benchmark_instrument_id.strip().upper()
            b_pre = sorted(
                (
                    e for e in benchmark_events
                    if e.instrument_id.strip().upper() == b_upper and e.local_received_at_utc <= decision_timestamp
                ),
                key=lambda e: e.local_received_at_utc,
            )
            if b_pre:
                b_ref = self._extract_reference_price(b_pre[-1])
                bench_ret_30m = self._compute_return_over_lookback(b_pre, decision_timestamp, b_ref, 1800.0)

        # Classify Market Direction
        dir_signal = ret_30m if ret_30m is not None else (ret_5m if ret_5m is not None else ret_1m)
        if dir_signal is None:
            direction = MarketDirection.UNAVAILABLE
        elif dir_signal >= 0.0015:
            direction = MarketDirection.BULLISH
        elif dir_signal <= -0.0015:
            direction = MarketDirection.BEARISH
        else:
            direction = MarketDirection.NEUTRAL

        # Classify Volatility Regime (based on window realized volatility in bps)
        if rv_bps is None:
            vol_regime = VolatilityRegime.UNAVAILABLE
        elif rv_bps < 25.0:
            vol_regime = VolatilityRegime.LOW
        elif rv_bps <= 75.0:
            vol_regime = VolatilityRegime.NORMAL
        else:
            vol_regime = VolatilityRegime.ELEVATED

        # Classify Liquidity Regime
        if session_state in (MarketSessionStatus.CLOSED, MarketSessionStatus.HALTED):
            liq_regime = LiquidityRegime.CLOSED_OR_HALTED
        elif spread_bps is None:
            liq_regime = LiquidityRegime.UNAVAILABLE
        elif is_stale or spread_bps > 20.0 or (tot_depth is not None and tot_depth < 50):
            liq_regime = LiquidityRegime.THIN
        elif spread_bps <= 5.0 and (tot_depth is None or tot_depth >= 500):
            liq_regime = LiquidityRegime.HIGH
        else:
            liq_regime = LiquidityRegime.NORMAL

        flags: List[str] = []
        if session_state == MarketSessionStatus.CLOSED:
            flags.append("OUT_OF_HOURS")
        if is_stale and session_state == MarketSessionStatus.OPEN:
            flags.append("STALE_FEED")
        if spread_bps is None:
            flags.append("QUOTE_MISSING")
        dq_status = "CLEAN" if not flags else "_".join(flags)

        return MarketRegimeContext(
            instrument_id=instrument_id,
            benchmark_instrument_id=benchmark_instrument_id,
            decision_timestamp=decision_timestamp,
            latest_market_timestamp=latest.local_received_at_utc,
            pre_event_observation_count=len(pre_events),
            excluded_future_events_count=excluded_future,
            mid_price=round(mid_p, 4) if mid_p is not None else None,
            last_traded_price=round(ltp, 4) if ltp is not None else None,
            bid_ask_spread=round(spread_val, 4) if spread_val is not None else None,
            bid_ask_spread_bps=spread_bps,
            top_of_book_bid_qty=bid_qty,
            top_of_book_ask_qty=ask_qty,
            top_of_book_total_depth=tot_depth,
            l2_total_bid_depth=l2_bid_depth,
            l2_total_ask_depth=l2_ask_depth,
            depth_imbalance=imbalance,
            microprice=round(micro_p, 4) if micro_p is not None else None,
            microprice_offset_bps=micro_offset_bps,
            recent_return_1m=ret_1m,
            recent_return_5m=ret_5m,
            recent_return_30m=ret_30m,
            realized_volatility_bps=rv_bps,
            benchmark_recent_return_30m=bench_ret_30m,
            feed_freshness_ms=round(freshness_ms, 2),
            is_stale=is_stale,
            session_state=session_state,
            market_direction=direction,
            volatility_regime=vol_regime,
            liquidity_regime=liq_regime,
            data_quality_status=dq_status,
        )
