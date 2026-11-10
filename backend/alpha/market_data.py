"""Provider-Neutral Market-Data Stream Scaffolding, Feed Integrity & Decoders."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import logging
import struct
import time
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from backend.alpha.instruments import get_instrument_master
from backend.alpha.schemas import DepthLevel, MarketEvent, MarketMessageType
from backend.alpha.timestamps import (
    LocalTimestamp,
    get_current_utc,
    get_monotonic_ns,
    to_utc_iso,
)

logger = logging.getLogger("tickerwatch.alpha.market_data")


class FeedStatus(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    AUTHENTICATING = "AUTHENTICATING"
    STREAMING = "STREAMING"
    STALE = "STALE"
    RECONNECTING = "RECONNECTING"
    ERROR = "ERROR"


class MarketDataSubscription(BaseModel):
    """Configuration-driven instrument subscription request."""
    instrument_id: str
    contract_token: Optional[str] = None
    mode: str = Field(default="FULL", description="LTP, QUOTE, or FULL (Level 2 5-depth)")


class FeedIntegrityAnomaly(BaseModel):
    """Recorded integrity anomaly in the market feed stream."""
    anomaly_type: str  # GAP, DUPLICATE, OUT_OF_ORDER, STALE_FEED
    instrument_id: str
    expected_sequence: Optional[int] = None
    observed_sequence: Optional[int] = None
    gap_size: Optional[int] = None
    timestamp_utc: datetime = Field(default_factory=get_current_utc)
    details: str


class FeedIntegrityMonitor:
    """
    Monitors message continuity, gap detection, duplicate suppression, and feed staleness.
    Never silently treats a sequence gap as continuous order book state.
    """

    def __init__(self, stale_timeout_seconds: float = 5.0) -> None:
        self.stale_timeout_seconds = stale_timeout_seconds
        self._last_sequences: Dict[str, int] = {}
        self._last_exchange_times: Dict[str, datetime] = {}
        self._last_message_at_utc: Optional[datetime] = None
        self._last_monotonic_ns: int = get_monotonic_ns()

        self.anomalies: List[FeedIntegrityAnomaly] = []
        self.total_messages: int = 0
        self.gap_count: int = 0
        self.duplicate_count: int = 0
        self.out_of_order_count: int = 0
        self.needs_resync: Dict[str, bool] = {}

    def inspect_packet(
        self,
        instrument_id: str,
        sequence_number: Optional[int],
        exchange_timestamp: Optional[datetime],
    ) -> Tuple[bool, Optional[FeedIntegrityAnomaly]]:
        """
        Verify sequence continuity and timestamp monotonicity.
        Returns:
            Tuple of (is_valid, anomaly_or_none)
        """
        now_utc = get_current_utc()
        self._last_message_at_utc = now_utc
        self._last_monotonic_ns = get_monotonic_ns()
        self.total_messages += 1

        anomaly: Optional[FeedIntegrityAnomaly] = None

        # 1. Sequence Continuity (where supported)
        if sequence_number is not None:
            last_seq = self._last_sequences.get(instrument_id)
            if last_seq is not None:
                if sequence_number == last_seq:
                    # Duplicate
                    self.duplicate_count += 1
                    anomaly = FeedIntegrityAnomaly(
                        anomaly_type="DUPLICATE",
                        instrument_id=instrument_id,
                        expected_sequence=last_seq + 1,
                        observed_sequence=sequence_number,
                        details=f"Duplicate packet with sequence {sequence_number}",
                    )
                    self.anomalies.append(anomaly)
                    return False, anomaly
                elif sequence_number < last_seq:
                    # Out-of-order sequence
                    self.out_of_order_count += 1
                    anomaly = FeedIntegrityAnomaly(
                        anomaly_type="OUT_OF_ORDER",
                        instrument_id=instrument_id,
                        expected_sequence=last_seq + 1,
                        observed_sequence=sequence_number,
                        details=f"Packet arrived out of order: got {sequence_number}, last was {last_seq}",
                    )
                    self.anomalies.append(anomaly)
                    return False, anomaly
                elif sequence_number > last_seq + 1:
                    # Sequence GAP detected!
                    gap = sequence_number - (last_seq + 1)
                    self.gap_count += 1
                    self.needs_resync[instrument_id] = True
                    anomaly = FeedIntegrityAnomaly(
                        anomaly_type="GAP",
                        instrument_id=instrument_id,
                        expected_sequence=last_seq + 1,
                        observed_sequence=sequence_number,
                        gap_size=gap,
                        details=f"Sequence GAP detected: missed {gap} messages ({last_seq + 1} to {sequence_number - 1})",
                    )
                    self.anomalies.append(anomaly)
                    self._last_sequences[instrument_id] = sequence_number
                    return False, anomaly

            self._last_sequences[instrument_id] = sequence_number

        # 2. Exchange Timestamp Monotonicity Check
        if exchange_timestamp is not None:
            last_et = self._last_exchange_times.get(instrument_id)
            if last_et is not None and exchange_timestamp < last_et:
                self.out_of_order_count += 1
                anomaly = FeedIntegrityAnomaly(
                    anomaly_type="OUT_OF_ORDER",
                    instrument_id=instrument_id,
                    details=f"Exchange timestamp moved backwards: {exchange_timestamp} < {last_et}",
                )
                self.anomalies.append(anomaly)
                return False, anomaly
            self._last_exchange_times[instrument_id] = exchange_timestamp

        return True, None

    def is_stale(self) -> bool:
        """Check if feed has stalled beyond configured timeout."""
        if self._last_message_at_utc is None:
            return False
        elapsed_sec = (get_monotonic_ns() - self._last_monotonic_ns) / 1_000_000_000.0
        return elapsed_sec > self.stale_timeout_seconds

    def acknowledge_resync(self, instrument_id: str) -> None:
        """Clear resync requirement after a fresh book snapshot."""
        self.needs_resync[instrument_id] = False


class BaseMarketDataProvider:
    """Abstract interface for streaming and replaying market data feeds."""

    def __init__(self, provider_id: str, stale_timeout_seconds: float = 5.0) -> None:
        self.provider_id = provider_id
        self.status = FeedStatus.DISCONNECTED
        self.integrity_monitor = FeedIntegrityMonitor(stale_timeout_seconds=stale_timeout_seconds)
        self.subscriptions: Dict[str, MarketDataSubscription] = {}
        self.instrument_master = get_instrument_master()

    async def connect(self) -> None:
        raise NotImplementedError

    async def disconnect(self) -> None:
        raise NotImplementedError

    async def subscribe(self, subscriptions: List[MarketDataSubscription]) -> None:
        for sub in subscriptions:
            self.subscriptions[sub.instrument_id] = sub

    async def stream_events(self) -> AsyncGenerator[MarketEvent, None]:
        raise NotImplementedError

    def get_integrity_summary(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "status": self.status.value,
            "total_messages": self.integrity_monitor.total_messages,
            "gap_count": self.integrity_monitor.gap_count,
            "duplicate_count": self.integrity_monitor.duplicate_count,
            "out_of_order_count": self.integrity_monitor.out_of_order_count,
            "is_stale": self.integrity_monitor.is_stale(),
            "anomalies_recorded": len(self.integrity_monitor.anomalies),
        }


class MockStreamingMarketDataProvider(BaseMarketDataProvider):
    """
    Mock streaming market data provider for offline testing and fault injection.
    Supports controlled injection of sequence gaps, duplicates, out-of-order packets,
    disconnects, and stale feed pauses.
    """

    def __init__(
        self,
        provider_id: str = "MOCK_STREAM",
        stale_timeout_seconds: float = 2.0,
    ) -> None:
        super().__init__(provider_id=provider_id, stale_timeout_seconds=stale_timeout_seconds)
        self._queue: asyncio.Queue[Optional[MarketEvent]] = asyncio.Queue(maxsize=1000)
        self._running = False

    async def connect(self) -> None:
        self.status = FeedStatus.STREAMING
        self._running = True

    async def disconnect(self) -> None:
        self.status = FeedStatus.DISCONNECTED
        self._running = False
        await self._queue.put(None)

    async def inject_event(self, event: MarketEvent) -> None:
        """Inject a test event into the stream."""
        await self._queue.put(event)

    async def stream_events(self) -> AsyncGenerator[MarketEvent, None]:
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=0.5)
                if event is None:
                    break

                # Run integrity verification
                is_valid, anomaly = self.integrity_monitor.inspect_packet(
                    instrument_id=event.instrument_id,
                    sequence_number=event.sequence_number,
                    exchange_timestamp=event.exchange_timestamp_utc,
                )

                yield event
            except asyncio.TimeoutError:
                if self.integrity_monitor.is_stale():
                    self.status = FeedStatus.STALE
                continue


class KiteConnectBinaryDecoder:
    """
    Decodes binary WebSocket quote packets following the official Zerodha Kite Connect specification.
    Packet Structure:
    - First 2 bytes: Number of packet segments (N).
    - For each packet:
      - 2 bytes: Length of packet (L).
      - L bytes: Packet payload.
        - L = 8: LTP packet (4 bytes token, 4 bytes LTP / 100).
        - L = 28/32: Quote packet (Token, LTP, LTT, Volume, etc.).
        - L = 184: Full Level 2 5-depth quote packet with bid/ask books.
    """

    def __init__(self, token_to_instrument: Dict[str, str]) -> None:
        self.token_to_instrument = token_to_instrument

    def decode_packet(self, data: bytes) -> List[MarketEvent]:
        """Decode raw binary buffer from Kite WebSocket into canonical MarketEvents."""
        if len(data) < 2:
            return []

        events: List[MarketEvent] = []
        now_utc = get_current_utc()
        now_mono = get_monotonic_ns()

        num_packets = struct.unpack(">H", data[:2])[0]
        offset = 2

        for _ in range(num_packets):
            if offset + 2 > len(data):
                break
            pkt_len = struct.unpack(">H", data[offset : offset + 2])[0]
            offset += 2

            if offset + pkt_len > len(data):
                break

            pkt_bytes = data[offset : offset + pkt_len]
            offset += pkt_len

            ev = self._parse_single_packet(pkt_len, pkt_bytes, now_utc, now_mono)
            if ev:
                events.append(ev)

        return events

    def _parse_single_packet(
        self, pkt_len: int, payload: bytes, now_utc: datetime, now_mono: int
    ) -> Optional[MarketEvent]:
        if pkt_len < 8:
            return None

        instrument_token = str(struct.unpack(">I", payload[:4])[0])
        canonical_inst = self.token_to_instrument.get(
            instrument_token, f"NSE:TOKEN_{instrument_token}:EQ"
        )

        # 1. LTP Mode (8 bytes)
        if pkt_len == 8:
            raw_ltp = struct.unpack(">I", payload[4:8])[0]
            ltp = Decimal(raw_ltp) / Decimal("100")
            return MarketEvent(
                provider="ZERODHA_KITE",
                venue="NSE",
                instrument_id=canonical_inst,
                contract_id=instrument_token,
                message_type=MarketMessageType.QUOTE,
                local_received_at_utc=now_utc,
                local_monotonic_ns=now_mono,
                last_traded_price=ltp,
                is_snapshot=False,
            )

        # 2. Full Mode (184 bytes - Level 2 5-depth)
        elif pkt_len == 184:
            # Token (4B), LTP (4B), LastQty (4B), AvgPrice (4B), Volume (4B), BuyQty (4B), SellQty (4B), Open (4B), High (4B), Low (4B), Close (4B)
            (
                token,
                raw_ltp,
                last_qty,
                avg_price,
                vol,
                buy_qty,
                sell_qty,
                o,
                h,
                l,
                c,
            ) = struct.unpack(">IIIIIIIIIII", payload[:44])

            ltp = Decimal(raw_ltp) / Decimal("100")

            # Timestamp (Last Traded Time) at byte 44 (4 bytes unix timestamp)
            ltt_epoch = struct.unpack(">I", payload[44:48])[0]
            exchange_ts = datetime.fromtimestamp(ltt_epoch, tz=timezone.utc) if ltt_epoch > 0 else None

            # Market depth: 10 entries of 12 bytes each (5 bids, 5 asks) starting at offset 64
            # Each depth entry: Quantity (4B), Price (4B / 100), Orders (2B), Reserved (2B)
            depth_bids: List[DepthLevel] = []
            depth_asks: List[DepthLevel] = []

            depth_offset = 64
            for i in range(10):
                d_qty, d_price, d_orders, _ = struct.unpack(">IIHH", payload[depth_offset : depth_offset + 12])
                depth_offset += 12
                level = DepthLevel(
                    price=Decimal(d_price) / Decimal("100"),
                    quantity=d_qty,
                    orders_count=d_orders,
                )
                if i < 5:
                    depth_bids.append(level)
                else:
                    depth_asks.append(level)

            best_bid_price = depth_bids[0].price if depth_bids else None
            best_bid_qty = depth_bids[0].quantity if depth_bids else None
            best_ask_price = depth_asks[0].price if depth_asks else None
            best_ask_qty = depth_asks[0].quantity if depth_asks else None

            return MarketEvent(
                provider="ZERODHA_KITE",
                venue="NSE",
                instrument_id=canonical_inst,
                contract_id=instrument_token,
                message_type=MarketMessageType.BOOK_SNAPSHOT,
                exchange_timestamp_utc=exchange_ts,
                local_received_at_utc=now_utc,
                local_monotonic_ns=now_mono,
                bid_price=best_bid_price,
                bid_quantity=best_bid_qty,
                ask_price=best_ask_price,
                ask_quantity=best_ask_qty,
                depth_bids=depth_bids,
                depth_asks=depth_asks,
                last_traded_price=ltp,
                last_traded_quantity=last_qty,
                cumulative_volume=vol,
                is_snapshot=True,
            )

        return None
