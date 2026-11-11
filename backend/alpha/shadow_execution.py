"""Shadow Execution Simulator (Phase 6 WP-G).

Strictly paper-only execution simulator (zero broker order API connections).
Models realistic microstructure execution decoupled cleanly from predictive models so
alternative latency, order-type, limit-queue fill, liquidity, statutory cost, session,
and rollover assumptions can be swept against identical shadow predictions.

Strictly preserves three separate return labels:
1. `gross_theoretical_return` (pre-decision mid/reference reaction)
2. `estimated_executable_return` (post-latency quote/depth/OHLCV-conservative fill return pre-fees)
3. `net_return_after_costs` (executable return less brokerage, SEBI/exchange, STT, stamp, GST, impact)
"""

from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field

from backend.alpha.event_study import MarketCalendarConfig
from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.live_instruments import LiveInstrumentStatus, LiveInstrumentUniverse
from backend.alpha.predictive_targets import CostAssumptionSpec
from backend.alpha.schemas import MarketEvent
from backend.alpha.shadow_ledger import ShadowLedgerEntry

IST_ZONE = ZoneInfo("Asia/Kolkata")


class ShadowOrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class FillQualitySource(str, Enum):
    L2_DEPTH_BOOK = "L2_DEPTH_BOOK"
    L1_BBO_QUOTE = "L1_BBO_QUOTE"
    OHLCV_CONSERVATIVE_FALLBACK = "OHLCV_CONSERVATIVE_FALLBACK"
    UNFILLED_OR_RESTRICTED = "UNFILLED_OR_RESTRICTED"


class SyntheticBarOHLCV(BaseModel):
    """Conservative fallback OHLCV bar schema when L1/L2 tick quotes are unavailable."""

    timestamp_utc: datetime
    open: float = Field(..., gt=0.0)
    high: float = Field(..., gt=0.0)
    low: float = Field(..., gt=0.0)
    close: float = Field(..., gt=0.0)
    volume: float = Field(default=0.0, ge=0.0)


class ShadowExecutionConfig(BaseModel):
    """Pluggable execution regime allowing friction sensitivity sweeps without altering models."""

    scenario_name: str = "IN_CONSERVATIVE_MARKET"
    order_type: ShadowOrderType = ShadowOrderType.MARKET
    decision_latency_ms: float = Field(default=25.0, ge=0.0)
    order_submission_latency_ms: float = Field(default=35.0, ge=0.0)
    intended_quantity: int = Field(default=75, gt=0)
    max_book_participation: float = Field(default=0.35, gt=0.0, le=1.0)
    limit_offset_bps: float = Field(default=1.0, ge=0.0)
    # Anti-optimistic limit rule: touch at limit != 100% fill; require trade-through or probabilistic queue factor
    require_trade_through_for_full_limit_fill: bool = True
    touch_only_fill_fraction: float = Field(default=0.25, ge=0.0, le=1.0)
    ohlcv_fallback_half_spread_bps: float = Field(default=4.5, ge=0.0)
    ohlcv_fallback_impact_bps: float = Field(default=3.0, ge=0.0)
    exit_before_close_minutes: int = Field(default=5, ge=0)
    block_pre_rollover_entries: bool = True
    cost_spec: CostAssumptionSpec = Field(default_factory=CostAssumptionSpec.indian_nifty_fut_conservative)


class SimulatedShadowTrade(BaseModel):
    """Complete paper trade audit record maintaining gross, executable, and net separation."""

    prediction_id: str
    event_id: str
    instrument_id: str
    scenario_name: str
    order_type: ShadowOrderType
    direction: int  # +1 LONG, -1 SHORT, 0 NO_TRADE

    filled: bool
    fill_fraction: float = Field(..., ge=0.0, le=1.0)
    filled_quantity: int = 0
    unfilled_reason: Optional[str] = None
    fill_quality_source: FillQualitySource
    fill_uncertainty_note: str = ""

    decision_timestamp_utc: datetime
    effective_order_arrival_utc: datetime
    entry_fill_timestamp_utc: Optional[datetime] = None
    exit_fill_timestamp_utc: Optional[datetime] = None

    pre_decision_ref_price: Optional[float] = None
    entry_fill_price: Optional[float] = None
    exit_fill_price: Optional[float] = None

    realized_entry_spread_bps: Optional[float] = None
    estimated_market_impact_bps: float = 0.0
    round_trip_statutory_cost_bps: float = 0.0

    # Strict separation of the 3 return layers
    gross_theoretical_return: Optional[float] = None
    estimated_executable_return: Optional[float] = None
    net_return_after_costs: Optional[float] = None

    rollover_adjusted_exit: bool = False
    session_restricted_exit: bool = False


class ShadowExecutionSimulator:
    """
    Deterministic paper execution simulator consuming immutable `ShadowLedgerEntry`
    and historical/live `MarketEvent` (with conservative OHLCV fallback).
    NEVER communicates with any real broker order-placement API.
    """

    def __init__(
        self,
        calendar: Optional[MarketCalendarConfig] = None,
        universe: Optional[LiveInstrumentUniverse] = None,
    ) -> None:
        self.calendar = calendar or MarketCalendarConfig.for_jurisdiction(MarketJurisdiction.IN)
        self.universe = universe or LiveInstrumentUniverse()

    @staticmethod
    def _mid_price(m: MarketEvent) -> Optional[float]:
        if m.bid_price is not None and m.ask_price is not None and m.ask_price > 0 and m.bid_price > 0:
            return float((m.bid_price + m.ask_price) / 2)
        return float(m.last_traded_price) if m.last_traded_price is not None else None

    @staticmethod
    def _sweep_l2_depth_price(
        levels: Sequence[Any],
        qty_target: int,
        fallback_bbo_price: float,
        is_buy: bool,
    ) -> tuple[float, int, float]:
        """Walk L2 depth levels to compute VWAP fill, filled qty, and additional depth impact in bps."""
        if not levels or qty_target <= 0:
            return fallback_bbo_price, qty_target, 0.0
        rem = qty_target
        filled = 0
        notional = 0.0
        top_p = float(levels[0].price)
        for lvl in levels:
            avail = int(lvl.quantity)
            if avail <= 0:
                continue
            take = min(rem, avail)
            notional += take * float(lvl.price)
            filled += take
            rem -= take
            if rem == 0:
                break
        if filled == 0:
            return fallback_bbo_price, 0, 0.0
        vwap = notional / float(filled)
        impact_bps = (
            max(0.0, ((vwap - top_p) / top_p) * 10000.0)
            if is_buy
            else max(0.0, ((top_p - vwap) / top_p) * 10000.0)
        )
        return vwap, filled, impact_bps

    def _compute_cost_bps(
        self,
        cfg: ShadowExecutionConfig,
        spread_bps: Optional[float],
        impact_bps: float,
    ) -> float:
        spec = cfg.cost_spec
        return round(
            spec.brokerage_bps_round_trip
            + spec.exchange_sebi_clearing_bps_round_trip
            + spec.stt_stamp_gst_bps_round_trip
            + (spread_bps if spread_bps is not None else 0.0)
            + impact_bps,
            4,
        )

    def simulate_trade(
        self,
        entry: ShadowLedgerEntry,
        market_events: Sequence[MarketEvent],
        scheduled_exit_utc: datetime,
        config: Optional[ShadowExecutionConfig] = None,
        fallback_ohlcv_bars: Optional[Sequence[SyntheticBarOHLCV]] = None,
        allow_diagnostic_unvalidated: bool = False,
    ) -> SimulatedShadowTrade:
        """Simulate realistic execution for a single shadow prediction under `config`."""
        cfg = config or ShadowExecutionConfig()
        direction = int(entry.signal_direction)
        dec_ts = entry.decision_timestamp_utc
        total_lat_ms = cfg.decision_latency_ms + cfg.order_submission_latency_ms
        arrival_ts = dec_ts + timedelta(milliseconds=total_lat_ms)

        if entry.no_trade_status or direction == 0:
            return SimulatedShadowTrade(
                prediction_id=entry.prediction_id,
                event_id=entry.event_id,
                instrument_id=entry.instrument_id,
                scenario_name=cfg.scenario_name,
                order_type=cfg.order_type,
                direction=0,
                filled=False,
                fill_fraction=0.0,
                unfilled_reason="SIGNAL_NO_TRADE_ABSTENTION",
                fill_quality_source=FillQualitySource.UNFILLED_OR_RESTRICTED,
                decision_timestamp_utc=dec_ts,
                effective_order_arrival_utc=arrival_ts,
            )

        if entry.model_validation_status.value != "MODEL_ELIGIBLE" and not allow_diagnostic_unvalidated:
            return SimulatedShadowTrade(
                prediction_id=entry.prediction_id,
                event_id=entry.event_id,
                instrument_id=entry.instrument_id,
                scenario_name=cfg.scenario_name,
                order_type=cfg.order_type,
                direction=direction,
                filled=False,
                fill_fraction=0.0,
                unfilled_reason=f"MODEL_NOT_ELIGIBLE:{entry.model_validation_status.value}",
                fill_quality_source=FillQualitySource.UNFILLED_OR_RESTRICTED,
                decision_timestamp_utc=dec_ts,
                effective_order_arrival_utc=arrival_ts,
            )

        # Check rollover / halt restriction on futures contracts
        rollover_exit = False
        if cfg.block_pre_rollover_entries and "_FUT:" in entry.instrument_id:
            sym = entry.instrument_id.split(":")[1].replace("_FUT", "")
            from backend.alpha.instruments import Segment

            seg = Segment.FUTIDX if sym in ("NIFTY", "BANKNIFTY") else Segment.FUTSTK
            try:
                c_state = self.universe.resolve_active_contract(sym, seg, as_of_date=arrival_ts.astimezone(IST_ZONE).date())
                if c_state.status in (LiveInstrumentStatus.HALTED, LiveInstrumentStatus.SUSPENDED):
                    return SimulatedShadowTrade(
                        prediction_id=entry.prediction_id,
                        event_id=entry.event_id,
                        instrument_id=entry.instrument_id,
                        scenario_name=cfg.scenario_name,
                        order_type=cfg.order_type,
                        direction=direction,
                        filled=False,
                        fill_fraction=0.0,
                        unfilled_reason=f"CONTRACT_{c_state.status.value}",
                        fill_quality_source=FillQualitySource.UNFILLED_OR_RESTRICTED,
                        decision_timestamp_utc=dec_ts,
                        effective_order_arrival_utc=arrival_ts,
                    )
                if c_state.status == LiveInstrumentStatus.PRE_ROLLOVER:
                    rollover_exit = True
            except ValueError:
                pass

        # Restrict trading window strictly inside exchange session hours
        arrival_loc = arrival_ts.astimezone(IST_ZONE)
        session_blocked = not self.calendar.is_in_session(arrival_ts)
        if session_blocked:
            # Move arrival forward cleanly to next active session open
            next_day = arrival_loc.date() if (
                self.calendar.is_trading_day(arrival_loc.date()) and arrival_loc.time() < self.calendar.session_open_local
            ) else self.calendar.next_trading_day(arrival_loc.date(), 1)
            arrival_ts = datetime.combine(next_day, self.calendar.session_open_local, tzinfo=IST_ZONE).astimezone(timezone.utc) + timedelta(milliseconds=total_lat_ms)

        # Cap exit before market close margin
        exit_loc = scheduled_exit_utc.astimezone(IST_ZONE)
        close_cap = datetime.combine(exit_loc.date(), self.calendar.session_close_local, tzinfo=IST_ZONE) - timedelta(
            minutes=cfg.exit_before_close_minutes
        )
        session_restricted_exit = False
        effective_exit_ts = scheduled_exit_utc
        if effective_exit_ts > close_cap.astimezone(timezone.utc) and effective_exit_ts.date() == arrival_ts.astimezone(IST_ZONE).date():
            effective_exit_ts = close_cap.astimezone(timezone.utc)
            session_restricted_exit = True

        inst_events = sorted(
            [m for m in market_events if m.instrument_id == entry.instrument_id],
            key=lambda m: m.local_received_at_utc,
        )
        pre_events = [m for m in inst_events if m.local_received_at_utc <= dec_ts]
        post_events = [m for m in inst_events if arrival_ts <= m.local_received_at_utc <= effective_exit_ts]

        pre_ref_p = self._mid_price(pre_events[-1]) if pre_events else None

        # Route A: Tick/Quote/L2 Path
        if post_events:
            entry_tick = post_events[0]
            exit_tick = post_events[-1]
            is_buy = direction > 0

            # Extract top-of-book & depth availability
            has_l2 = bool(entry_tick.depth_asks if is_buy else entry_tick.depth_bids)
            has_bbo = entry_tick.bid_price is not None and entry_tick.ask_price is not None

            if cfg.order_type == ShadowOrderType.MARKET:
                if has_l2:
                    levels = entry_tick.depth_asks if is_buy else entry_tick.depth_bids
                    base_top = float(levels[0].price)
                    cap_qty = max(1, int(sum(int(l.quantity) for l in levels) * cfg.max_book_participation))
                    req_qty = min(cfg.intended_quantity, cap_qty)
                    entry_p, filled_qty, imp_bps = self._sweep_l2_depth_price(levels, req_qty, base_top, is_buy)
                    src = FillQualitySource.L2_DEPTH_BOOK
                    uncert_note = "L2 book sweep capped by max_book_participation."
                elif has_bbo:
                    entry_p = float(entry_tick.ask_price if is_buy else entry_tick.bid_price)
                    avail_qty = int((entry_tick.ask_quantity if is_buy else entry_tick.bid_quantity) or cfg.intended_quantity)
                    cap_qty = max(1, int(avail_qty * cfg.max_book_participation))
                    filled_qty = min(cfg.intended_quantity, cap_qty)
                    imp_bps = 1.5
                    src = FillQualitySource.L1_BBO_QUOTE
                    uncert_note = "L1 BBO fill capped by top-of-book liquidity participation."
                else:
                    ltp = float(entry_tick.last_traded_price or 0.0)
                    if ltp <= 0.0:
                        return SimulatedShadowTrade(
                            prediction_id=entry.prediction_id, event_id=entry.event_id,
                            instrument_id=entry.instrument_id, scenario_name=cfg.scenario_name,
                            order_type=cfg.order_type, direction=direction, filled=False,
                            fill_fraction=0.0, unfilled_reason="NO_VALID_QUOTE_OR_LTP",
                            fill_quality_source=FillQualitySource.UNFILLED_OR_RESTRICTED,
                            decision_timestamp_utc=dec_ts, effective_order_arrival_utc=arrival_ts,
                        )
                    slip = cfg.ohlcv_fallback_half_spread_bps / 10000.0
                    entry_p = ltp * (1.0 + slip if is_buy else 1.0 - slip)
                    filled_qty = cfg.intended_quantity
                    imp_bps = cfg.ohlcv_fallback_impact_bps
                    src = FillQualitySource.OHLCV_CONSERVATIVE_FALLBACK
                    uncert_note = "Tick lacked BBO; conservative half-spread + impact applied."
            else:
                # Documented non-optimistic LIMIT order model: NEVER assume full fill merely on touch!
                mid_arrival = self._mid_price(entry_tick) or float(entry_tick.last_traded_price or 0.0)
                if mid_arrival <= 0.0:
                    return SimulatedShadowTrade(
                        prediction_id=entry.prediction_id, event_id=entry.event_id,
                        instrument_id=entry.instrument_id, scenario_name=cfg.scenario_name,
                        order_type=cfg.order_type, direction=direction, filled=False,
                        fill_fraction=0.0, unfilled_reason="LIMIT_NO_VALID_PRICE",
                        fill_quality_source=FillQualitySource.UNFILLED_OR_RESTRICTED,
                        decision_timestamp_utc=dec_ts, effective_order_arrival_utc=arrival_ts,
                    )
                off = cfg.limit_offset_bps / 10000.0
                limit_p = mid_arrival * (1.0 - off if is_buy else 1.0 + off)
                future_ticks = post_events[1:] if len(post_events) > 1 else post_events
                prices_seen = [p for m in future_ticks if (p := (float(m.last_traded_price) if m.last_traded_price else self._mid_price(m))) is not None]

                traded_through = (
                    any(p < limit_p * (1.0 - 0.0001) for p in prices_seen)
                    if is_buy
                    else any(p > limit_p * (1.0 + 0.0001) for p in prices_seen)
                )
                touched_only = (
                    any(p <= limit_p for p in prices_seen) if is_buy else any(p >= limit_p for p in prices_seen)
                )

                if traded_through:
                    entry_p = limit_p
                    filled_qty = cfg.intended_quantity
                    imp_bps = 0.0
                    src = FillQualitySource.L1_BBO_QUOTE if has_bbo else FillQualitySource.OHLCV_CONSERVATIVE_FALLBACK
                    uncert_note = "Limit traded strictly through price; full queue fill modeled."
                elif touched_only and not cfg.require_trade_through_for_full_limit_fill:
                    entry_p = limit_p
                    filled_qty = max(1, int(round(cfg.intended_quantity * cfg.touch_only_fill_fraction)))
                    imp_bps = 0.0
                    src = FillQualitySource.L1_BBO_QUOTE
                    uncert_note = f"Touch-only partial fill ({cfg.touch_only_fill_fraction:.0%}) reflecting uncertain queue priority."
                elif touched_only:
                    return SimulatedShadowTrade(
                        prediction_id=entry.prediction_id, event_id=entry.event_id,
                        instrument_id=entry.instrument_id, scenario_name=cfg.scenario_name,
                        order_type=cfg.order_type, direction=direction, filled=False,
                        fill_fraction=0.0, unfilled_reason="LIMIT_TOUCHED_ONLY_NO_TRADE_THROUGH",
                        fill_quality_source=FillQualitySource.UNFILLED_OR_RESTRICTED,
                        fill_uncertainty_note="Price touched limit without trade-through; strict queue rule leaves order unfilled.",
                        decision_timestamp_utc=dec_ts, effective_order_arrival_utc=arrival_ts,
                    )
                else:
                    return SimulatedShadowTrade(
                        prediction_id=entry.prediction_id, event_id=entry.event_id,
                        instrument_id=entry.instrument_id, scenario_name=cfg.scenario_name,
                        order_type=cfg.order_type, direction=direction, filled=False,
                        fill_fraction=0.0, unfilled_reason="LIMIT_PRICE_NEVER_REACHED",
                        fill_quality_source=FillQualitySource.UNFILLED_OR_RESTRICTED,
                        decision_timestamp_utc=dec_ts, effective_order_arrival_utc=arrival_ts,
                    )

            # Exit fill on opposing side of book or conservative LTP
            if exit_tick.bid_price is not None and exit_tick.ask_price is not None:
                exit_p = float(exit_tick.bid_price if is_buy else exit_tick.ask_price)
                spread_bps = (
                    float((entry_tick.ask_price - entry_tick.bid_price) / entry_tick.bid_price) * 10000.0
                    if (entry_tick.bid_price and entry_tick.ask_price and entry_tick.bid_price > 0)
                    else None
                )
            else:
                exit_mid = float(exit_tick.last_traded_price or entry_p)
                slip_exit = cfg.ohlcv_fallback_half_spread_bps / 10000.0
                exit_p = exit_mid * (1.0 - slip_exit if is_buy else 1.0 + slip_exit)
                spread_bps = cfg.ohlcv_fallback_half_spread_bps * 2.0

            fill_frac = min(1.0, filled_qty / float(cfg.intended_quantity))
            exec_ret = direction * ((exit_p - entry_p) / entry_p)
            gross_theo = direction * (((self._mid_price(exit_tick) or exit_p) - pre_ref_p) / pre_ref_p) if pre_ref_p else exec_ret

            cost_bps = self._compute_cost_bps(cfg, spread_bps=0.0 if has_bbo else spread_bps, impact_bps=imp_bps)
            net_ret = exec_ret - (cost_bps / 10000.0)

            return SimulatedShadowTrade(
                prediction_id=entry.prediction_id,
                event_id=entry.event_id,
                instrument_id=entry.instrument_id,
                scenario_name=cfg.scenario_name,
                order_type=cfg.order_type,
                direction=direction,
                filled=True,
                fill_fraction=round(fill_frac, 4),
                filled_quantity=filled_qty,
                fill_quality_source=src,
                fill_uncertainty_note=uncert_note,
                decision_timestamp_utc=dec_ts,
                effective_order_arrival_utc=arrival_ts,
                entry_fill_timestamp_utc=entry_tick.local_received_at_utc,
                exit_fill_timestamp_utc=exit_tick.local_received_at_utc,
                pre_decision_ref_price=pre_ref_p,
                entry_fill_price=round(entry_p, 6),
                exit_fill_price=round(exit_p, 6),
                realized_entry_spread_bps=round(spread_bps, 4) if spread_bps is not None else None,
                estimated_market_impact_bps=round(imp_bps, 4),
                round_trip_statutory_cost_bps=cost_bps,
                gross_theoretical_return=round(gross_theo, 8),
                estimated_executable_return=round(exec_ret, 8),
                net_return_after_costs=round(net_ret, 8),
                rollover_adjusted_exit=rollover_exit,
                session_restricted_exit=session_restricted_exit,
            )

        # Route B: Conservative OHLCV fallback when only bars exist
        if fallback_ohlcv_bars:
            bars = sorted(fallback_ohlcv_bars, key=lambda b: b.timestamp_utc)
            post_bars = [b for b in bars if arrival_ts <= b.timestamp_utc <= effective_exit_ts]
            if post_bars:
                eb = post_bars[0]
                xb = post_bars[-1]
                is_buy = direction > 0
                hs = cfg.ohlcv_fallback_half_spread_bps / 10000.0
                # Conservative bar penalty: enter adverse side of open/close midpoint
                entry_p = eb.open * (1.0 + hs if is_buy else 1.0 - hs)
                exit_p = xb.close * (1.0 - hs if is_buy else 1.0 + hs)
                gross_theo = direction * ((xb.close - eb.open) / eb.open)
                exec_ret = direction * ((exit_p - entry_p) / entry_p)
                cost_bps = self._compute_cost_bps(cfg, spread_bps=0.0, impact_bps=cfg.ohlcv_fallback_impact_bps)
                net_ret = exec_ret - (cost_bps / 10000.0)
                return SimulatedShadowTrade(
                    prediction_id=entry.prediction_id,
                    event_id=entry.event_id,
                    instrument_id=entry.instrument_id,
                    scenario_name=cfg.scenario_name,
                    order_type=cfg.order_type,
                    direction=direction,
                    filled=True,
                    fill_fraction=1.0,
                    filled_quantity=cfg.intended_quantity,
                    fill_quality_source=FillQualitySource.OHLCV_CONSERVATIVE_FALLBACK,
                    fill_uncertainty_note="Only OHLCV bars available; conservative adverse half-spread + impact applied.",
                    decision_timestamp_utc=dec_ts,
                    effective_order_arrival_utc=arrival_ts,
                    entry_fill_timestamp_utc=eb.timestamp_utc,
                    exit_fill_timestamp_utc=xb.timestamp_utc,
                    pre_decision_ref_price=eb.open,
                    entry_fill_price=round(entry_p, 6),
                    exit_fill_price=round(exit_p, 6),
                    realized_entry_spread_bps=round(cfg.ohlcv_fallback_half_spread_bps * 2.0, 4),
                    estimated_market_impact_bps=cfg.ohlcv_fallback_impact_bps,
                    round_trip_statutory_cost_bps=cost_bps,
                    gross_theoretical_return=round(gross_theo, 8),
                    estimated_executable_return=round(exec_ret, 8),
                    net_return_after_costs=round(net_ret, 8),
                    rollover_adjusted_exit=rollover_exit,
                    session_restricted_exit=session_restricted_exit,
                )

        return SimulatedShadowTrade(
            prediction_id=entry.prediction_id,
            event_id=entry.event_id,
            instrument_id=entry.instrument_id,
            scenario_name=cfg.scenario_name,
            order_type=cfg.order_type,
            direction=direction,
            filled=False,
            fill_fraction=0.0,
            unfilled_reason="NO_POST_DECISION_MARKET_DATA",
            fill_quality_source=FillQualitySource.UNFILLED_OR_RESTRICTED,
            decision_timestamp_utc=dec_ts,
            effective_order_arrival_utc=arrival_ts,
        )
