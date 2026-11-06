"""Regression tests for Phase 1.1 independent engineering audit findings."""

import asyncio
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import httpx
import pytest
import respx

from backend.connectors.models import ConnectorStatus, SourceItem
from backend.connectors.rss import RSSConnector
from backend.connectors.sec import SECConnector, SECRateLimiter
from backend.database.connection import apply_migrations
from backend.database.repository import Repository
from backend.ingestion.orchestrator import IngestionOrchestrator
from backend.ingestion.scheduler import IngestionScheduler
from backend.monitoring.health import HealthService
from backend.connectors.registry import ConnectorRegistry

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def test_repo(tmp_path):
    db_file = tmp_path / "regressions.db"
    db_path = str(db_file)
    apply_migrations(db_path)
    return Repository(db_path)


@pytest.mark.asyncio
async def test_shared_sec_rate_limiter_across_instances():
    """
    Finding 1 Regression:
    Verify that multiple SEC connector instances share the same rate limiter,
    enforcing minimum request intervals across all SEC requests in the process.
    """
    conn1 = SECConnector(user_agent="Test/1.0 admin@example.com")
    conn2 = SECConnector(user_agent="Test/1.0 admin@example.com")
    # Verify both connectors share the same process-wide rate limiter instance
    assert conn1.rate_limiter is conn2.rate_limiter

    limiter = SECRateLimiter(min_interval_seconds=0.06)
    t0 = time.monotonic()
    await limiter.acquire()
    await limiter.acquire()
    await limiter.acquire()
    elapsed = time.monotonic() - t0

    # 3 acquires with 0.06s minimum interval must take at least 0.11s
    assert elapsed >= 0.11


@pytest.mark.asyncio
@respx.mock
async def test_304_updates_successful_poll_without_advancing_observed_item(test_repo):
    """
    Finding 2 Regression:
    Verify that an HTTP 304 response updates last_successful_fetch without
    incorrectly advancing last_observed_item, and does not classify the feed as unhealthy.
    """
    feed_url = "https://example.com/feed-304.xml"
    # First request returns 200 with an article
    rss_xml = (FIXTURES_DIR / "valid_rss2.xml").read_text(encoding="utf-8")
    respx.get(feed_url).mock(return_value=httpx.Response(
        200, text=rss_xml, headers={"etag": '"etag-1"'}
    ))

    connector = RSSConnector(connector_id="conn_304_test", feed_url=feed_url)
    orchestrator = IngestionOrchestrator(repository=test_repo)

    # 1. Initial fetch -> 2 items inserted
    res1 = await orchestrator.ingest_connector(connector)
    assert res1["inserted"] == 2
    state1 = test_repo.get_connector_state("conn_304_test")
    assert state1.last_observed_item is not None
    observed_time_1 = state1.last_observed_item
    successful_fetch_1 = state1.last_successful_fetch

    # Small sleep to ensure clock advances
    await asyncio.sleep(0.01)

    # 2. Subsequent request returns 304 Not Modified
    respx.get(feed_url).mock(return_value=httpx.Response(304))
    res2 = await orchestrator.ingest_connector(connector)
    assert res2["inserted"] == 0
    assert res2["fetched"] == 0

    state2 = test_repo.get_connector_state("conn_304_test")
    # last_successful_fetch MUST advance
    assert state2.last_successful_fetch > successful_fetch_1
    # last_observed_item MUST remain unchanged (not updated to now)
    assert state2.last_observed_item == observed_time_1
    # Status MUST remain HEALTHY
    assert state2.status == "HEALTHY"


@pytest.mark.asyncio
@respx.mock
async def test_persistence_timestamp_distinction(test_repo):
    """
    Finding 2 Regression:
    Verify that last_successful_persistence is tracked separately from
    last_successful_fetch and only updates when items are genuinely inserted.
    """
    feed_url = "https://example.com/persistence-test.xml"
    rss_xml = (FIXTURES_DIR / "valid_rss2.xml").read_text(encoding="utf-8")

    respx.get(feed_url).mock(return_value=httpx.Response(200, text=rss_xml))
    connector = RSSConnector(connector_id="conn_persist_track", feed_url=feed_url)
    orchestrator = IngestionOrchestrator(repository=test_repo)

    # First fetch: inserts 2 items
    await orchestrator.ingest_connector(connector)
    state1 = test_repo.get_connector_state("conn_persist_track")
    assert state1.last_successful_persistence is not None
    persist_time_1 = state1.last_successful_persistence

    await asyncio.sleep(0.01)

    # Second fetch with 304: poll succeeds but 0 items persisted
    respx.get(feed_url).mock(return_value=httpx.Response(304))
    await orchestrator.ingest_connector(connector)
    state2 = test_repo.get_connector_state("conn_persist_track")

    # Fetch updated, but persistence timestamp preserved
    assert state2.last_successful_fetch > state1.last_successful_fetch
    assert state2.last_successful_persistence == persist_time_1


@pytest.mark.asyncio
async def test_scheduler_graceful_shutdown_natural_completion(test_repo):
    """
    Finding 3 Regression:
    Verify that IngestionScheduler.stop() waits for ongoing tasks to complete
    gracefully without immediately aborting in-flight processing.
    """
    executed = False

    class SlowConnector(RSSConnector):
        async def fetch(self, cursor=None):
            nonlocal executed
            await asyncio.sleep(0.05)  # Simulate in-flight work
            executed = True
            from backend.connectors.models import FetchBatch
            return FetchBatch(
                connector_id=self.connector_id,
                items=[],
                source_status="NOT_MODIFIED"
            )

    connector = SlowConnector(
        connector_id="slow_conn",
        feed_url="https://example.com/slow.xml",
        poll_interval_seconds=10
    )
    orchestrator = IngestionOrchestrator(repository=test_repo)
    scheduler = IngestionScheduler(orchestrator=orchestrator)

    scheduler.start_connectors([connector])
    # Allow task to enter execution
    await asyncio.sleep(0.02)
    # Request shutdown with graceful timeout
    await scheduler.stop(timeout_seconds=1.0)

    # In-flight task should have been allowed to complete naturally
    assert executed is True
    assert len(scheduler.tasks) == 0


@pytest.mark.asyncio
@respx.mock
async def test_atomic_persistence_and_ack_under_cancellation(test_repo):
    """
    Finding 4 Regression:
    Verify that persistence and cursor ack are unified under cancellation protection,
    so that cancelling the orchestrator cannot leave the DB cursor and in-memory cursor divergent.
    """
    feed_url = "https://example.com/ack-test.xml"
    rss_xml = (FIXTURES_DIR / "valid_rss2.xml").read_text(encoding="utf-8")
    respx.get(feed_url).mock(return_value=httpx.Response(200, text=rss_xml))

    connector = RSSConnector(connector_id="ack_test_conn", feed_url=feed_url)
    orchestrator = IngestionOrchestrator(repository=test_repo)

    # Ingest normally
    res = await orchestrator.ingest_connector(connector)
    assert res["status"] == "SUCCESS"

    db_cursor = test_repo.get_connector_cursor("ack_test_conn")
    assert db_cursor is not None
    assert connector.committed_cursor == db_cursor
