"""TickerWatch FastAPI Application Entrypoint."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from fastapi import FastAPI

from backend.api.routes import router
from backend.config import Settings, get_settings, setup_logging
from backend.connectors.registry import ConnectorRegistry
from backend.database.connection import apply_migrations
from backend.database.repository import Repository
from backend.ingestion.orchestrator import IngestionOrchestrator
from backend.ingestion.scheduler import IngestionScheduler

logger = logging.getLogger("tickerwatch.app")


def create_app(
    settings: Optional[Settings] = None,
    start_scheduler: Optional[bool] = None,
) -> FastAPI:
    """Application factory for TickerWatch API service."""
    app_settings = settings or get_settings()
    setup_logging(app_settings.log_level)

    # 1. Establish database schema and run migrations
    apply_migrations(app_settings.get_sqlite_path())

    # 2. Instantiate core infrastructure components
    repo = Repository(app_settings.get_sqlite_path())
    registry = ConnectorRegistry(app_settings)
    registry.load_from_config()

    orchestrator = IngestionOrchestrator(
        repository=repo,
        retry_policy=None,  # Uses default RetryPolicy
    )

    scheduler = IngestionScheduler(
        orchestrator=orchestrator,
        max_concurrency=app_settings.max_connector_concurrency,
    )

    effective_start_scheduler = (
        start_scheduler
        if start_scheduler is not None
        else app_settings.start_scheduler
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        logger.info(
            "TickerWatch ingestion service starting up",
            extra={"env": app_settings.app_env, "connectors": len(registry.list_all())}
        )
        # Start background polling tasks for enabled connectors only when configured
        if effective_start_scheduler:
            enabled = registry.list_enabled()
            if enabled:
                scheduler.start_connectors(enabled)
                logger.info("Autonomous scheduler started", extra={"active_connectors": [c.connector_id for c in enabled]})

        yield

        # Graceful shutdown
        logger.info("TickerWatch ingestion service shutting down")
        await scheduler.stop(timeout_seconds=5.0)

    app = FastAPI(
        title="TickerWatch",
        description="Local-First Financial Intelligence Ingestion Engine",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Inject dependencies onto router
    router.repo = repo
    router.registry = registry
    router.orchestrator = orchestrator
    router.scheduler = scheduler

    app.include_router(router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)
