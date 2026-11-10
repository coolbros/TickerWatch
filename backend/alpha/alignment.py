"""Temporal Alignment Engine: Zero-Lookahead As-Of Joins & Multi-Horizon Forward Outcomes."""

from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple
import uuid
from pydantic import BaseModel, Field

from backend.alpha.instruments import RelevanceTier
from backend.alpha.schemas import MarketEvent, NewsEvent
from backend.alpha.timestamps import (
    get_current_utc,
    get_monotonic_ns,
    to_utc_iso,
)


class MarketSessionStatus(str, Enum):
    OPEN = "OPEN"
    PRE_OPEN = "PRE_OPEN"
    CLOSED = "CLOSED"
    HALTED = "HALTED"


def get_market_session_status(dt_utc: datetime) -> MarketSessionStatus:
    """
    Determine Indian market session status based on UTC time.
    Indian Trading Session (Asia/Kolkata = UTC + 5:30):
    - Weekdays (Mon-Fri) only.
    - Pre-Open: 09:00 to 09:08 IST -> 03:30 to 03:38 UTC
    - Normal Trading: 09:15 to 15:30 IST -> 03:45 to 10:00 UTC
    """
    # 0 = Monday, 6 = Sunday
    if dt_utc.weekday() >= 5:
        return MarketSessionStatus.CLOSED

    # Time in minutes from midnight UTC
    minute_of_day = dt_utc.hour * 60 + dt_utc.minute

    # 03:30 UTC = 210 min; 03:38 UTC = 218 min
    if 210 <= minute_of_day < 218:
        return MarketSessionStatus.PRE_OPEN

    # 03:45 UTC = 225 min; 10:00 UTC = 600 min
    if 225 <= minute_of_day < 600:
        return MarketSessionStatus.OPEN

    return MarketSessionStatus.CLOSED


class PreEventMarketContext(BaseModel):
    """Observable market state strictly at or before decision timestamp."""

    instrument_id: str
    relevance: RelevanceTier
    market_observed_at_utc: Optional[datetime] = None
    bid_price: Optional[Decimal] = None
    ask_price: Optional[Decimal] = None
    mid_price: Optional[Decimal] = None
    spread: Optional[Decimal] = None
    bid_quantity: Optional[int] = None
    ask_quantity: Optional[int] = None
    last_traded_price: Optional[Decimal] = None
    last_traded_quantity: Optional[int] = None
    feed_freshness_ms: Optional[float] = None
    short_term_volatility_bps: Optional[float] = None
    session_state: MarketSessionStatus = MarketSessionStatus.OPEN
    is_stale: bool = False


class ForwardHorizonOutcome(BaseModel):
    """Post-event market observation across a designated forward research horizon."""

    horizon_label: str  # 100ms, 1s, 5s, 30s, 5m
    horizon_seconds: float
    target_timestamp_utc: datetime
    observed_timestamp_utc: Optional[datetime] = None
    mid_price: Optional[Decimal] = None
    forward_return_bps: Optional[float] = None  # Basis points return
    status: str = "VALID"  # VALID, RESOLUTION_INSUFFICIENT, MISSING_DATA, MARKET_CLOSED


class AlignedEventRecord(BaseModel):
    """Fully aligned research record combining news intelligence with contemporaneous market state."""

    alignment_id: str = Field(default_factory=lambda: f"align_{uuid.uuid4().hex[:12]}")
    news_event_id: str
    headline: str
    entity_ids: List[str]
    published_at_utc: Optional[datetime] = None
    provider_observed_at_utc: Optional[datetime] = None
    decision_timestamp_utc: datetime
    aligned_at_utc: datetime = Field(default_factory=get_current_utc)
    alignment_duration_ms: float = 0.0
    pre_event_context: Dict[str, PreEventMarketContext] = Field(default_factory=dict)
    forward_outcomes: Dict[str, Dict[str, ForwardHorizonOutcome]] = Field(default_factory=dict)
    quality_flag: str = "CLEAN"  # CLEAN, STALE_MARKET, OUT_OF_HOURS, PARTIALLY_UNALIGNED
    schema_version: str = "2.0.0"


HORIZON_MAP = {
    "100ms": 0.1,
    "1s": 1.0,
    "5s": 5.0,
    "30s": 30.0,
    "5m": 300.0,
}


class TemporalAlignmentEngine:
    """
    Sub-second temporal alignment engine executing causal as-of joins.
    Maintains a ring buffer of historical market events per instrument and
    anchors news arrivals strictly against prevailing pre-event quotes without look-ahead bias.
    """

    def __init__(
        self,
        lookback_window_seconds: float = 300.0,
        stale_quote_threshold_seconds: float = 60.0,
    ) -> None:
        self.lookback_window_seconds = lookback_window_seconds
        self.stale_quote_threshold_seconds = stale_quote_threshold_seconds
        # In-memory history per instrument: deque of MarketEvents sorted by local_received_at_utc
        self._market_history: Dict[str, deque[MarketEvent]] = {}

    def add_market_event(self, event: MarketEvent) -> None:
        """Add incoming market event to in-memory chronological buffer."""
        inst = event.instrument_id
        if inst not in self._market_history:
            self._market_history[inst] = deque()

        queue = self._market_history[inst]
        queue.append(event)

        # Prune old events beyond lookback window
        cutoff = event.local_received_at_utc - timedelta(seconds=self.lookback_window_seconds)
        while queue and queue[0].local_received_at_utc < cutoff:
            queue.popleft()

    def _compute_short_term_volatility_bps(
        self, events: List[MarketEvent], window_seconds: float = 30.0
    ) -> Optional[float]:
        """Compute realized mid-price return standard deviation in basis points over window."""
        if len(events) < 3:
            return None

        mids = [float(e.mid_price) for e in events if e.mid_price is not None and e.mid_price > 0]
        if len(mids) < 3:
            return None

        # Log returns
        returns = [math.log(mids[i] / mids[i - 1]) for i in range(1, len(mids))]
        if not returns:
            return None

        mean_r = sum(returns) / len(returns)
        variance = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance)
        return round(std_dev * 10000.0, 2)  # In basis points (bps)

    def align_event(
        self,
        news_event: NewsEvent,
        post_event_stream: Optional[List[MarketEvent]] = None,
    ) -> AlignedEventRecord:
        """
        Execute as-of join associating news event with contemporaneous market state.

        Args:
            news_event: The incoming or replayed news event.
            post_event_stream: Optional chronological market events arriving after decision time.

        Returns:
            AlignedEventRecord with guaranteed zero look-ahead bias.
        """
        t0_mono = get_monotonic_ns()
        tau_decision = news_event.local_received_at_utc
        session_status = get_market_session_status(tau_decision)

        quality_flags: List[str] = []
        if session_status == MarketSessionStatus.CLOSED:
            quality_flags.append("OUT_OF_HOURS")

        pre_contexts: Dict[str, PreEventMarketContext] = {}
        forward_outcomes: Dict[str, Dict[str, ForwardHorizonOutcome]] = {}

        # Default instruments: from event mappings, or fallback to NIFTY benchmark
        instruments = news_event.instrument_mappings
        if not instruments:
            from backend.alpha.instruments import get_instrument_master
            im = get_instrument_master()
            instruments = im.resolve_event_mapping(
                entity_id=news_event.entity_ids[0] if news_event.entity_ids else None,
                event_family="GENERAL",
            )

        for mapping in instruments:
            inst_id = mapping.instrument_id
            history = self._market_history.get(inst_id, deque())

            # 1. As-Of Join: Find latest market event at or before tau_decision
            # Filter history strictly <= tau_decision
            valid_pre = [m for m in history if m.local_received_at_utc <= tau_decision]

            if not valid_pre:
                # No market data available prior to event
                pre_contexts[inst_id] = PreEventMarketContext(
                    instrument_id=inst_id,
                    relevance=mapping.relevance,
                    session_state=session_status,
                    is_stale=True,
                )
                quality_flags.append("MISSING_MARKET_DATA")
                continue

            latest_m = valid_pre[-1]

            # CRITICAL ASSERTION: Zero look-ahead bias enforcement
            assert latest_m.local_received_at_utc <= tau_decision, (
                f"FATAL LOOK-AHEAD VIOLATION: Market event {latest_m.local_received_at_utc} "
                f"> News arrival {tau_decision}"
            )

            freshness_sec = (tau_decision - latest_m.local_received_at_utc).total_seconds()
            freshness_ms = freshness_sec * 1000.0
            is_stale = freshness_sec > self.stale_quote_threshold_seconds

            if is_stale and session_status == MarketSessionStatus.OPEN:
                quality_flags.append("STALE_MARKET")

            # Compute micro-volatility over [-30s, 0]
            vol_cutoff = tau_decision - timedelta(seconds=30.0)
            vol_events = [m for m in valid_pre if m.local_received_at_utc >= vol_cutoff]
            vol_bps = self._compute_short_term_volatility_bps(vol_events)

            pre_ctx = PreEventMarketContext(
                instrument_id=inst_id,
                relevance=mapping.relevance,
                market_observed_at_utc=latest_m.local_received_at_utc,
                bid_price=latest_m.bid_price,
                ask_price=latest_m.ask_price,
                mid_price=latest_m.mid_price,
                spread=latest_m.spread,
                bid_quantity=latest_m.bid_quantity,
                ask_quantity=latest_m.ask_quantity,
                last_traded_price=latest_m.last_traded_price,
                last_traded_quantity=latest_m.last_traded_quantity,
                feed_freshness_ms=round(freshness_ms, 2),
                short_term_volatility_bps=vol_bps,
                session_state=session_status,
                is_stale=is_stale,
            )
            pre_contexts[inst_id] = pre_ctx

            # 2. Forward Outcome Observations (Post-event horizons)
            forward_outcomes[inst_id] = {}
            p_base = float(latest_m.mid_price) if latest_m.mid_price else None

            # Gather post-event stream for this instrument
            inst_post: List[MarketEvent] = []
            if post_event_stream:
                inst_post = [
                    m for m in post_event_stream
                    if m.instrument_id == inst_id and m.local_received_at_utc > tau_decision
                ]

            for h_label, h_sec in HORIZON_MAP.items():
                target_t = tau_decision + timedelta(seconds=h_sec)

                if session_status == MarketSessionStatus.CLOSED:
                    forward_outcomes[inst_id][h_label] = ForwardHorizonOutcome(
                        horizon_label=h_label,
                        horizon_seconds=h_sec,
                        target_timestamp_utc=target_t,
                        status="MARKET_CLOSED",
                    )
                    continue

                # Find first market observation at or after target_t
                future_matches = [m for m in inst_post if m.local_received_at_utc >= target_t]

                if not future_matches:
                    forward_outcomes[inst_id][h_label] = ForwardHorizonOutcome(
                        horizon_label=h_label,
                        horizon_seconds=h_sec,
                        target_timestamp_utc=target_t,
                        status="MISSING_DATA",
                    )
                    continue

                m_post = future_matches[0]
                p_future = float(m_post.mid_price) if m_post.mid_price else None
                ret_bps = None
                if p_base and p_future and p_base > 0:
                    ret_bps = round(((p_future - p_base) / p_base) * 10000.0, 2)

                forward_outcomes[inst_id][h_label] = ForwardHorizonOutcome(
                    horizon_label=h_label,
                    horizon_seconds=h_sec,
                    target_timestamp_utc=target_t,
                    observed_timestamp_utc=m_post.local_received_at_utc,
                    mid_price=m_post.mid_price,
                    forward_return_bps=ret_bps,
                    status="VALID",
                )

        duration_ms = (get_monotonic_ns() - t0_mono) / 1_000_000.0
        final_quality = "CLEAN" if not quality_flags else "_".join(sorted(set(quality_flags)))

        return AlignedEventRecord(
            news_event_id=news_event.event_id,
            headline=news_event.headline,
            entity_ids=news_event.entity_ids,
            published_at_utc=news_event.published_at_utc,
            provider_observed_at_utc=news_event.provider_observed_at_utc,
            decision_timestamp_utc=tau_decision,
            aligned_at_utc=get_current_utc(),
            alignment_duration_ms=round(duration_ms, 3),
            pre_event_context=pre_contexts,
            forward_outcomes=forward_outcomes,
            quality_flag=final_quality,
        )
