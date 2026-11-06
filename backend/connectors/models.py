"""Canonical data models for connectors and ingestion pipeline."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConnectorStatus(str, Enum):
    """Health states for connectors."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    AUTH_ERROR = "AUTH_ERROR"
    DISABLED = "DISABLED"


class SourceItem(BaseModel):
    """
    Canonical, normalised financial information item emitted by all connectors.
    Represents an atomic news article, SEC filing, or exchange announcement.
    """
    model_config = ConfigDict(extra="ignore")

    connector_id: str = Field(..., description="Unique connector identifier (e.g., 'rss_yahoo_finance', 'sec_edgar')")
    source_item_id: str = Field(..., description="Stable provider-specific or derived identifier for the item")
    publisher: str = Field(..., description="Name of the publisher or regulatory body")
    source_type: str = Field(..., description="Type of source, e.g. 'rss', 'sec_filing', 'exchange_announcement'")
    title: str = Field(..., description="Title or headline of the item")
    url: str = Field(..., description="Canonical URL to original source item")

    published_at_utc: Optional[datetime] = Field(
        default=None,
        description="Normalised UTC publication timestamp, or None if unavailable. Never fabricated."
    )
    published_at_original: Optional[str] = Field(
        default=None,
        description="Raw publication date string as provided by source"
    )
    observed_at_utc: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when TickerWatch fetched and observed the item in UTC"
    )
    updated_at_utc: Optional[datetime] = Field(
        default=None,
        description="Normalised UTC update timestamp if modified post-publication"
    )

    content_text: Optional[str] = Field(
        default=None,
        description="Clean snippet, summary, or full text if permitted"
    )
    content_hash: str = Field(
        ...,
        description="Deterministic SHA-256 hash of conservative normalised content for deduplication"
    )
    raw_metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Raw source metadata preserved for audit, provenance, and debugging"
    )
    language: Optional[str] = Field(
        default="en",
        description="ISO language code if known"
    )
    tickers: List[str] = Field(
        default_factory=list,
        description="Associated equity tickers, normalised to uppercase"
    )

    @field_validator("tickers")
    @classmethod
    def normalise_tickers(cls, v: List[str]) -> List[str]:
        # Normalise uppercase and deduplicate while maintaining order
        seen = set()
        clean = []
        for t in v:
            t_clean = t.strip().upper()
            if t_clean and t_clean not in seen:
                seen.add(t_clean)
                clean.append(t_clean)
        return clean

    @field_validator("published_at_utc", "observed_at_utc", "updated_at_utc")
    @classmethod
    def ensure_utc(cls, dt: Optional[datetime]) -> Optional[datetime]:
        if dt is None:
            return None
        if dt.tzinfo is None:
            # Assume naive datetime is UTC
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)


class FetchBatch(BaseModel):
    """Batch of items returned by a connector fetch operation."""
    model_config = ConfigDict(extra="ignore")

    connector_id: str
    items: List[SourceItem] = Field(default_factory=list)
    next_cursor: Optional[str] = None
    fetched_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_status: str = Field(default="OK")
    raw_count: int = 0
    is_exhausted: bool = False


class ConnectorHealth(BaseModel):
    """Observable health metrics and operational state for a connector."""
    model_config = ConfigDict(extra="ignore")

    connector_id: str
    name: str
    enabled: bool
    status: ConnectorStatus
    last_attempted_at_utc: Optional[datetime] = None
    last_successful_at_utc: Optional[datetime] = None
    last_observed_item_at_utc: Optional[datetime] = None
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    next_poll_at_utc: Optional[datetime] = None
    poll_interval_seconds: int = 300
