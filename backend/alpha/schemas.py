"""Canonical Data Schemas for TickerWatch Alpha News & Market Data."""

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field, field_validator

from backend.alpha.instruments import InstrumentMappingRecord


class MarketMessageType(str, Enum):
    QUOTE = "QUOTE"                    # Top of book / BBO update
    TRADE = "TRADE"                    # Executed trade print
    BOOK_SNAPSHOT = "BOOK_SNAPSHOT"    # Full L2 depth snapshot (5 or 20 depth)
    BOOK_DELTA = "BOOK_DELTA"          # Incremental depth change
    SESSION_STATE = "SESSION_STATE"    # Pre-open, open, halt, close
    HEARTBEAT = "HEARTBEAT"            # Provider keepalive packet


class DepthLevel(BaseModel):
    """Single price-level entry in an order book."""
    price: Decimal = Field(..., description="Level price")
    quantity: int = Field(..., description="Aggregate shares at this price level")
    orders_count: Optional[int] = Field(None, description="Number of orders at this level if provided")

    @field_validator("price")
    @classmethod
    def validate_positive_price(cls, v: Decimal) -> Decimal:
        if v < Decimal("0"):
            raise ValueError("Depth price cannot be negative")
        return v

    @field_validator("quantity")
    @classmethod
    def validate_positive_qty(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Depth quantity cannot be negative")
        return v


class NewsEvent(BaseModel):
    """Canonical representation of a financial news event or regulatory disclosure."""

    event_id: str = Field(default_factory=lambda: f"news_evt_{uuid.uuid4().hex[:12]}")
    provider_id: str = Field(..., description="Originating connector ID, e.g. nse_corporate, sec_edgar")
    source_name: str = Field(..., description="Display name of source, e.g. NSE Corporate Filings")
    source_url: str = Field(..., description="Original publication link or reference")
    canonical_article_id: Optional[int] = Field(None, description="FK to SQLite articles.id if persisted")
    headline: str = Field(..., description="Sanitized headline or title")
    body_text: Optional[str] = Field(None, description="Body content or snippet if retained")
    published_at_utc: Optional[datetime] = Field(None, description="Reported publication time in UTC")
    provider_observed_at_utc: Optional[datetime] = Field(None, description="Provider observation time in UTC")
    local_received_at_utc: datetime = Field(..., description="Local socket ingress time in UTC")
    local_monotonic_ns: int = Field(..., description="Local monotonic clock at ingress (perf_counter_ns)")
    ingestion_completed_at_utc: Optional[datetime] = Field(None, description="Commit completion time in UTC")
    version: int = Field(default=1, description="Revision version counter")
    is_correction: bool = Field(default=False, description="True if amending an earlier disclosure")
    replaces_event_id: Optional[str] = Field(None, description="ID of earlier event amended by this record")
    language: str = Field(default="en", description="Language code")
    entity_ids: List[str] = Field(default_factory=list, description="Extracted company/entity keys")
    instrument_mappings: List[InstrumentMappingRecord] = Field(default_factory=list)
    content_hash: str = Field(..., description="Deterministic SHA-256 fingerprint")
    raw_payload_ref: Optional[str] = Field(None, description="Pointer to archived raw message")
    schema_version: str = Field(default="2.0.0", description="Contract schema version")


class MarketEvent(BaseModel):
    """Canonical representation of a normalized market tick, quote, or depth update."""

    event_id: str = Field(default_factory=lambda: f"mkt_evt_{uuid.uuid4().hex[:12]}")
    provider: str = Field(..., description="Data provider name, e.g. ZERODHA_KITE, TRUDATA, REPLAY")
    venue: str = Field(default="NSE", description="Exchange venue, e.g. NSE, BSE")
    segment: str = Field(default="EQ", description="Segment: EQ, FUTIDX, FUTSTK")
    instrument_id: str = Field(..., description="Canonical instrument key, e.g. NSE:NIFTY_FUT:CURRENT")
    contract_id: Optional[str] = Field(None, description="Vendor instrument token or symbol")
    message_type: MarketMessageType = Field(default=MarketMessageType.QUOTE)
    exchange_timestamp_utc: Optional[datetime] = Field(None, description="Exchange match-engine time in UTC")
    provider_timestamp_utc: Optional[datetime] = Field(None, description="Provider transmission time in UTC")
    local_received_at_utc: datetime = Field(..., description="Local socket ingress time in UTC")
    local_monotonic_ns: int = Field(..., description="Local monotonic clock at ingress (perf_counter_ns)")
    persisted_at_utc: Optional[datetime] = Field(None, description="Disk commit time in UTC")
    sequence_number: Optional[int] = Field(None, description="Monotonic sequence number if provided")
    bid_price: Optional[Decimal] = Field(None, description="Best bid price")
    bid_quantity: Optional[int] = Field(None, description="Best bid aggregate quantity")
    ask_price: Optional[Decimal] = Field(None, description="Best ask price")
    ask_quantity: Optional[int] = Field(None, description="Best ask aggregate quantity")
    depth_bids: List[DepthLevel] = Field(default_factory=list, description="Bid depth levels (top 5 or 20)")
    depth_asks: List[DepthLevel] = Field(default_factory=list, description="Ask depth levels (top 5 or 20)")
    last_traded_price: Optional[Decimal] = Field(None, description="Last traded price")
    last_traded_quantity: Optional[int] = Field(None, description="Quantity of last trade print")
    cumulative_volume: Optional[int] = Field(None, description="Day cumulative volume")
    is_snapshot: bool = Field(default=True, description="True if complete depth snapshot; False if delta")
    session_state: Optional[str] = Field(None, description="Market session status: OPEN, CLOSED, HALTED")
    raw_payload_ref: Optional[str] = Field(None, description="Pointer to archived raw frame")
    schema_version: str = Field(default="2.0.0", description="Contract schema version")

    @field_validator("bid_price", "ask_price", "last_traded_price")
    @classmethod
    def validate_positive_prices(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v < Decimal("0"):
            raise ValueError("Price cannot be negative")
        return v

    @field_validator("bid_quantity", "ask_quantity", "last_traded_quantity", "cumulative_volume")
    @classmethod
    def validate_positive_quantities(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 0:
            raise ValueError("Quantity cannot be negative")
        return v

    @property
    def spread(self) -> Optional[Decimal]:
        """Calculate top-of-book bid-ask spread."""
        if self.bid_price is not None and self.ask_price is not None:
            return self.ask_price - self.bid_price
        return None

    @property
    def mid_price(self) -> Optional[Decimal]:
        """Calculate arithmetic mid-price."""
        if self.bid_price is not None and self.ask_price is not None:
            return (self.bid_price + self.ask_price) / Decimal("2")
        return None

    @property
    def microprice(self) -> Optional[Decimal]:
        """Calculate volume-weighted microprice."""
        if (
            self.bid_price is not None
            and self.ask_price is not None
            and self.bid_quantity is not None
            and self.ask_quantity is not None
        ):
            total_qty = self.bid_quantity + self.ask_quantity
            if total_qty > 0:
                return (
                    Decimal(self.bid_price) * Decimal(self.ask_quantity)
                    + Decimal(self.ask_price) * Decimal(self.bid_quantity)
                ) / Decimal(total_qty)
        return self.mid_price


class QualityQuarantineRecord(BaseModel):
    """Traceable quarantine entry for malformed, corrupted or out-of-spec events."""

    quarantine_id: str = Field(default_factory=lambda: f"quar_{uuid.uuid4().hex[:12]}")
    source_type: str = Field(..., description="NEWS or MARKET_DATA")
    source_reference: str = Field(..., description="Original URL, packet index, or payload hash")
    quarantine_reason: str = Field(..., description="Specific violation rule description")
    raw_payload_snippet: str = Field(..., description="Truncated copy of raw payload for debugging")
    quarantined_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: str = Field(default="2.0.0")
