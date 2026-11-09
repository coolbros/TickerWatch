"""FastAPI route definitions for TickerWatch REST API."""

import json
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from backend.api.dashboard import DASHBOARD_HTML
from backend.api.schemas import (
    ArticleResponse,
    HealthResponse,
    IngestionStatsResponse,
    TriggerResponse,
)
from backend.connectors.registry import ConnectorRegistry
from backend.database.repository import Repository
from backend.ingestion.orchestrator import IngestionOrchestrator
from backend.monitoring.health import HealthService

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the interactive TickerWatch MVP dashboard."""
    return HTMLResponse(content=DASHBOARD_HTML)


def get_dependencies():
    # Will be overridden in main app
    pass


@router.get("/health", response_model=HealthResponse)
async def get_system_health(
    registry: ConnectorRegistry = Depends(lambda: router.registry),
    repo: Repository = Depends(lambda: router.repo),
):
    """Return consolidated health status across all connectors."""
    health_svc = HealthService(registry, repo)
    return await health_svc.get_all_health()


@router.get("/connectors")
async def list_connectors(
    registry: ConnectorRegistry = Depends(lambda: router.registry),
    repo: Repository = Depends(lambda: router.repo),
):
    """List all registered connectors and their current durable database state."""
    states = {s.connector_id: s for s in repo.get_all_connector_states()}
    result = []
    for c in registry.list_all():
        db_state = states.get(c.connector_id)
        result.append({
            "connector_id": c.connector_id,
            "name": c.name,
            "enabled": c.enabled,
            "poll_interval_seconds": c.poll_interval_seconds,
            "cursor": db_state.cursor if db_state else None,
            "status": db_state.status if db_state else c.status.value,
            "last_successful_fetch": db_state.last_successful_fetch if db_state else None,
            "consecutive_failures": db_state.consecutive_failures if db_state else 0,
            "last_error": db_state.last_error if db_state else None,
        })
    return {"connectors": result}


@router.get("/connectors/{connector_id}/health")
async def get_single_connector_health(
    connector_id: str,
    registry: ConnectorRegistry = Depends(lambda: router.registry),
    repo: Repository = Depends(lambda: router.repo),
):
    """Return detailed health metrics for a specific connector."""
    health_svc = HealthService(registry, repo)
    health = await health_svc.get_connector_health(connector_id)
    if not health:
        raise HTTPException(status_code=404, detail=f"Connector '{connector_id}' not found")
    return health.model_dump()


@router.get("/articles", response_model=List[ArticleResponse])
async def list_articles(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ticker: Optional[str] = Query(default=None),
    connector_id: Optional[str] = Query(default=None),
    repo: Repository = Depends(lambda: router.repo),
):
    """Retrieve paginated normalised articles with optional ticker or connector filtering."""
    rows = repo.get_articles(limit=limit, offset=offset, ticker=ticker, connector_id=connector_id)
    response = []
    for r in rows:
        try:
            tickers_list = json.loads(r.tickers)
        except Exception:
            tickers_list = []
        response.append(
            ArticleResponse(
                id=r.id,
                connector_id=r.connector_id,
                source_item_id=r.source_item_id,
                publisher=r.publisher,
                source_type=r.source_type,
                title=r.title,
                url=r.url,
                published_at_utc=r.published_at_utc,
                published_at_original=r.published_at_original,
                observed_at_utc=r.observed_at_utc,
                updated_at_utc=r.updated_at_utc,
                content_hash=r.content_hash,
                snippet=r.snippet,
                language=r.language,
                tickers=tickers_list,
                created_at_utc=r.created_at_utc,
            )
        )
    return response


@router.get("/articles/{article_id}", response_model=ArticleResponse)
async def get_article(
    article_id: int,
    repo: Repository = Depends(lambda: router.repo),
):
    """Retrieve an individual article by primary key ID."""
    row = repo.get_article_by_id(article_id)
    if not row:
        raise HTTPException(status_code=404, detail="Article not found")
    try:
        tickers_list = json.loads(row.tickers)
    except Exception:
        tickers_list = []
    return ArticleResponse(
        id=row.id,
        connector_id=row.connector_id,
        source_item_id=row.source_item_id,
        publisher=row.publisher,
        source_type=row.source_type,
        title=row.title,
        url=row.url,
        published_at_utc=row.published_at_utc,
        published_at_original=row.published_at_original,
        observed_at_utc=row.observed_at_utc,
        updated_at_utc=row.updated_at_utc,
        content_hash=row.content_hash,
        snippet=row.snippet,
        language=row.language,
        tickers=tickers_list,
        created_at_utc=row.created_at_utc,
    )


@router.get("/stats/ingestion", response_model=IngestionStatsResponse)
async def get_ingestion_stats(
    repo: Repository = Depends(lambda: router.repo),
):
    """Return high-level ingestion throughput and error statistics."""
    return repo.get_ingestion_stats()


@router.post("/connectors/{connector_id}/trigger", response_model=TriggerResponse)
async def trigger_connector_ingestion(
    connector_id: str,
    registry: ConnectorRegistry = Depends(lambda: router.registry),
    orchestrator: IngestionOrchestrator = Depends(lambda: router.orchestrator),
):
    """Manually invoke a single ingestion cycle for a connector."""
    connector = registry.get(connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail=f"Connector '{connector_id}' not found")
    stats = await orchestrator.ingest_connector(connector)
    return stats
