"""Integration tests validating error isolation across connectors and stale-feed detection."""

from datetime import datetime, timezone, timedelta
from pathlib import Path
import httpx
import pytest
import respx

from backend.connectors.models import ConnectorStatus
from backend.connectors.rss import RSSConnector
from backend.database.connection import apply_migrations
from backend.database.repository import Repository
from backend.ingestion.orchestrator import IngestionOrchestrator
from backend.ingestion.retry import RetryPolicy
from backend.monitoring.health import HealthService
from backend.connectors.registry import ConnectorRegistry

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def rss_xml():
    return (FIXTURES_DIR / "valid_rss2.xml").read_text(encoding="utf-8")


@pytest.mark.asyncio
@respx.mock
async def test_connector_error_isolation(tmp_path, rss_xml):
    """
    Acceptance Test 6:
    When Connector A fails repeatedly with HTTP 500 errors,
    Connector B continues operating completely unaffected.
    """
    db_file = tmp_path / "isolation.db"
    db_path = str(db_file)
    apply_migrations(db_path)

    # Connector A: permanently broken (HTTP 500)
    broken_url = "https://broken.example.com/rss.xml"
    respx.get(broken_url).mock(return_value=httpx.Response(500, text="Internal Server Error"))

    # Connector B: healthy
    healthy_url = "https://healthy.example.com/rss.xml"
    respx.get(healthy_url).mock(return_value=httpx.Response(200, text=rss_xml))

    repo = Repository(db_path)
    # Fast retry policy for test
    policy = RetryPolicy(max_retries=1, base_delay_seconds=0.01, jitter=False)
    orchestrator = IngestionOrchestrator(repository=repo, retry_policy=policy)

    conn_broken = RSSConnector(connector_id="broken_conn", feed_url=broken_url)
    conn_healthy = RSSConnector(connector_id="healthy_conn", feed_url=healthy_url)

    # Ingest broken connector: fails
    res_broken = await orchestrator.ingest_connector(conn_broken)
    assert res_broken["status"] == "FAILED"
    assert res_broken["failed"] == 1

    # Ingest healthy connector: must succeed completely
    res_healthy = await orchestrator.ingest_connector(conn_healthy)
    assert res_healthy["status"] == "SUCCESS"
    assert res_healthy["inserted"] == 2

    # Check health and database state
    broken_state = repo.get_connector_state("broken_conn")
    healthy_state = repo.get_connector_state("healthy_conn")

    assert broken_state.consecutive_failures == 1
    assert "500" in (broken_state.last_error or "")
    assert healthy_state.status == "HEALTHY"
    assert healthy_state.consecutive_failures == 0


@pytest.mark.asyncio
async def test_stale_feed_and_degraded_health_detection(tmp_path):
    """
    Acceptance Test 7:
    Simulate consecutive failures and extended silence to verify DEGRADED and STALE transitions.
    """
    db_file = tmp_path / "stale_test.db"
    db_path = str(db_file)
    apply_migrations(db_path)

    repo = Repository(db_path)
    registry = ConnectorRegistry()

    conn = RSSConnector(connector_id="stale_feed", feed_url="https://example.com/feed", poll_interval_seconds=60)
    registry.register(conn)

    health_svc = HealthService(registry=registry, repository=repo)

    # Initially healthy
    h1 = await health_svc.get_connector_health("stale_feed")
    assert h1.status == ConnectorStatus.HEALTHY

    # Simulate 3 consecutive failures -> DEGRADED
    conn.record_failure("Failure 1")
    conn.record_failure("Failure 2")
    conn.record_failure("Failure 3")
    h2 = await health_svc.get_connector_health("stale_feed")
    assert h2.status == ConnectorStatus.DEGRADED

    # Reset and simulate success long ago (> 3x poll interval) -> STALE
    conn.record_success(items_count=1)
    conn.last_successful_at_utc = datetime.now(timezone.utc) - timedelta(seconds=1200)
    h3 = await health_svc.get_connector_health("stale_feed")
    assert h3.status == ConnectorStatus.STALE
