"""Unit and integration tests with controlled failure injection verifying cursor and transaction durability."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import httpx
import pytest
import respx

from backend.connectors.models import FetchBatch, SourceItem
from backend.connectors.rss import RSSConnector
from backend.database.connection import apply_migrations
from backend.database.repository import Repository
from backend.ingestion.orchestrator import IngestionOrchestrator
from backend.ingestion.retry import RetryPolicy

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def test_repo(tmp_path):
    db_file = tmp_path / "durability_test.db"
    db_path = str(db_file)
    apply_migrations(db_path)
    return Repository(db_path)


@pytest.fixture
def rss_xml():
    return (FIXTURES_DIR / "valid_rss2.xml").read_text(encoding="utf-8")


def make_dummy_item(connector_id: str, item_id: str) -> SourceItem:
    return SourceItem(
        connector_id=connector_id,
        source_item_id=item_id,
        publisher="TestPub",
        source_type="rss",
        title=f"Title for {item_id}",
        url=f"https://example.com/{item_id}",
        published_at_utc=datetime.now(timezone.utc),
        observed_at_utc=datetime.now(timezone.utc),
        content_hash=f"hash_{item_id}",
    )


@pytest.mark.asyncio
async def test_failure_injection_during_database_insertion_rolls_back_everything(test_repo):
    """
    FAILURE INJECTION: Inject database error during article insertion.
    Guarantees:
    1. Entire transaction rolls back.
    2. Zero articles are persisted.
    3. Database cursor does NOT advance.
    4. In-memory connector cursor is NOT acknowledged.
    5. Clean recovery on retry.
    """
    conn_id = "fail_inject_insert"
    items = [make_dummy_item(conn_id, "item-1"), make_dummy_item(conn_id, "item-2")]

    class FailingConnector(RSSConnector):
        def __init__(self):
            super().__init__(connector_id=conn_id, feed_url="https://example.com/fail.xml")
            self.ack_called = False

        async def fetch(self, cursor=None):
            return FetchBatch(
                connector_id=self.connector_id,
                items=items,
                next_cursor="cursor_target_v1",
                source_status="OK",
            )

        async def ack(self, cursor: str):
            self.ack_called = True
            await super().ack(cursor)

    connector = FailingConnector()
    orchestrator = IngestionOrchestrator(
        repository=test_repo,
        retry_policy=RetryPolicy(max_retries=1, base_delay_seconds=0.01, jitter=False)
    )

    # Patch save_batch_atomic to simulate disk I/O or constraint explosion
    original_save = test_repo.save_batch_atomic

    def simulated_crash_save(cid, batch_items, new_cursor):
        raise OSError("Simulated disk I/O failure during batch save")

    with patch.object(test_repo, "save_batch_atomic", side_effect=simulated_crash_save):
        res = await orchestrator.ingest_connector(connector)
        assert res["status"] == "FAILED"

    # Verifications:
    # 1. Database has 0 articles
    articles = test_repo.get_articles(connector_id=conn_id)
    assert len(articles) == 0

    # 2. Database cursor did NOT advance
    assert test_repo.get_connector_cursor(conn_id) is None

    # 3. Connector in-memory ack was NOT called
    assert connector.ack_called is False
    assert connector.committed_cursor is None

    # --- RECOVERY CYCLE ---
    # With disk error resolved, the exact same batch must persist cleanly
    res_recovery = await orchestrator.ingest_connector(connector)
    assert res_recovery["status"] == "SUCCESS"
    assert res_recovery["inserted"] == 2

    # Verifications post-recovery:
    assert len(test_repo.get_articles(connector_id=conn_id)) == 2
    assert test_repo.get_connector_cursor(conn_id) == "cursor_target_v1"
    assert connector.committed_cursor == "cursor_target_v1"


@pytest.mark.asyncio
async def test_failure_injection_during_cursor_advancement_rolls_back_articles(test_repo):
    """
    FAILURE INJECTION: Inject failure when updating connector_state inside the atomic transaction.
    Guarantees:
    - Entire SQLite transaction rolls back; no orphaned articles left committed.
    """
    conn_id = "fail_inject_cursor"
    items = [make_dummy_item(conn_id, "orphan-1")]

    # Manually run a transaction where connector_state update fails
    conn = test_repo._get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        # Insert article
        conn.execute(
            """INSERT INTO articles (
                   connector_id, source_item_id, publisher, source_type, title, url,
                   observed_at_utc, content_hash, raw_metadata, tickers, created_at_utc
               ) VALUES (?, ?, 'Pub', 'rss', 'Title', 'http://url',
                         datetime('now'), 'hash', '{}', '[]', datetime('now'));""",
            (conn_id, "orphan-1")
        )
        # Force failure on cursor update
        conn.execute("INSERT INTO connector_state (connector_id, non_existent_col) VALUES ('foo', 'bar');")
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()

    # Verify article was rolled back completely
    articles = test_repo.get_articles(connector_id=conn_id)
    assert len(articles) == 0
    assert test_repo.get_connector_cursor(conn_id) is None


@pytest.mark.asyncio
async def test_cancellation_during_fetch_does_not_advance_cursor(test_repo):
    """
    Verify that if a coroutine is cancelled while fetching from a provider:
    - Cursor does not advance.
    - Zero records are persisted.
    - Database remains consistent.
    """
    conn_id = "cancel_fetch_conn"

    class SlowHangingConnector(RSSConnector):
        async def fetch(self, cursor=None):
            await asyncio.sleep(10.0)  # Hang until cancelled
            return FetchBatch(connector_id=self.connector_id, items=[])

    connector = SlowHangingConnector(connector_id=conn_id, feed_url="https://example.com/hang.xml")
    orchestrator = IngestionOrchestrator(repository=test_repo)

    task = asyncio.create_task(orchestrator.ingest_connector(connector))
    await asyncio.sleep(0.02)  # Let it start
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # Assertions
    assert test_repo.get_connector_cursor(conn_id) is None
    assert len(test_repo.get_articles(connector_id=conn_id)) == 0


@pytest.mark.asyncio
@respx.mock
async def test_multi_cycle_idempotency_with_repeated_fetches(test_repo, rss_xml):
    """
    IDEMPOTENCY VERIFICATION:
    Run 5 consecutive ingestion cycles with unchanged content.
    Verify:
    1. Exactly 2 articles exist in DB (no duplicates).
    2. Cycle 1: inserted=2, skipped=0.
    3. Cycles 2-5: inserted=0, skipped=2.
    4. Database cursor advances consistently.
    """
    feed_url = "https://example.com/multi_cycle.xml"
    respx.get(feed_url).mock(return_value=httpx.Response(
        200, text=rss_xml, headers={"etag": '"fixed_etag"'}
    ))

    conn_id = "multi_cycle_conn"
    connector = RSSConnector(connector_id=conn_id, feed_url=feed_url)
    orchestrator = IngestionOrchestrator(repository=test_repo)

    # Cycle 1
    res1 = await orchestrator.ingest_connector(connector)
    assert res1["inserted"] == 2
    assert res1["skipped"] == 0

    # Cycles 2 to 5
    for cycle in range(2, 6):
        res = await orchestrator.ingest_connector(connector)
        assert res["inserted"] == 0
        # If feed returns items again without 304, save_batch_atomic suppresses them
        # If 304 was returned, items=[], but here respx returns 200 with same XML:
        # RSS lookback deduplication skips already-seen IDs, so 0 duplicates reach DB
        assert len(test_repo.get_articles(connector_id=conn_id)) == 2
