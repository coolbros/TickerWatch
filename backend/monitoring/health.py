"""Health monitoring service synthesizing real-time connector metrics and database state."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.connectors.models import ConnectorHealth, ConnectorStatus
from backend.connectors.registry import ConnectorRegistry
from backend.database.repository import Repository
from backend.ingestion.normalizer import parse_timestamp_to_utc


class HealthService:
    """Provides consolidated health inspection across all ingestion connectors."""

    def __init__(self, registry: ConnectorRegistry, repository: Repository) -> None:
        self.registry = registry
        self.repo = repository

    async def get_connector_health(self, connector_id: str) -> Optional[ConnectorHealth]:
        """Fetch health for a specific connector."""
        connector = self.registry.get(connector_id)
        if not connector:
            return None

        # Fetch in-memory health
        health = await connector.health()

        # Merge with database state if available
        db_state = self.repo.get_connector_state(connector_id)
        if db_state:
            health.consecutive_failures = max(health.consecutive_failures, db_state.consecutive_failures)
            if db_state.last_error and not health.last_error:
                health.last_error = db_state.last_error
            if db_state.status == "AUTH_ERROR":
                health.status = ConnectorStatus.AUTH_ERROR
            elif db_state.status == "DEGRADED" and health.status == ConnectorStatus.HEALTHY:
                health.status = ConnectorStatus.DEGRADED

            if db_state.last_successful_persistence and not health.last_successful_persistence_at_utc:
                health.last_successful_persistence_at_utc = parse_timestamp_to_utc(db_state.last_successful_persistence)
            if db_state.last_observed_item and not health.last_observed_item_at_utc:
                health.last_observed_item_at_utc = parse_timestamp_to_utc(db_state.last_observed_item)
            if db_state.last_successful_fetch and not health.last_successful_at_utc:
                health.last_successful_at_utc = parse_timestamp_to_utc(db_state.last_successful_fetch)

        return health

    async def get_all_health(self) -> Dict[str, Any]:
        """Aggregate system-wide health status and connector details."""
        connectors_health: List[ConnectorHealth] = []
        overall_status = "HEALTHY"

        for connector in self.registry.list_all():
            h = await self.get_connector_health(connector.connector_id)
            if h:
                connectors_health.append(h)
                if h.status == ConnectorStatus.AUTH_ERROR:
                    overall_status = "AUTH_ERROR"
                elif h.status in (ConnectorStatus.DEGRADED, ConnectorStatus.STALE) and overall_status == "HEALTHY":
                    overall_status = "DEGRADED"

        return {
            "status": overall_status,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "connectors_count": len(connectors_health),
            "connectors": [h.model_dump() for h in connectors_health],
        }
