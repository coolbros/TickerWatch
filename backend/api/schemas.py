"""FastAPI request and response schemas."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    timestamp_utc: str
    connectors_count: int
    connectors: List[Dict[str, Any]]


class ArticleResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    connector_id: str
    source_item_id: str
    publisher: str
    source_type: str
    title: str
    url: str
    published_at_utc: Optional[str] = None
    published_at_original: Optional[str] = None
    observed_at_utc: str
    updated_at_utc: Optional[str] = None
    content_hash: str
    snippet: Optional[str] = None
    language: Optional[str] = "en"
    tickers: List[str] = []
    created_at_utc: str


class IngestionStatsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total_articles: int
    articles_by_connector: Dict[str, int]
    total_ingestion_attempts: int
    total_fetched: int
    total_inserted: int
    total_skipped: int
    total_failed: int
    dead_letter_count: int


class TriggerResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    connector_id: str
    status: str
    fetched: int
    inserted: int
    skipped: int
    failed: int
    duration_ms: float
    error: Optional[str] = None
