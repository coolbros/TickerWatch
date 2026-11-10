"""Causal Historical Market Response & Event-Study Engine.

Computes verified multi-horizon post-event outcome labels (`1m`, `5m`, `30m`, `1h`, `1d`, `5d`)
strictly respecting:
1. Exchange trading calendars, session boundaries, overnight gaps, and holidays (NSE/BSE, US).
2. Source observation resolution (prevents hallucinating intraday outcomes from daily bars).
3. Trading halts, missing observations, stale quotes, and corporate action multipliers.
4. Futures contract expiry and explicit rollover adjustment policies (prohibits silent stitching).
5. Primary equity/single-stock-future vs contextual benchmark attribution.
6. Point-in-time outcome maturity (`outcome_end_price_ts <= evaluation_decision_ts`).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
import math
from typing import Dict, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field

from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.instruments import RelevanceTier
from backend.alpha.nlp_pipeline import FinancialEventRepresentation
from backend.alpha.outcomes import (
    EventOutcomeLabel,
    OutcomeDataQualityStatus,
    OutcomeHorizon,
    PriceObservation,
)


class AnchorMode(str, Enum):
    """Defines how the outcome start reference price is anchored relative to decision timestamp."""

    PRE_EVENT_AS_OF = "PRE_EVENT_AS_OF"
    POST_DECISION_EXECUTABLE = "POST_DECISION_EXECUTABLE"


class FuturesRolloverMethod(str, Enum):
    """Documented futures contract rollover adjustment methodology."""

    REJECT_ACROSS_EXPIRY = "REJECT_ACROSS_EXPIRY"
    ADDITIVE_SPREAD_BACK_ADJUST = "ADDITIVE_SPREAD_BACK_ADJUST"
    RATIO_MULTIPLICATIVE = "RATIO_MULTIPLICATIVE"


class FuturesRolloverSpec(BaseModel):
    """Explicit documented spread or multiplier between expiring and next futures contract."""

    from_contract_id: str
    to_contract_id: str
    method: FuturesRolloverMethod = FuturesRolloverMethod.ADDITIVE_SPREAD_BACK_ADJUST
    price_spread_to_minus_from: Optional[float] = Field(
        None,
        description="P(new_contract) - P(old_contract) at rollover point for additive adjustment",
    )
    ratio_to_over_from: Optional[float] = Field(
        None,
        description="P(new_contract) / P(old_contract) at rollover point for multiplicative adjustment",
    )


class MarketCalendarConfig(BaseModel):
    """Exchange trading hours and holiday schedule."""

    jurisdiction: MarketJurisdiction
    timezone_name: str
    session_open_local: time
    session_close_local: time
    holidays: Set[date] = Field(default_factory=set)
    is_24x7: bool = False

    @classmethod
    def for_jurisdiction(
        cls,
        jurisdiction: MarketJurisdiction,
        holidays: Optional[Set[date]] = None,
    ) -> "MarketCalendarConfig":
        hset = set(holidays or ())
        if jurisdiction == MarketJurisdiction.IN:
            return cls(
                jurisdiction=MarketJurisdiction.IN,
                timezone_name="Asia/Kolkata",
                session_open_local=time(9, 15),
                session_close_local=time(15, 30),
                holidays=hset,
            )
        if jurisdiction == MarketJurisdiction.US:
            return cls(
                jurisdiction=MarketJurisdiction.US,
                timezone_name="America/New_York",
                session_open_local=time(9, 30),
                session_close_local=time(16, 0),
                holidays=hset,
            )
        return cls(
            jurisdiction=MarketJurisdiction.GLOBAL,
            timezone_name="UTC",
            session_open_local=time(0, 0),
            session_close_local=time(23, 59, 59),
            holidays=hset,
            is_24x7=True,
        )

    def _tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    def is_trading_day(self, d: date) -> bool:
        if self.is_24x7:
            return True
        return d.weekday() < 5 and d not in self.holidays

    def next_trading_day(self, d: date, n: int = 1) -> date:
        cur = d
        steps = 0
        while steps < n:
            cur = cur + timedelta(days=1)
            if self.is_trading_day(cur):
                steps += 1
        return cur

    def is_in_session(self, dt_utc: datetime) -> bool:
        if self.is_24x7:
            return True
        dt_loc = dt_utc.astimezone(self._tz())
        if not self.is_trading_day(dt_loc.date()):
            return False
        t_loc = dt_loc.time()
        return self.session_open_local <= t_loc <= self.session_close_local

    def _align_to_session_start(self, dt_loc: datetime) -> datetime:
        """Advance dt_loc to the nearest active or upcoming session open timestamp."""
        d = dt_loc.date()
        t = dt_loc.time()
        tz = self._tz()

        if self.is_trading_day(d):
            if t < self.session_open_local:
                return datetime.combine(d, self.session_open_local, tzinfo=tz)
            if t < self.session_close_local:
                return dt_loc
            # At or after today's close -> next trading day open
            nd = self.next_trading_day(d, 1)
            return datetime.combine(nd, self.session_open_local, tzinfo=tz)

        nd = self.next_trading_day(d, 1)
        return datetime.combine(nd, self.session_open_local, tzinfo=tz)

    def advance_trading_minutes(self, start_utc: datetime, minutes: int) -> Tuple[datetime, bool]:
        """
        Advance strictly through active trading-session minutes.
        Returns (target_utc, crossed_session_boundary).
        """
        if self.is_24x7:
            return start_utc + timedelta(minutes=minutes), False

        tz = self._tz()
        start_loc = start_utc.astimezone(tz)
        initial_session_day = start_loc.date() if self.is_in_session(start_utc) else None

        cur = self._align_to_session_start(start_loc)
        crossed = (initial_session_day is None) or (cur.date() != initial_session_day)
        remaining = float(minutes)

        while remaining > 1e-6:
            session_end = datetime.combine(cur.date(), self.session_close_local, tzinfo=tz)
            avail_min = max(0.0, (session_end - cur).total_seconds() / 60.0)
            if remaining <= avail_min + 1e-9:
                cur = cur + timedelta(minutes=remaining)
                remaining = 0.0
            else:
                remaining -= avail_min
                nd = self.next_trading_day(cur.date(), 1)
                cur = datetime.combine(nd, self.session_open_local, tzinfo=tz)
                crossed = True

        return cur.astimezone(timezone.utc), crossed

    def advance_trading_days(self, start_utc: datetime, trading_days: int) -> Tuple[datetime, bool]:
        """
        Compute target timestamp `trading_days` valid exchange sessions forward.
        - If start is within active trading hours: moves N trading days forward to same time of day.
        - If start is pre-open on a trading day: day 1 ends at today's close; day N at (N-1)th next close.
        - If start is post-close or non-trading day: day 1 ends at next trading day's close.
        """
        if self.is_24x7:
            return start_utc + timedelta(days=trading_days), False

        tz = self._tz()
        loc = start_utc.astimezone(tz)
        d = loc.date()
        t = loc.time()

        if self.is_trading_day(d) and self.session_open_local <= t <= self.session_close_local:
            target_d = self.next_trading_day(d, trading_days)
            target_loc = datetime.combine(target_d, t, tzinfo=tz)
            return target_loc.astimezone(timezone.utc), True

        if self.is_trading_day(d) and t < self.session_open_local:
            # First trading session completes today at close
            target_d = d if trading_days == 1 else self.next_trading_day(d, trading_days - 1)
            target_loc = datetime.combine(target_d, self.session_close_local, tzinfo=tz)
            return target_loc.astimezone(timezone.utc), True

        # After close or holiday/weekend
        target_d = self.next_trading_day(d, trading_days)
        target_loc = datetime.combine(target_d, self.session_close_local, tzinfo=tz)
        return target_loc.astimezone(timezone.utc), True

    def count_trading_days_between(self, ts_a: datetime, ts_b: datetime) -> int:
        tz = self._tz()
        da = min(ts_a, ts_b).astimezone(tz).date()
        db = max(ts_a, ts_b).astimezone(tz).date()
        if da == db:
            return 0
        count = 0
        cur = da + timedelta(days=1)
        while cur <= db:
            if self.is_trading_day(cur):
                count += 1
            cur += timedelta(days=1)
        return count


def select_study_instruments(
    rep: FinancialEventRepresentation,
    jurisdiction: MarketJurisdiction = MarketJurisdiction.IN,
) -> Tuple[Optional[str], List[str], Optional[str]]:
    """
    Separate direct primary corporate instruments from contextual/benchmark indices.

    Rule:
    - For individual corporate events (Earnings, Contracts, Guidance, Regulatory): select directly
      affected cash equity or single-stock future as primary; treat NIFTY_FUT / SPY as contextual benchmark.
    - For MACRO or systemic announcements: index future/benchmark may act as primary instrument.

    Returns:
        (primary_instrument_id, contextual_instrument_ids, default_benchmark_id)
    """
    default_bench = "NSE:NIFTY_FUT:CURRENT" if jurisdiction == MarketJurisdiction.IN else "US:SPY:EQ"
    is_macro = rep.classification.primary_family.value in ("MACRO", "MACROECONOMIC") or (
        rep.primary_entity.entity_id in ("RBI", "FINMIN", "INDIA_MACRO", "GOI", "FED", "SEC")
    )

    direct_equities: List[str] = []
    direct_stock_futs: List[str] = []
    contextual_insts: List[str] = []

    for m in rep.all_instrument_mappings:
        inst_id = m.instrument_id
        is_index = ("NIFTY_FUT" in inst_id or "BANKNIFTY" in inst_id or ":SPY:" in inst_id or ":QQQ:" in inst_id)
        if m.relevance == RelevanceTier.DIRECT and not is_index:
            if "_FUT" in inst_id:
                direct_stock_futs.append(inst_id)
            else:
                direct_equities.append(inst_id)
        elif m.relevance == RelevanceTier.DIRECT and is_macro and is_index:
            direct_equities.append(inst_id)
        else:
            if inst_id not in contextual_insts:
                contextual_insts.append(inst_id)

    if direct_equities:
        primary = direct_equities[0]
        for extra in direct_equities[1:] + direct_stock_futs:
            if extra not in contextual_insts:
                contextual_insts.append(extra)
    elif direct_stock_futs:
        primary = direct_stock_futs[0]
    elif is_macro:
        primary = default_bench
        if primary in contextual_insts:
            contextual_insts.remove(primary)
    else:
        primary = None

    benchmark = None if (is_macro and primary == default_bench) else default_bench
    return primary, contextual_insts, benchmark


class EventStudyEngine:
    """
    Computes causally verified multi-horizon market outcomes for historical events.
    Handles calendar sessions, overnight gaps, holidays, halts, resolution limits,
    corporate action factors, futures contract rollovers, and benchmark adjustments.
    """

    def __init__(
        self,
        calendar: Optional[MarketCalendarConfig] = None,
        rollover_specs: Optional[List[FuturesRolloverSpec]] = None,
        anchor_mode: AnchorMode = AnchorMode.PRE_EVENT_AS_OF,
        benchmark_beta: float = 1.0,
        max_stale_pre_seconds: float = 432000.0,  # 5 days max lookback across long weekends
    ) -> None:
        self.calendar = calendar or MarketCalendarConfig.for_jurisdiction(MarketJurisdiction.IN)
        self.anchor_mode = anchor_mode
        self.benchmark_beta = benchmark_beta
        self.max_stale_pre_seconds = max_stale_pre_seconds
        self._rollover_map: Dict[Tuple[str, str], FuturesRolloverSpec] = {}
        for spec in rollover_specs or []:
            self._rollover_map[(spec.from_contract_id, spec.to_contract_id)] = spec

    @staticmethod
    def estimate_bar_resolution_seconds(observations: Sequence[PriceObservation]) -> Optional[float]:
        """
        Estimate intrinsic sampling frequency of observations without letting overnight/weekend gaps
        artificially inflate intraday bar spacing.
        """
        if len(observations) < 2:
            return None
        deltas = [
            (observations[i].timestamp_utc - observations[i - 1].timestamp_utc).total_seconds()
            for i in range(1, len(observations))
            if (observations[i].timestamp_utc - observations[i - 1].timestamp_utc).total_seconds() > 0
        ]
        if not deltas:
            return None
        # Intraday observations (< 4h apart) represent genuine intraday frequency
        intraday_deltas = sorted(d for d in deltas if d < 14400.0)
        if intraday_deltas:
            return intraday_deltas[len(intraday_deltas) // 2]
        deltas_sorted = sorted(deltas)
        return deltas_sorted[len(deltas_sorted) // 2]

    def _resolve_start_observation(
        self,
        observations: Sequence[PriceObservation],
        decision_ts: datetime,
    ) -> Tuple[Optional[PriceObservation], OutcomeDataQualityStatus, Optional[str]]:
        if not observations:
            return None, OutcomeDataQualityStatus.MISSING_DATA, "No price observations supplied"

        if self.anchor_mode == AnchorMode.PRE_EVENT_AS_OF:
            pre = [o for o in observations if o.timestamp_utc <= decision_ts]
            if not pre:
                return None, OutcomeDataQualityStatus.MISSING_DATA, "No pre-event observation at or before decision_ts"
            cand = pre[-1]
            if cand.is_halted:
                return cand, OutcomeDataQualityStatus.HALTED_TRADING, "Market halted at pre-event anchor"
            # Check gap across non-holiday trading days
            trading_gap = self.calendar.count_trading_days_between(cand.timestamp_utc, decision_ts)
            if (decision_ts - cand.timestamp_utc).total_seconds() > self.max_stale_pre_seconds or trading_gap > 3:
                return cand, OutcomeDataQualityStatus.STALE_OBSERVATION, "Pre-event anchor exceeds staleness limit"
            return cand, OutcomeDataQualityStatus.VERIFIED, None

        # POST_DECISION_EXECUTABLE
        post = [o for o in observations if o.timestamp_utc >= decision_ts]
        if not post:
            return None, OutcomeDataQualityStatus.MISSING_DATA, "No executable observation at or after decision_ts"
        cand = post[0]
        if cand.is_halted:
            return cand, OutcomeDataQualityStatus.HALTED_TRADING, "Market halted at post-decision entry"
        return cand, OutcomeDataQualityStatus.VERIFIED, None

    @staticmethod
    def _default_target_tolerance(horizon: OutcomeHorizon) -> Tuple[float, float]:
        """Return (pre_tolerance_seconds, post_tolerance_seconds) for target timestamp matching."""
        return {
            OutcomeHorizon.M1: (5.0, 60.0),
            OutcomeHorizon.M5: (15.0, 120.0),
            OutcomeHorizon.M30: (60.0, 300.0),
            OutcomeHorizon.H1: (120.0, 600.0),
            OutcomeHorizon.D1: (1800.0, 7200.0),
            OutcomeHorizon.D5: (3600.0, 14400.0),
        }[horizon]

    def _resolve_target_timestamp(
        self,
        horizon: OutcomeHorizon,
        anchor_ref_ts: datetime,
    ) -> Tuple[datetime, bool]:
        if horizon == OutcomeHorizon.M1:
            return self.calendar.advance_trading_minutes(anchor_ref_ts, 1)
        if horizon == OutcomeHorizon.M5:
            return self.calendar.advance_trading_minutes(anchor_ref_ts, 5)
        if horizon == OutcomeHorizon.M30:
            return self.calendar.advance_trading_minutes(anchor_ref_ts, 30)
        if horizon == OutcomeHorizon.H1:
            return self.calendar.advance_trading_minutes(anchor_ref_ts, 60)
        if horizon == OutcomeHorizon.D1:
            return self.calendar.advance_trading_days(anchor_ref_ts, 1)
        return self.calendar.advance_trading_days(anchor_ref_ts, 5)

    def _find_target_observation(
        self,
        observations: Sequence[PriceObservation],
        start_ts: datetime,
        target_ts: datetime,
        horizon: OutcomeHorizon,
    ) -> Tuple[Optional[PriceObservation], OutcomeDataQualityStatus, Optional[str]]:
        pre_tol, post_tol = self._default_target_tolerance(horizon)
        # Prefer earliest observation strictly after start_ts closest to target_ts within [target-pre_tol, target+post_tol]
        candidates = [
            o for o in observations
            if o.timestamp_utc > start_ts
            and (target_ts - timedelta(seconds=pre_tol)) <= o.timestamp_utc <= (target_ts + timedelta(seconds=post_tol))
        ]
        if not candidates:
            return None, OutcomeDataQualityStatus.MISSING_DATA, f"No observation within tolerance around target {target_ts.isoformat()}"

        # Prefer observation >= target_ts if available; else closest before within pre_tol
        at_or_after = [o for o in candidates if o.timestamp_utc >= target_ts]
        chosen = at_or_after[0] if at_or_after else candidates[-1]
        if chosen.is_halted:
            return chosen, OutcomeDataQualityStatus.HALTED_TRADING, "Trading halted at horizon endpoint"
        return chosen, OutcomeDataQualityStatus.VERIFIED, None

    def _adjust_series_prices(
        self,
        window_obs: List[PriceObservation],
    ) -> Tuple[Optional[List[float]], OutcomeDataQualityStatus, bool, Optional[Dict[str, object]], Optional[str]]:
        """
        Apply corporate action multipliers and explicit futures rollover adjustments across `window_obs`.
        Strictly refuses silent stitching across differing `contract_id`s without a documented RolloverSpec.
        """
        if not window_obs:
            return None, OutcomeDataQualityStatus.MISSING_DATA, False, None, "Empty window"

        start_obs = window_obs[0]
        end_obs = window_obs[-1]

        # Detect trading halts anywhere along window endpoints or dominating interior
        if any(o.is_halted for o in (start_obs, end_obs)):
            return None, OutcomeDataQualityStatus.HALTED_TRADING, False, None, "Trading halted at endpoint"

        # Check corporate action adjustment
        ca_applied = any(
            abs(o.corporate_action_factor - start_obs.corporate_action_factor) > 1e-9
            for o in window_obs
        )

        # Check contract identity consistency
        contracts_seen = [o.contract_id for o in window_obs if o.contract_id is not None]
        distinct_contracts: List[str] = []
        for c in contracts_seen:
            if not distinct_contracts or distinct_contracts[-1] != c:
                distinct_contracts.append(c)

        rollover_applied = False
        rollover_meta: Optional[Dict[str, object]] = None
        status = OutcomeDataQualityStatus.CORPORATE_ACTION_ADJUSTED if ca_applied else OutcomeDataQualityStatus.VERIFIED

        # Compute base corporate-action adjusted prices
        base_prices = [o.price * o.corporate_action_factor for o in window_obs]

        if len(distinct_contracts) > 1:
            if len(distinct_contracts) > 2:
                return (
                    None,
                    OutcomeDataQualityStatus.ROLLOVER_INVALID,
                    False,
                    {"contracts_seen": distinct_contracts, "reason": "Multi-roll window unsupported without chain spec"},
                    "Multiple contract rollovers in single window",
                )
            c_from, c_to = distinct_contracts[0], distinct_contracts[1]
            spec = self._rollover_map.get((c_from, c_to))
            if spec is None or spec.method == FuturesRolloverMethod.REJECT_ACROSS_EXPIRY:
                return (
                    None,
                    OutcomeDataQualityStatus.ROLLOVER_INVALID,
                    False,
                    {"from_contract": c_from, "to_contract": c_to, "reason": "Missing documented FuturesRolloverSpec"},
                    f"Silent futures stitching prohibited across expiry {c_from} -> {c_to}",
                )

            adjusted: List[float] = []
            for idx, o in enumerate(window_obs):
                p = base_prices[idx]
                if o.contract_id == c_to:
                    if spec.method == FuturesRolloverMethod.ADDITIVE_SPREAD_BACK_ADJUST:
                        spread = spec.price_spread_to_minus_from or 0.0
                        # Express post-rollover contract on pre-rollover price basis by subtracting rollover basis jump
                        p = p - spread
                    elif spec.method == FuturesRolloverMethod.RATIO_MULTIPLICATIVE:
                        ratio = spec.ratio_to_over_from or 1.0
                        if ratio <= 0:
                            return None, OutcomeDataQualityStatus.ROLLOVER_INVALID, False, None, "Non-positive rollover ratio"
                        p = p / ratio
                adjusted.append(p)

            rollover_applied = True
            status = OutcomeDataQualityStatus.ROLLOVER_ADJUSTED
            rollover_meta = {
                "from_contract": c_from,
                "to_contract": c_to,
                "method": spec.method.value,
                "spread": spec.price_spread_to_minus_from,
                "ratio": spec.ratio_to_over_from,
            }
            base_prices = adjusted

        if any(p <= 0 for p in base_prices):
            return None, OutcomeDataQualityStatus.ROLLOVER_INVALID, rollover_applied, rollover_meta, "Adjusted price <= 0"

        return base_prices, status, rollover_applied, rollover_meta, None

    def compute_outcome_label(
        self,
        event_id: str,
        instrument_id: str,
        horizon: OutcomeHorizon,
        event_timestamp_utc: datetime,
        decision_timestamp_utc: datetime,
        observations: Sequence[PriceObservation],
        benchmark_instrument: Optional[str] = None,
        benchmark_observations: Optional[Sequence[PriceObservation]] = None,
        evaluation_decision_ts: Optional[datetime] = None,
        market_data_source: str = "LOCAL_BARS",
        is_synthetic: bool = False,
    ) -> EventOutcomeLabel:
        """
        Compute a single causally verified EventOutcomeLabel for `(event_id, instrument_id, horizon)`.
        """
        sorted_obs = sorted(observations, key=lambda o: o.timestamp_utc)

        # 1. Source Resolution Check
        est_res = self.estimate_bar_resolution_seconds(sorted_obs)
        if est_res is not None and est_res > horizon.min_required_resolution_seconds + 1e-6:
            return EventOutcomeLabel(
                event_id=event_id,
                instrument_id=instrument_id,
                horizon=horizon,
                event_timestamp_utc=event_timestamp_utc,
                decision_timestamp_utc=decision_timestamp_utc,
                benchmark_instrument=benchmark_instrument,
                market_data_source=market_data_source,
                anchor_mode=self.anchor_mode.value,
                data_quality_status=OutcomeDataQualityStatus.INSUFFICIENT_RESOLUTION,
                notes=f"Estimated bar spacing {est_res:.1f}s exceeds horizon requirement {horizon.min_required_resolution_seconds:.1f}s",
                is_synthetic=is_synthetic,
            )

        # 2. Resolve Start Observation
        start_obs, start_status, start_note = self._resolve_start_observation(sorted_obs, decision_timestamp_utc)
        if start_obs is None or start_status != OutcomeDataQualityStatus.VERIFIED:
            return EventOutcomeLabel(
                event_id=event_id,
                instrument_id=instrument_id,
                horizon=horizon,
                event_timestamp_utc=event_timestamp_utc,
                decision_timestamp_utc=decision_timestamp_utc,
                outcome_start_price_ts=(start_obs.timestamp_utc if start_obs else None),
                start_contract_id=(start_obs.contract_id if start_obs else None),
                benchmark_instrument=benchmark_instrument,
                market_data_source=market_data_source,
                anchor_mode=self.anchor_mode.value,
                data_quality_status=start_status,
                notes=start_note,
                is_synthetic=is_synthetic,
            )

        # 3. Resolve Target Endpoint Timestamp respecting sessions & holidays
        # If event happens out-of-hours, active reaction clock runs from session open;
        # for PRE_EVENT_AS_OF we anchor start price at prev close and advance from decision_ts across calendar.
        ref_clock = (
            decision_timestamp_utc
            if self.anchor_mode == AnchorMode.PRE_EVENT_AS_OF
            else max(decision_timestamp_utc, start_obs.timestamp_utc)
        )
        target_ts, crossed_boundary = self._resolve_target_timestamp(horizon, ref_clock)

        # Causal Maturity Check against evaluation_decision_ts (if caller is querying at a point in time)
        if evaluation_decision_ts is not None and target_ts > evaluation_decision_ts:
            return EventOutcomeLabel(
                event_id=event_id,
                instrument_id=instrument_id,
                horizon=horizon,
                event_timestamp_utc=event_timestamp_utc,
                decision_timestamp_utc=decision_timestamp_utc,
                outcome_start_price_ts=start_obs.timestamp_utc,
                outcome_end_price_ts=target_ts,
                start_contract_id=start_obs.contract_id,
                benchmark_instrument=benchmark_instrument,
                market_data_source=market_data_source,
                anchor_mode=self.anchor_mode.value,
                data_quality_status=OutcomeDataQualityStatus.IMMATURE,
                session_boundary_crossed=crossed_boundary,
                notes=f"Horizon target {target_ts.isoformat()} has not elapsed by query decision {evaluation_decision_ts.isoformat()}",
                is_synthetic=is_synthetic,
            )

        # 4. Find Target End Observation
        end_obs, end_status, end_note = self._find_target_observation(
            sorted_obs, start_obs.timestamp_utc, target_ts, horizon
        )
        if end_obs is None or end_status != OutcomeDataQualityStatus.VERIFIED:
            return EventOutcomeLabel(
                event_id=event_id,
                instrument_id=instrument_id,
                horizon=horizon,
                event_timestamp_utc=event_timestamp_utc,
                decision_timestamp_utc=decision_timestamp_utc,
                outcome_start_price_ts=start_obs.timestamp_utc,
                outcome_end_price_ts=(end_obs.timestamp_utc if end_obs else None),
                start_contract_id=start_obs.contract_id,
                end_contract_id=(end_obs.contract_id if end_obs else None),
                benchmark_instrument=benchmark_instrument,
                market_data_source=market_data_source,
                anchor_mode=self.anchor_mode.value,
                data_quality_status=end_status,
                session_boundary_crossed=crossed_boundary,
                notes=end_note,
                is_synthetic=is_synthetic,
            )

        if evaluation_decision_ts is not None and end_obs.timestamp_utc > evaluation_decision_ts:
            return EventOutcomeLabel(
                event_id=event_id,
                instrument_id=instrument_id,
                horizon=horizon,
                event_timestamp_utc=event_timestamp_utc,
                decision_timestamp_utc=decision_timestamp_utc,
                outcome_start_price_ts=start_obs.timestamp_utc,
                outcome_end_price_ts=end_obs.timestamp_utc,
                data_quality_status=OutcomeDataQualityStatus.IMMATURE,
                notes="Observed endpoint exceeds evaluation_decision_ts",
                is_synthetic=is_synthetic,
            )

        # 5. Gather Window Observations [start_obs, ..., end_obs] & Apply Rollover / Corp Action Adjustments
        window_obs = [
            o for o in sorted_obs
            if start_obs.timestamp_utc <= o.timestamp_utc <= end_obs.timestamp_utc
        ]
        adj_prices, adj_status, rollover_applied, rollover_meta, adj_note = self._adjust_series_prices(window_obs)
        overnight_gap = self.calendar.count_trading_days_between(start_obs.timestamp_utc, end_obs.timestamp_utc) >= 1 or (
            not self.calendar.is_in_session(decision_timestamp_utc)
        )

        if adj_prices is None:
            return EventOutcomeLabel(
                event_id=event_id,
                instrument_id=instrument_id,
                horizon=horizon,
                event_timestamp_utc=event_timestamp_utc,
                decision_timestamp_utc=decision_timestamp_utc,
                outcome_start_price_ts=start_obs.timestamp_utc,
                outcome_end_price_ts=end_obs.timestamp_utc,
                start_contract_id=start_obs.contract_id,
                end_contract_id=end_obs.contract_id,
                benchmark_instrument=benchmark_instrument,
                market_data_source=market_data_source,
                anchor_mode=self.anchor_mode.value,
                data_quality_status=adj_status,
                session_boundary_crossed=crossed_boundary or overnight_gap,
                overnight_gap_included=overnight_gap,
                contract_rollover_applied=rollover_applied,
                rollover_metadata=rollover_meta,
                notes=adj_note,
                is_synthetic=is_synthetic,
            )

        p_start = adj_prices[0]
        p_end = adj_prices[-1]
        simple_ret = (p_end / p_start) - 1.0
        log_ret = math.log(p_end / p_start)

        # 6. Path Risk & Microstructure Statistics over Window
        path_returns = [(p / p_start) - 1.0 for p in adj_prices[1:]]
        mfe = max(path_returns) if path_returns else simple_ret
        mae = min(path_returns) if path_returns else simple_ret

        realized_vol: Optional[float] = None
        if len(adj_prices) >= 3:
            step_logs = [math.log(adj_prices[i] / adj_prices[i - 1]) for i in range(1, len(adj_prices))]
            realized_vol = round(math.sqrt(sum(r * r for r in step_logs)), 6)

        spreads_bps = [
            ((o.ask - o.bid) / ((o.ask + o.bid) / 2.0)) * 10000.0
            for o in window_obs
            if o.bid is not None and o.ask is not None and o.ask >= o.bid and (o.ask + o.bid) > 0
        ]
        avg_spread = round(sum(spreads_bps) / len(spreads_bps), 3) if spreads_bps else None

        vols = [o.volume for o in window_obs[1:] if o.volume is not None and o.volume >= 0]
        total_vol = round(sum(vols), 2) if vols else None

        # 7. Benchmark Return Adjustment over matching [start_ts, end_ts]
        bench_ret: Optional[float] = None
        bench_adj_ret: Optional[float] = None
        if benchmark_instrument and benchmark_observations:
            pre_tol, post_tol = self._default_target_tolerance(horizon)
            b_sorted = sorted(benchmark_observations, key=lambda o: o.timestamp_utc)
            b_start_cands = [o for o in b_sorted if o.timestamp_utc <= start_obs.timestamp_utc and not o.is_halted]
            if not b_start_cands:
                b_start_cands = [
                    o for o in b_sorted
                    if abs((o.timestamp_utc - start_obs.timestamp_utc).total_seconds()) <= post_tol and not o.is_halted
                ]
            b_end_cands = [
                o for o in b_sorted
                if (end_obs.timestamp_utc - timedelta(seconds=pre_tol)) <= o.timestamp_utc <= (end_obs.timestamp_utc + timedelta(seconds=post_tol))
                and not o.is_halted
            ]
            if b_start_cands and b_end_cands:
                b_s = b_start_cands[-1]
                b_e = [o for o in b_end_cands if o.timestamp_utc >= end_obs.timestamp_utc]
                b_end = b_e[0] if b_e else b_end_cands[-1]
                b_window = [o for o in b_sorted if b_s.timestamp_utc <= o.timestamp_utc <= b_end.timestamp_utc]
                b_adj, b_stat, _, _, _ = self._adjust_series_prices(b_window)
                if b_adj and len(b_adj) >= 2 and b_stat in (
                    OutcomeDataQualityStatus.VERIFIED,
                    OutcomeDataQualityStatus.ROLLOVER_ADJUSTED,
                    OutcomeDataQualityStatus.CORPORATE_ACTION_ADJUSTED,
                ):
                    bench_ret = round((b_adj[-1] / b_adj[0]) - 1.0, 6)
                    bench_adj_ret = round(simple_ret - self.benchmark_beta * bench_ret, 6)

        return EventOutcomeLabel(
            event_id=event_id,
            instrument_id=instrument_id,
            horizon=horizon,
            event_timestamp_utc=event_timestamp_utc,
            decision_timestamp_utc=decision_timestamp_utc,
            outcome_start_price_ts=start_obs.timestamp_utc,
            outcome_end_price_ts=end_obs.timestamp_utc,
            start_price=round(p_start, 4),
            end_price=round(p_end, 4),
            raw_log_return=round(log_ret, 6),
            simple_return=round(simple_ret, 6),
            benchmark_instrument=benchmark_instrument,
            benchmark_return=bench_ret,
            benchmark_adjusted_return=bench_adj_ret,
            realized_volatility=realized_vol,
            max_favorable_excursion=round(mfe, 6),
            max_adverse_excursion=round(mae, 6),
            avg_spread_bps=avg_spread,
            total_volume=total_vol,
            market_data_source=market_data_source,
            anchor_mode=self.anchor_mode.value,
            data_quality_status=adj_status,
            session_boundary_crossed=crossed_boundary or overnight_gap,
            overnight_gap_included=overnight_gap,
            contract_rollover_applied=rollover_applied,
            start_contract_id=start_obs.contract_id,
            end_contract_id=end_obs.contract_id,
            rollover_metadata=rollover_meta,
            is_synthetic=is_synthetic,
        )

    def compute_multi_horizon_outcomes(
        self,
        event_id: str,
        instrument_id: str,
        event_timestamp_utc: datetime,
        decision_timestamp_utc: datetime,
        observations: Sequence[PriceObservation],
        horizons: Optional[Sequence[OutcomeHorizon]] = None,
        benchmark_instrument: Optional[str] = None,
        benchmark_observations: Optional[Sequence[PriceObservation]] = None,
        evaluation_decision_ts: Optional[datetime] = None,
        market_data_source: str = "LOCAL_BARS",
        is_synthetic: bool = False,
    ) -> Dict[OutcomeHorizon, EventOutcomeLabel]:
        target_horizons = horizons or list(OutcomeHorizon)
        return {
            h: self.compute_outcome_label(
                event_id=event_id,
                instrument_id=instrument_id,
                horizon=h,
                event_timestamp_utc=event_timestamp_utc,
                decision_timestamp_utc=decision_timestamp_utc,
                observations=observations,
                benchmark_instrument=benchmark_instrument,
                benchmark_observations=benchmark_observations,
                evaluation_decision_ts=evaluation_decision_ts,
                market_data_source=market_data_source,
                is_synthetic=is_synthetic,
            )
            for h in target_horizons
        }
