"""Database row schemas and data transfer models."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


@dataclass
class ArticleRow:
    """Internal database representation of an article."""
    id: int
    connector_id: str
    source_item_id: str
    publisher: str
    source_type: str
    title: str
    url: str
    published_at_utc: Optional[str]
    published_at_original: Optional[str]
    observed_at_utc: str
    updated_at_utc: Optional[str]
    content_hash: str
    snippet: Optional[str]
    raw_metadata: str
    language: Optional[str]
    tickers: str
    created_at_utc: str


@dataclass
class ConnectorStateRow:
    """Internal database representation of connector cursor & health state."""
    connector_id: str
    cursor: Optional[str]
    last_successful_fetch: Optional[str]
    last_attempted_fetch: Optional[str]
    last_observed_item: Optional[str]
    last_successful_persistence: Optional[str]
    consecutive_failures: int
    status: str
    last_error: Optional[str]
    updated_at_utc: str


@dataclass
class IngestionAttemptRow:
    """Internal database representation of an ingestion run."""
    id: int
    connector_id: str
    start_time_utc: str
    completion_time_utc: str
    number_fetched: int
    number_inserted: int
    number_skipped: int
    number_failed: int
    retry_count: int
    error_details: Optional[str]
    duration_ms: float


@dataclass
class DeadLetterRow:
    """Internal database representation of an unprocessable item."""
    id: int
    connector_id: str
    source_item_id: Optional[str]
    error_category: str
    error_message: str
    raw_payload: Optional[str]
    retry_count: int
    first_failure_at_utc: str
    last_failure_at_utc: str
    retry_status: str
