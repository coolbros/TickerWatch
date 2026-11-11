"""Phase 6 Real-Time Market-Data Adapters, Resilience Runner & Disabled Scaffolds (WP-B).

Extends Phase 2 `BaseMarketDataProvider`, `FeedIntegrityMonitor`, and `KiteConnectBinaryDecoder`:
- Enforces credential checks without exposing secrets in logs or errors.
- Retains clearly disabled adapters when broker/vendor subscriptions are absent
  (never presenting mocked feeds as live).
- Implements WebSocket/feed reconnects with exponential backoff, heartbeat monitoring,
  sequence-gap detection -> explicit snapshot resync callbacks, duplicate/out-of-order
  tracking, bounded queue backpressure & overflow telemetry, and checkpoint persistence.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import os
import random
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.alpha.market_data import (
    BaseMarketDataProvider,
    FeedStatus,
    KiteConnectBinaryDecoder,
    MarketDataSubscription,
)
from backend.alpha.schemas import MarketEvent
from backend.alpha.timestamps import get_current_utc, get_monotonic_ns

logger = logging.getLogger("tickerwatch.alpha.live_market")


class ProviderAuthError(PermissionError):
    """Authentication/credential failure guaranteed never to leak raw credential values."""


class MarketFeedTelemetry(BaseModel):
    """Operational stream metrics emitted by `ResilientMarketFeedRunner`."""

    provider_id: str
    status: str
    reconnect_attempts: int = 0
    heartbeat_timeouts: int = 0
    resync_requests: int = 0
    events_received: int = 0
    events_dispatched: int = 0
    queue_overflow_drops: int = 0
    gap_count: int = 0
    duplicate_count: int = 0
    out_of_order_count: int = 0
    last_sequence_by_instrument: Dict[str, int] = Field(default_factory=dict)
    last_checkpoint_utc: Optional[str] = None
    last_error: Optional[str] = None


class ZerodhaKiteLiveAdapter(BaseMarketDataProvider):
    """
    Authorized Zerodha Kite Connect WebSocket adapter extending Phase 2 `KiteConnectBinaryDecoder`.
    Remains cleanly `DISCONNECTED` / disabled unless valid `api_key` and `access_token`
    are supplied (never emits mocked data as live). Supports an injected async `frame_source`
    for deterministic offline binary packet & disconnect testing.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        access_token: Optional[str] = None,
        token_to_instrument: Optional[Dict[str, str]] = None,
        stale_timeout_seconds: float = 5.0,
        frame_source_factory: Optional[Callable[[], AsyncGenerator[bytes, None]]] = None,
    ) -> None:
        super().__init__(provider_id="ZERODHA_KITE", stale_timeout_seconds=stale_timeout_seconds)
        self._api_key = (api_key if api_key is not None else os.environ.get("KITE_API_KEY", "")).strip()
        self._access_token = (access_token if access_token is not None else os.environ.get("KITE_ACCESS_TOKEN", "")).strip()
        self.decoder = KiteConnectBinaryDecoder(token_to_instrument or {})
        self._frame_source_factory = frame_source_factory
        self._active_stream: Optional[AsyncGenerator[bytes, None]] = None
        self._connected = False
        self.last_error: Optional[str] = (
            None if self.has_credentials else "DISABLED: KITE_API_KEY / KITE_ACCESS_TOKEN not configured"
        )

    @property
    def has_credentials(self) -> bool:
        return bool(self._api_key and self._access_token)

    def _safe_error(self, msg: str) -> str:
        safe = msg
        for sec in (self._api_key, self._access_token):
            if sec and len(sec) >= 3:
                safe = safe.replace(sec, "***REDACTED***")
        return safe

    async def connect(self) -> None:
        if not self.has_credentials:
            self.status = FeedStatus.ERROR
            self.last_error = "Authentication blocked: missing KITE_API_KEY or KITE_ACCESS_TOKEN"
            raise ProviderAuthError(self.last_error)

        if self._frame_source_factory is None:
            # No live Kite binary WS transport available unless an explicit transport factory is wired
            self.status = FeedStatus.ERROR
            self.last_error = "Kite WebSocket transport unavailable in local offline environment"
            raise ConnectionError(self.last_error)

        try:
            self._active_stream = self._frame_source_factory()
            self._connected = True
            self.status = FeedStatus.STREAMING
            self.last_error = None
        except Exception as exc:
            self.status = FeedStatus.ERROR
            self.last_error = self._safe_error(str(exc))
            raise

    async def disconnect(self) -> None:
        self._connected = False
        self.status = FeedStatus.DISCONNECTED
        if self._active_stream is not None:
            try:
                await self._active_stream.aclose()
            except Exception:
                pass
            self._active_stream = None

    async def subscribe(self, subscriptions: List[MarketDataSubscription]) -> None:
        await super().subscribe(subscriptions)
        for sub in subscriptions:
            if sub.contract_token:
                self.decoder.token_to_instrument[sub.contract_token] = sub.instrument_id

    async def stream_events(self) -> AsyncGenerator[MarketEvent, None]:
        if not self._connected or self._active_stream is None:
            raise RuntimeError("ZerodhaKiteLiveAdapter must connect before streaming")

        async for raw_frame in self._active_stream:
            if not self._connected:
                break
            decoded = self.decoder.decode_packet(raw_frame)
            for ev in decoded:
                self.integrity_monitor.inspect_packet(
                    instrument_id=ev.instrument_id,
                    sequence_number=ev.sequence_number,
                    exchange_timestamp=ev.exchange_timestamp_utc,
                )
                yield ev


class DisabledMarketDataProvider(BaseMarketDataProvider):
    """Explicitly disabled market feed placeholder preserving interface parity without mocked data."""

    def __init__(self, provider_id: str, access_blocker: str) -> None:
        super().__init__(provider_id=provider_id, stale_timeout_seconds=5.0)
        self.enabled = False
        self.status = FeedStatus.DISCONNECTED
        self.access_blocker = access_blocker
        self.last_error = access_blocker

    async def connect(self) -> None:
        self.status = FeedStatus.ERROR
        raise ProviderAuthError(f"{self.provider_id} is disabled: {self.access_blocker}")

    async def disconnect(self) -> None:
        self.status = FeedStatus.DISCONNECTED

    async def stream_events(self) -> AsyncGenerator[MarketEvent, None]:
        if False:
            yield  # pragma: no cover
        raise ProviderAuthError(f"{self.provider_id} cannot stream: {self.access_blocker}")


class ResilientMarketFeedRunner:
    """
    Coordinates durable streaming consumption over any `BaseMarketDataProvider` with:
    - Bounded `asyncio.Queue` backpressure + explicit `queue_overflow_drops` telemetry.
    - Exponential backoff reconnects (`base_backoff * 2^attempt`).
    - Heartbeat staleness detection (`heartbeat_interval_seconds`).
    - Sequence-gap detection invoking `on_resync_required(instrument_id)`.
    - Persistent sequence checkpoint callback + clean graceful cancellation.
    """

    def __init__(
        self,
        provider: BaseMarketDataProvider,
        max_queue_size: int = 512,
        base_backoff_seconds: float = 0.05,
        max_backoff_seconds: float = 2.0,
        max_reconnects: int = 5,
        heartbeat_interval_seconds: float = 0.5,
        on_event: Optional[Callable[[MarketEvent], Awaitable[None] | None]] = None,
        on_resync_required: Optional[Callable[[str, int], Awaitable[None] | None]] = None,
        on_checkpoint: Optional[Callable[[Dict[str, int], datetime], Awaitable[None] | None]] = None,
        on_connection_event: Optional[Callable[[str, str], Awaitable[None] | None]] = None,
    ) -> None:
        self.provider = provider
        self.max_queue_size = max_queue_size
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.max_reconnects = max_reconnects
        self.heartbeat_interval_seconds = heartbeat_interval_seconds

        self.on_event = on_event
        self.on_resync_required = on_resync_required
        self.on_checkpoint = on_checkpoint
        self.on_connection_event = on_connection_event

        self._queue: asyncio.Queue[Optional[MarketEvent]] = asyncio.Queue(maxsize=max_queue_size)
        self._stop_requested = False
        self.telemetry = MarketFeedTelemetry(
            provider_id=provider.provider_id,
            status=provider.status.value,
        )

    async def _emit_conn(self, state: str, details: str) -> None:
        if self.on_connection_event is not None:
            res = self.on_connection_event(state, details)
            if asyncio.iscoroutine(res):
                await res

    def stop(self) -> None:
        self._stop_requested = True

    def _enqueue_with_backpressure(self, ev: MarketEvent) -> None:
        self.telemetry.events_received += 1
        if ev.sequence_number is not None:
            self.telemetry.last_sequence_by_instrument[ev.instrument_id] = ev.sequence_number
        if self._queue.full():
            self.telemetry.queue_overflow_drops += 1
            logger.warning(
                "Market feed queue full; dropping event with telemetry counter incremented",
                extra={"provider_id": self.provider.provider_id, "instrument_id": ev.instrument_id},
            )
            return
        self._queue.put_nowait(ev)

    async def _consumer_loop(self) -> None:
        while True:
            ev = await self._queue.get()
            if ev is None:
                self._queue.task_done()
                break
            try:
                if self.on_event is not None:
                    res = self.on_event(ev)
                    if asyncio.iscoroutine(res):
                        await res
                self.telemetry.events_dispatched += 1
            finally:
                self._queue.task_done()

    async def run(
        self,
        subscriptions: Optional[List[MarketDataSubscription]] = None,
        max_events: Optional[int] = None,
    ) -> MarketFeedTelemetry:
        consumer_task = asyncio.create_task(self._consumer_loop())
        attempt = 0
        try:
            while not self._stop_requested:
                try:
                    await self._emit_conn("CONNECTING", f"attempt={attempt}")
                    await self.provider.connect()
                    if subscriptions:
                        await self.provider.subscribe(subscriptions)
                    await self._emit_conn("STREAMING", "connected and subscribed")
                    attempt = 0  # Reset backoff upon clean connection

                    last_hb_ns = get_monotonic_ns()
                    async for ev in self.provider.stream_events():
                        now_ns = get_monotonic_ns()
                        if (now_ns - last_hb_ns) / 1_000_000_000.0 > self.heartbeat_interval_seconds:
                            # Heartbeat interval exceeded between arriving packets
                            if self.provider.integrity_monitor.is_stale():
                                self.telemetry.heartbeat_timeouts += 1
                        last_hb_ns = now_ns

                        self._enqueue_with_backpressure(ev)

                        # Check whether FeedIntegrityMonitor flagged a sequence gap needing snapshot resync
                        if self.provider.integrity_monitor.needs_resync.get(ev.instrument_id):
                            self.telemetry.resync_requests += 1
                            if self.on_resync_required is not None:
                                r = self.on_resync_required(
                                    ev.instrument_id,
                                    self.telemetry.last_sequence_by_instrument.get(ev.instrument_id, 0),
                                )
                                if asyncio.iscoroutine(r):
                                    await r
                            self.provider.integrity_monitor.acknowledge_resync(ev.instrument_id)

                        if self.telemetry.events_received % 25 == 0 and self.on_checkpoint is not None:
                            cp_ts = get_current_utc()
                            c_res = self.on_checkpoint(dict(self.telemetry.last_sequence_by_instrument), cp_ts)
                            if asyncio.iscoroutine(c_res):
                                await c_res
                            self.telemetry.last_checkpoint_utc = cp_ts.isoformat()

                        if max_events is not None and self.telemetry.events_received >= max_events:
                            self._stop_requested = True
                            break

                    if not self._stop_requested:
                        # Clean EOF from transport -> trigger reconnect check unless bounded
                        raise ConnectionAbortedError("Underlying market stream ended unexpectedly")

                except ProviderAuthError as auth_exc:
                    self.telemetry.last_error = str(auth_exc)
                    await self._emit_conn("AUTH_ERROR", str(auth_exc))
                    break
                except Exception as exc:
                    if self._stop_requested:
                        break
                    self.telemetry.last_error = str(exc)
                    if attempt >= self.max_reconnects:
                        await self._emit_conn("FAILED", f"Exhausted max_reconnects={self.max_reconnects}: {exc}")
                        break
                    attempt += 1
                    self.telemetry.reconnect_attempts += 1
                    delay = min(self.max_backoff_seconds, self.base_backoff_seconds * (2 ** (attempt - 1))) + random.uniform(0.0, 0.02)
                    await self._emit_conn("RECONNECTING", f"attempt={attempt}, sleep={delay:.3f}s")
                    await asyncio.sleep(delay)
        finally:
            await self.provider.disconnect()
            if self.on_checkpoint is not None and self.telemetry.last_sequence_by_instrument:
                cp_ts = get_current_utc()
                c_res = self.on_checkpoint(dict(self.telemetry.last_sequence_by_instrument), cp_ts)
                if asyncio.iscoroutine(c_res):
                    await c_res
                self.telemetry.last_checkpoint_utc = cp_ts.isoformat()
            await self._queue.put(None)
            await consumer_task

            self.telemetry.status = self.provider.status.value
            self.telemetry.gap_count = self.provider.integrity_monitor.gap_count
            self.telemetry.duplicate_count = self.provider.integrity_monitor.duplicate_count
            self.telemetry.out_of_order_count = self.provider.integrity_monitor.out_of_order_count
            await self._emit_conn("DISCONNECTED", "graceful shutdown complete")

        return self.telemetry
