"""Deterministic Historical Replay Engine for Market Microstructure & News."""

from datetime import datetime, timezone
import hashlib
import heapq
import json
import logging
from typing import Any, Dict, Generator, List, Optional, Tuple, Union
from pydantic import BaseModel, Field

from backend.alpha.alignment import AlignedEventRecord, TemporalAlignmentEngine
from backend.alpha.schemas import MarketEvent, NewsEvent
from backend.alpha.timestamps import get_monotonic_ns

logger = logging.getLogger("tickerwatch.alpha.replay")


class ReplayMetrics(BaseModel):
    """Execution telemetry from a deterministic replay run."""

    total_events_processed: int = 0
    news_events_processed: int = 0
    market_events_processed: int = 0
    alignments_generated: int = 0
    duration_seconds: float = 0.0
    throughput_eps: float = 0.0
    deterministic_sha256: str = ""
    replayed_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DeterministicReplayEngine:
    """
    Replays heterogeneous news and market streams in strict chronological order.
    Guarantees:
    - Same input dataset + configuration = identical aligned output and checksum.
    - Accurately simulates the arrival sequence observed by the live system.
    """

    def __init__(
        self,
        alignment_engine: Optional[TemporalAlignmentEngine] = None,
    ) -> None:
        self.alignment_engine = alignment_engine or TemporalAlignmentEngine()
        self.aligned_records: List[AlignedEventRecord] = []

    def replay_streams(
        self,
        news_stream: List[NewsEvent],
        market_stream: List[MarketEvent],
    ) -> Tuple[List[AlignedEventRecord], ReplayMetrics]:
        """
        Merge and replay news and market events in timestamp order.

        Args:
            news_stream: List of canonical NewsEvents
            market_stream: List of canonical MarketEvents

        Returns:
            Tuple of (aligned_records, replay_metrics)
        """
        t0_mono = get_monotonic_ns()
        self.aligned_records = []

        # Min-heap priority queue keyed by (local_received_at_utc, monotonic_ns, seq_id, event_obj)
        # Note: tie-breaker priority given to MarketEvent before NewsEvent if exact same timestamp
        heap: List[Tuple[datetime, int, int, Union[MarketEvent, NewsEvent]]] = []

        counter = 0
        for m in market_stream:
            # Type priority 0 for market
            heapq.heappush(heap, (m.local_received_at_utc, 0, counter, m))
            counter += 1

        for n in news_stream:
            # Type priority 1 for news
            heapq.heappush(heap, (n.local_received_at_utc, 1, counter, n))
            counter += 1

        market_count = 0
        news_count = 0

        # Maintain future market events for post-event forward outcome resolution
        sorted_market = sorted(market_stream, key=lambda x: x.local_received_at_utc)

        while heap:
            ts, priority, _, item = heapq.heappop(heap)

            if isinstance(item, MarketEvent):
                market_count += 1
                self.alignment_engine.add_market_event(item)

            elif isinstance(item, NewsEvent):
                news_count += 1
                # Forward observations are drawn from market stream arriving after news arrival
                aligned = self.alignment_engine.align_event(
                    news_event=item,
                    post_event_stream=sorted_market,
                )
                self.aligned_records.append(aligned)

        duration_sec = (get_monotonic_ns() - t0_mono) / 1_000_000_000.0
        total_events = market_count + news_count
        throughput = total_events / max(duration_sec, 0.0001)

        # Compute deterministic cryptographic hash of all generated alignments
        hasher = hashlib.sha256()
        for rec in self.aligned_records:
            # Hash canonical attributes
            summary_dict = {
                "news_id": rec.news_event_id,
                "decision_time": rec.decision_timestamp_utc.isoformat(),
                "pre_mids": {k: str(v.mid_price) for k, v in rec.pre_event_context.items()},
                "outcomes": {
                    k: {h: str(o.forward_return_bps) for h, o in horizons.items()}
                    for k, horizons in rec.forward_outcomes.items()
                },
            }
            hasher.update(json.dumps(summary_dict, sort_keys=True).encode("utf-8"))

        metrics = ReplayMetrics(
            total_events_processed=total_events,
            news_events_processed=news_count,
            market_events_processed=market_count,
            alignments_generated=len(self.aligned_records),
            duration_seconds=round(duration_sec, 4),
            throughput_eps=round(throughput, 1),
            deterministic_sha256=hasher.hexdigest(),
        )

        return self.aligned_records, metrics
