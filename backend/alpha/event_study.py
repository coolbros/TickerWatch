"""Causal Historical Market Response & Event-Study Engine.

Computes verified multi-horizon post-event outcome labels (`1m`, `5m`, `30m`, `1h`, `1d`, `5d`)
strictly respecting:
1. Exchange trading calendars, session boundaries, overnight gaps, and holidays (NSE/BSE, US).
2. Explicit distinction between total event REACTION (`PRE_EVENT_AS_OF`) and post-receipt
   EXECUTABLE outcomes (`POST_DECISION_EXECUTABLE`) accounting for pipeline latency,
   next-session open execution, and bid-ask spreads.
3. Source observation resolution (prevents hallucinating intraday outcomes from daily bars).
4. Trading halts, missing observations, stale quotes, and corporate action multipliers.
5. Futures contract expiry and explicit rollover adjustment policies (prohibits silent stitching).
6. Causal benchmark-adjustment provenance (`SIMPLE_MARKET_ADJUSTED` vs pre-event rolling OLS beta).
7. Point-in-time outcome maturity (`outcome_end_price_ts <= evaluation_decision_ts`).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
import math
from typing import Dict, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo
import numpy as np
from pydantic import BaseModel, Field

from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.instruments import RelevanceTier
from backend.alpha.nlp_pipeline import FinancialEventRepresentation
from backend.alpha.outcomes import (
    BenchmarkAdjustmentMethod,
    EventOutcomeLabel,
    OutcomeDataQualityStatus,
    OutcomeHorizon,
    OutcomeLabelType,
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
            target_d = d if trading_days == 1 else self.next_trading_day(d, trading_days - 1)
            target_loc = datetime.combine(target_d, self.session_close_local, tzinfo=tz)
            return target_loc.astimezone(timezone.utc), True

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
    Separates REACTION outcomes (anchored pre-event to measure market repricing) from
    EXECUTABLE outcomes (anchored post-receipt + latency within active market sessions
    and accounting for bid-ask spread).
    Supports explicit SIMPLE_MARKET_ADJUSTED vs strictly pre-event PRE_EVENT_ROLLING_BETA.
    """

    def __init__(
        self,
        calendar: Optional[MarketCalendarConfig] = None,
        rollover_specs: Optional[List[FuturesRolloverSpec]] = None,
        anchor_mode: AnchorMode = AnchorMode.PRE_EVENT_AS_OF,
        pipeline_latency_ms: float = 0.0,
        executable_side: int = 0,  # 0=mid/close (+spread metadata), +1=cross ask->bid, -1=cross bid->ask
        benchmark_mode: BenchmarkAdjustmentMethod = BenchmarkAdjustmentMethod.PRE_EVENT_ROLLING_BETA,
        benchmark_beta: Optional[float] = None,  # Legacy explicit constant beta override
        beta_lookback_obs: int = 30,
        beta_min_samples: int = 10,
        max_stale_pre_seconds: float = 432000.0,  # 5 days max lookback across long weekends
    ) -> None:
        self.calendar = calendar or MarketCalendarConfig.for_jurisdiction(MarketJurisdiction.IN)
        self.anchor_mode = anchor_mode
        self.pipeline_latency_ms = max(0.0, float(pipeline_latency_ms))
        self.executable_side = executable_side
        self.benchmark_mode = (
            BenchmarkAdjustmentMethod.FIXED_BETA_LEGACY
            if benchmark_beta is not None
            else benchmark_mode
        )
        self.benchmark_beta = benchmark_beta
        self.beta_lookback_obs = beta_lookback_obs
        self.beta_min_samples = beta_min_samples
        self.max_stale_pre_seconds = max_stale_pre_seconds
        self._rollover_map: Dict[Tuple[str, str], FuturesRolloverSpec] = {}
        for spec in rollover_specs or []:
            self._rollover_map[(spec.from_contract_id, spec.to_contract_id)] = spec

    @property
    def label_type(self) -> OutcomeLabelType:
        return (
            OutcomeLabelType.REACTION
            if self.anchor_mode == AnchorMode.PRE_EVENT_AS_OF
            else OutcomeLabelType.EXECUTABLE
        )

    def estimate_bar_resolution_seconds(
        self,
        observations: Sequence[PriceObservation],
    ) -> Optional[float]:
        """
        Estimate intrinsic trading-session spacing of observations without letting overnight,
        weekend, or holiday closures inflate resolution (e.g., Fri close -> Mon close is 1 trading day).
        """
        if len(observations) < 2:
            return None
        effective_deltas: List[float] = []
        for i in range(1, len(observations)):
            dt_sec = (observations[i].timestamp_utc - observations[i - 1].timestamp_utc).total_seconds()
            if dt_sec <= 0:
                continue
            if dt_sec < 14400.0:
                effective_deltas.append(dt_sec)
            else:
                td_gap = self.calendar.count_trading_days_between(
                    observations[i - 1].timestamp_utc, observations[i].timestamp_utc
                )
                effective_deltas.append(max(1, td_gap) * 86400.0)
        if not effective_deltas:
            return None
        intraday_deltas = sorted(d for d in effective_deltas if d < 14400.0)
        if intraday_deltas:
            return intraday_deltas[len(intraday_deltas) // 2]
        deltas_sorted = sorted(effective_deltas)
        return deltas_sorted[len(deltas_sorted) // 2]

    def _resolve_start_observation(
        self,
        observations: Sequence[PriceObservation],
        decision_ts: datetime,
    ) -> Tuple[Optional[PriceObservation], OutcomeDataQualityStatus, Optional[str], Optional[str]]:
        if not observations:
            return None, OutcomeDataQualityStatus.MISSING_DATA, "No price observations supplied", None

        if self.anchor_mode == AnchorMode.PRE_EVENT_AS_OF:
            pre = [o for o in observations if o.timestamp_utc <= decision_ts]
            if not pre:
                return None, OutcomeDataQualityStatus.MISSING_DATA, "No pre-event observation at or before decision_ts", None
            cand = pre[-1]
            if cand.is_halted:
                return cand, OutcomeDataQualityStatus.HALTED_TRADING, "Market halted at pre-event anchor", "PRE_EVENT_REF"
            trading_gap = self.calendar.count_trading_days_between(cand.timestamp_utc, decision_ts)
            if (decision_ts - cand.timestamp_utc).total_seconds() > self.max_stale_pre_seconds or trading_gap > 3:
                return cand, OutcomeDataQualityStatus.STALE_OBSERVATION, "Pre-event anchor exceeds staleness limit", "PRE_EVENT_REF"
            return cand, OutcomeDataQualityStatus.VERIFIED, None, "PRE_EVENT_REF"

        # POST_DECISION_EXECUTABLE: require receipt + pipeline latency AND regular active session eligibility
        eligible_ts = decision_ts + timedelta(milliseconds=self.pipeline_latency_ms)
        in_session_post = [
            o for o in observations
            if o.timestamp_utc >= eligible_ts and self.calendar.is_in_session(o.timestamp_utc)
        ]
        if not in_session_post:
            # Check if only off-hours prints existed before next session
            return (
                None,
                OutcomeDataQualityStatus.MISSING_DATA,
                f"No executable active-session observation at or after {eligible_ts.isoformat()}",
                None,
            )
        cand = in_session_post[0]
        if cand.is_halted:
            return cand, OutcomeDataQualityStatus.HALTED_TRADING, "Market halted at post-decision entry", "HALTED"

        tz = self.calendar._tz()
        crossed_next_session = (
            not self.calendar.is_in_session(eligible_ts)
            or cand.timestamp_utc.astimezone(tz).date() != eligible_ts.astimezone(tz).date()
        )
        if self.executable_side > 0:
            q_type = "EXEC_ASK_NEXT_SESSION" if crossed_next_session else "EXEC_ASK"
        elif self.executable_side < 0:
            q_type = "EXEC_BID_NEXT_SESSION" if crossed_next_session else "EXEC_BID"
        else:
            q_type = "NEXT_SESSION_OPEN" if crossed_next_session else "EXEC_OBSERVATION"
        return cand, OutcomeDataQualityStatus.VERIFIED, None, q_type

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
        candidates = [
            o for o in observations
            if o.timestamp_utc > start_ts
            and (target_ts - timedelta(seconds=pre_tol)) <= o.timestamp_utc <= (target_ts + timedelta(seconds=post_tol))
        ]
        if not candidates:
            return None, OutcomeDataQualityStatus.MISSING_DATA, f"No observation within tolerance around target {target_ts.isoformat()}"

        at_or_after = [o for o in candidates if o.timestamp_utc >= target_ts]
        chosen = at_or_after[0] if at_or_after else candidates[-1]
        if chosen.is_halted:
            return chosen, OutcomeDataQualityStatus.HALTED_TRADING, "Trading halted at horizon endpoint"
        return chosen, OutcomeDataQualityStatus.VERIFIED, None

    def _effective_obs_price(self, obs: PriceObservation, role: str) -> float:
        """
        Extract executable or reaction reference price cleanly.
        For EXECUTABLE mode with explicit side (+1 long / -1 short), cross the bid-ask spread.
        """
        raw = obs.price
        if self.anchor_mode == AnchorMode.POST_DECISION_EXECUTABLE and self.executable_side != 0:
            if obs.bid is not None and obs.ask is not None and obs.ask >= obs.bid > 0:
                if role == "entry":
                    raw = obs.ask if self.executable_side > 0 else obs.bid
                elif role == "exit":
                    raw = obs.bid if self.executable_side > 0 else obs.ask
        return raw * obs.corporate_action_factor

    def _adjust_series_prices(
        self,
        window_obs: List[PriceObservation],
        apply_executable_quotes: bool = False,
    ) -> Tuple[Optional[List[float]], OutcomeDataQualityStatus, bool, Optional[Dict[str, object]], Optional[str]]:
        """
        Apply corporate action multipliers and explicit futures rollover adjustments across `window_obs`.
        Strictly refuses silent stitching across differing `contract_id`s without a documented RolloverSpec.
        """
        if not window_obs:
            return None, OutcomeDataQualityStatus.MISSING_DATA, False, None, "Empty window"

        start_obs = window_obs[0]
        end_obs = window_obs[-1]

        if any(o.is_halted for o in (start_obs, end_obs)):
            return None, OutcomeDataQualityStatus.HALTED_TRADING, False, None, "Trading halted at endpoint"

        ca_applied = any(
            abs(o.corporate_action_factor - start_obs.corporate_action_factor) > 1e-9
            for o in window_obs
        )

        contracts_seen = [o.contract_id for o in window_obs if o.contract_id is not None]
        distinct_contracts: List[str] = []
        for c in contracts_seen:
            if not distinct_contracts or distinct_contracts[-1] != c:
                distinct_contracts.append(c)

        rollover_applied = False
        rollover_meta: Optional[Dict[str, object]] = None
        status = OutcomeDataQualityStatus.CORPORATE_ACTION_ADJUSTED if ca_applied else OutcomeDataQualityStatus.VERIFIED

        base_prices: List[float] = []
        n_w = len(window_obs)
        for idx, o in enumerate(window_obs):
            if apply_executable_quotes and self.anchor_mode == AnchorMode.POST_DECISION_EXECUTABLE and self.executable_side != 0:
                if idx == 0:
                    base_prices.append(self._effective_obs_price(o, "entry"))
                elif idx == n_w - 1:
                    base_prices.append(self._effective_obs_price(o, "exit"))
                else:
                    base_prices.append(o.price * o.corporate_action_factor)
            else:
                base_prices.append(o.price * o.corporate_action_factor)

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

    def _estimate_pre_event_beta(
        self,
        inst_obs: Sequence[PriceObservation],
        bench_obs: Sequence[PriceObservation],
        cutoff_ts: datetime,
    ) -> Tuple[Optional[float], int, Optional[datetime], Optional[datetime]]:
        """
        Estimate OLS beta strictly from pre-event observations (`timestamp_utc <= cutoff_ts`).
        Zero future prices ever enter the beta calculation window.
        """
        pre_i = [o for o in inst_obs if o.timestamp_utc <= cutoff_ts and not o.is_halted]
        pre_b = [o for o in bench_obs if o.timestamp_utc <= cutoff_ts and not o.is_halted]
        if len(pre_i) < self.beta_min_samples + 1 or len(pre_b) < self.beta_min_samples + 1:
            return None, max(0, min(len(pre_i), len(pre_b)) - 1), None, None

        pre_i = pre_i[-(self.beta_lookback_obs + 1) :]
        b_times = [o.timestamp_utc.timestamp() for o in pre_b]

        aligned_i: List[float] = []
        aligned_b: List[float] = []
        aligned_ts: List[datetime] = []
        j = 0
        for oi in pre_i:
            t_i = oi.timestamp_utc.timestamp()
            while j + 1 < len(b_times) and b_times[j + 1] <= t_i:
                j += 1
            if abs(b_times[j] - t_i) <= 300.0 and pre_b[j].timestamp_utc <= cutoff_ts:
                aligned_i.append(oi.price * oi.corporate_action_factor)
                aligned_b.append(pre_b[j].price * pre_b[j].corporate_action_factor)
                aligned_ts.append(oi.timestamp_utc)

        if len(aligned_i) < self.beta_min_samples + 1:
            return None, max(0, len(aligned_i) - 1), None, None

        r_i = np.diff(np.asarray(aligned_i, dtype=np.float64)) / np.asarray(aligned_i[:-1], dtype=np.float64)
        r_b = np.diff(np.asarray(aligned_b, dtype=np.float64)) / np.asarray(aligned_b[:-1], dtype=np.float64)
        var_b = float(np.var(r_b))
        if var_b < 1e-12:
            return None, int(len(r_i)), aligned_ts[0], aligned_ts[-1]

        cov_ib = float(np.mean((r_i - np.mean(r_i)) * (r_b - np.mean(r_b))))
        est_beta = cov_ib / var_b
        # Bound pathological small-sample leverage without hiding real variation
        est_beta = max(-5.0, min(5.0, est_beta))
        return round(est_beta, 6), int(len(r_i)), aligned_ts[0], aligned_ts[-1]

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
        lbl_type = self.label_type
        lat_ms = self.pipeline_latency_ms if lbl_type == OutcomeLabelType.EXECUTABLE else 0.0

        # 1. Source Resolution Check
        est_res = self.estimate_bar_resolution_seconds(sorted_obs)
        if est_res is not None and est_res > horizon.min_required_resolution_seconds + 1e-6:
            return EventOutcomeLabel(
                event_id=event_id,
                instrument_id=instrument_id,
                horizon=horizon,
                label_type=lbl_type,
                event_timestamp_utc=event_timestamp_utc,
                decision_timestamp_utc=decision_timestamp_utc,
                pipeline_latency_ms=lat_ms,
                benchmark_instrument=benchmark_instrument,
                market_data_source=market_data_source,
                anchor_mode=self.anchor_mode.value,
                data_quality_status=OutcomeDataQualityStatus.INSUFFICIENT_RESOLUTION,
                notes=f"Estimated bar spacing {est_res:.1f}s exceeds horizon requirement {horizon.min_required_resolution_seconds:.1f}s",
                is_synthetic=is_synthetic,
            )

        # 2. Resolve Start Observation
        start_obs, start_status, start_note, quote_type = self._resolve_start_observation(
            sorted_obs, decision_timestamp_utc
        )
        if start_obs is None or start_status != OutcomeDataQualityStatus.VERIFIED:
            return EventOutcomeLabel(
                event_id=event_id,
                instrument_id=instrument_id,
                horizon=horizon,
                label_type=lbl_type,
                event_timestamp_utc=event_timestamp_utc,
                decision_timestamp_utc=decision_timestamp_utc,
                outcome_start_price_ts=(start_obs.timestamp_utc if start_obs else None),
                pipeline_latency_ms=lat_ms,
                entry_quote_type=quote_type,
                start_contract_id=(start_obs.contract_id if start_obs else None),
                benchmark_instrument=benchmark_instrument,
                market_data_source=market_data_source,
                anchor_mode=self.anchor_mode.value,
                data_quality_status=start_status,
                notes=start_note,
                is_synthetic=is_synthetic,
            )

        entry_spread_bps: Optional[float] = None
        if start_obs.bid is not None and start_obs.ask is not None and start_obs.ask >= start_obs.bid:
            mid_s = (start_obs.ask + start_obs.bid) / 2.0
            if mid_s > 0:
                entry_spread_bps = round(((start_obs.ask - start_obs.bid) / mid_s) * 10000.0, 3)

        # 3. Resolve Target Endpoint Timestamp respecting sessions & holidays
        ref_clock = (
            decision_timestamp_utc
            if self.anchor_mode == AnchorMode.PRE_EVENT_AS_OF
            else start_obs.timestamp_utc
        )
        target_ts, crossed_boundary = self._resolve_target_timestamp(horizon, ref_clock)

        if evaluation_decision_ts is not None and target_ts > evaluation_decision_ts:
            return EventOutcomeLabel(
                event_id=event_id,
                instrument_id=instrument_id,
                horizon=horizon,
                label_type=lbl_type,
                event_timestamp_utc=event_timestamp_utc,
                decision_timestamp_utc=decision_timestamp_utc,
                outcome_start_price_ts=start_obs.timestamp_utc,
                outcome_end_price_ts=target_ts,
                pipeline_latency_ms=lat_ms,
                entry_quote_type=quote_type,
                entry_spread_bps_cost=entry_spread_bps,
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
                label_type=lbl_type,
                event_timestamp_utc=event_timestamp_utc,
                decision_timestamp_utc=decision_timestamp_utc,
                outcome_start_price_ts=start_obs.timestamp_utc,
                outcome_end_price_ts=(end_obs.timestamp_utc if end_obs else None),
                pipeline_latency_ms=lat_ms,
                entry_quote_type=quote_type,
                entry_spread_bps_cost=entry_spread_bps,
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
                label_type=lbl_type,
                event_timestamp_utc=event_timestamp_utc,
                decision_timestamp_utc=decision_timestamp_utc,
                outcome_start_price_ts=start_obs.timestamp_utc,
                outcome_end_price_ts=end_obs.timestamp_utc,
                pipeline_latency_ms=lat_ms,
                entry_quote_type=quote_type,
                entry_spread_bps_cost=entry_spread_bps,
                data_quality_status=OutcomeDataQualityStatus.IMMATURE,
                notes="Observed endpoint exceeds evaluation_decision_ts",
                is_synthetic=is_synthetic,
            )

        # 5. Gather Window Observations [start_obs, ..., end_obs] & Apply Adjustments
        window_obs = [
            o for o in sorted_obs
            if start_obs.timestamp_utc <= o.timestamp_utc <= end_obs.timestamp_utc
        ]
        adj_prices, adj_status, rollover_applied, rollover_meta, adj_note = self._adjust_series_prices(
            window_obs,
            apply_executable_quotes=(lbl_type == OutcomeLabelType.EXECUTABLE),
        )
        overnight_gap = self.calendar.count_trading_days_between(start_obs.timestamp_utc, end_obs.timestamp_utc) >= 1 or (
            lbl_type == OutcomeLabelType.REACTION and not self.calendar.is_in_session(decision_timestamp_utc)
        )

        if adj_prices is None:
            return EventOutcomeLabel(
                event_id=event_id,
                instrument_id=instrument_id,
                horizon=horizon,
                label_type=lbl_type,
                event_timestamp_utc=event_timestamp_utc,
                decision_timestamp_utc=decision_timestamp_utc,
                outcome_start_price_ts=start_obs.timestamp_utc,
                outcome_end_price_ts=end_obs.timestamp_utc,
                pipeline_latency_ms=lat_ms,
                entry_quote_type=quote_type,
                entry_spread_bps_cost=entry_spread_bps,
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

        # 7. Benchmark Return Adjustment with Strict Causal Integrity (A5 Remediation)
        bench_ret: Optional[float] = None
        bench_adj_ret: Optional[float] = None
        adj_method = BenchmarkAdjustmentMethod.NONE
        est_beta_val: Optional[float] = None
        beta_count: Optional[int] = None
        beta_w_start: Optional[datetime] = None
        beta_w_end: Optional[datetime] = None

        if benchmark_instrument and benchmark_observations and self.benchmark_mode != BenchmarkAdjustmentMethod.NONE:
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
                    if self.benchmark_mode == BenchmarkAdjustmentMethod.FIXED_BETA_LEGACY:
                        beta_used = float(self.benchmark_beta if self.benchmark_beta is not None else 1.0)
                        est_beta_val = beta_used
                        adj_method = BenchmarkAdjustmentMethod.FIXED_BETA_LEGACY
                        bench_adj_ret = round(simple_ret - beta_used * bench_ret, 6)
                    elif self.benchmark_mode == BenchmarkAdjustmentMethod.PRE_EVENT_ROLLING_BETA:
                        est_b, n_b, ws, we = self._estimate_pre_event_beta(sorted_obs, b_sorted, start_obs.timestamp_utc)
                        beta_count = n_b
                        beta_w_start = ws
                        beta_w_end = we
                        if est_b is not None:
                            est_beta_val = est_b
                            adj_method = BenchmarkAdjustmentMethod.PRE_EVENT_ROLLING_BETA
                            bench_adj_ret = round(simple_ret - est_b * bench_ret, 6)
                        else:
                            # Explicitly fall back to unestimated simple market adjustment rather than claiming beta=1.0
                            adj_method = BenchmarkAdjustmentMethod.SIMPLE_MARKET_ADJUSTED
                            bench_adj_ret = round(simple_ret - bench_ret, 6)
                    else:
                        adj_method = BenchmarkAdjustmentMethod.SIMPLE_MARKET_ADJUSTED
                        bench_adj_ret = round(simple_ret - bench_ret, 6)

        return EventOutcomeLabel(
            event_id=event_id,
            instrument_id=instrument_id,
            horizon=horizon,
            label_type=lbl_type,
            event_timestamp_utc=event_timestamp_utc,
            decision_timestamp_utc=decision_timestamp_utc,
            outcome_start_price_ts=start_obs.timestamp_utc,
            outcome_end_price_ts=end_obs.timestamp_utc,
            pipeline_latency_ms=lat_ms,
            entry_quote_type=quote_type,
            entry_spread_bps_cost=entry_spread_bps,
            start_price=round(p_start, 4),
            end_price=round(p_end, 4),
            raw_log_return=round(log_ret, 6),
            simple_return=round(simple_ret, 6),
            benchmark_instrument=benchmark_instrument,
            benchmark_return=bench_ret,
            benchmark_adjusted_return=bench_adj_ret,
            benchmark_adjustment_method=adj_method,
            estimated_beta=est_beta_val,
            beta_sample_count=beta_count,
            beta_window_start_ts=beta_w_start,
            beta_window_end_ts=beta_w_end,
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

    def compute_reaction_and_executable_pair(
        self,
        event_id: str,
        instrument_id: str,
        horizon: OutcomeHorizon,
        event_timestamp_utc: datetime,
        decision_timestamp_utc: datetime,
        observations: Sequence[PriceObservation],
        pipeline_latency_ms: float = 15.0,
        executable_side: int = 0,
        benchmark_instrument: Optional[str] = None,
        benchmark_observations: Optional[Sequence[PriceObservation]] = None,
        evaluation_decision_ts: Optional[datetime] = None,
        market_data_source: str = "LOCAL_BARS",
        is_synthetic: bool = False,
    ) -> Dict[OutcomeLabelType, EventOutcomeLabel]:
        """Compute separated REACTION vs POST_DECISION_EXECUTABLE labels for the same event."""
        reaction_engine = EventStudyEngine(
            calendar=self.calendar,
            rollover_specs=list(self._rollover_map.values()),
            anchor_mode=AnchorMode.PRE_EVENT_AS_OF,
            benchmark_mode=self.benchmark_mode,
            benchmark_beta=self.benchmark_beta,
            beta_lookback_obs=self.beta_lookback_obs,
            beta_min_samples=self.beta_min_samples,
            max_stale_pre_seconds=self.max_stale_pre_seconds,
        )
        exec_engine = EventStudyEngine(
            calendar=self.calendar,
            rollover_specs=list(self._rollover_map.values()),
            anchor_mode=AnchorMode.POST_DECISION_EXECUTABLE,
            pipeline_latency_ms=pipeline_latency_ms,
            executable_side=executable_side,
            benchmark_mode=self.benchmark_mode,
            benchmark_beta=self.benchmark_beta,
            beta_lookback_obs=self.beta_lookback_obs,
            beta_min_samples=self.beta_min_samples,
            max_stale_pre_seconds=self.max_stale_pre_seconds,
        )
        return {
            OutcomeLabelType.REACTION: reaction_engine.compute_outcome_label(
                event_id=event_id,
                instrument_id=instrument_id,
                horizon=horizon,
                event_timestamp_utc=event_timestamp_utc,
                decision_timestamp_utc=decision_timestamp_utc,
                observations=observations,
                benchmark_instrument=benchmark_instrument,
                benchmark_observations=benchmark_observations,
                evaluation_decision_ts=evaluation_decision_ts,
                market_data_source=market_data_source,
                is_synthetic=is_synthetic,
            ),
            OutcomeLabelType.EXECUTABLE: exec_engine.compute_outcome_label(
                event_id=event_id,
                instrument_id=instrument_id,
                horizon=horizon,
                event_timestamp_utc=event_timestamp_utc,
                decision_timestamp_utc=decision_timestamp_utc,
                observations=observations,
                benchmark_instrument=benchmark_instrument,
                benchmark_observations=benchmark_observations,
                evaluation_decision_ts=evaluation_decision_ts,
                market_data_source=market_data_source,
                is_synthetic=is_synthetic,
            ),
        }
