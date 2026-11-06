"""Integration tests validating restart durability, crash recovery, and duplicate suppression."""

from pathlib import Path
import httpx
import pytest
import respx

from backend.connectors.rss import RSSConnector
from backend.connectors.sec import SECConnector
from backend.database.connection import apply_migrations
from backend.database.repository import Repository
from backend.ingestion.orchestrator import IngestionOrchestrator
from backend.ingestion.retry import RetryPolicy

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def rss_xml():
    return (FIXTURES_DIR / "valid_rss2.xml").read_text(encoding="utf-8")


@pytest.fixture
def sec_json():
    return (FIXTURES_DIR / "sec_submissions_sample.json").read_text(encoding="utf-8")


@pytest.mark.asyncio
@respx.mock
async def test_restart_durability_and_duplicate_suppression(tmp_path, rss_xml):
    """
    Acceptance Test 3 & 5:
    Run ingestion, stop application, restart with new instances,
    re-run ingestion and verify zero duplicates created.
    """
    db_file = tmp_path / "restart_test.db"
    db_path = str(db_file)
    apply_migrations(db_path)

    feed_url = "https://news.example.com/rss.xml"
    respx.get(feed_url).mock(return_value=httpx.Response(200, text=rss_xml))

    # --- Phase 1: First Run ---
    repo1 = Repository(db_path)
    connector1 = RSSConnector(connector_id="rss_test", feed_url=feed_url)
    orchestrator1 = IngestionOrchestrator(repository=repo1)

    result1 = await orchestrator1.ingest_connector(connector1)
    assert result1["status"] == "SUCCESS"
    assert result1["inserted"] == 2
    assert result1["skipped"] == 0

    articles_run1 = repo1.get_articles(connector_id="rss_test")
    assert len(articles_run1) == 2
    cursor1 = repo1.get_connector_cursor("rss_test")
    assert cursor1 is not None

    # --- Simulate Full Application Shutdown & Restart ---
    del orchestrator1
    del connector1
    del repo1

    # --- Phase 2: Second Run on Fresh Instances ---
    repo2 = Repository(db_path)
    connector2 = RSSConnector(connector_id="rss_test", feed_url=feed_url)
    orchestrator2 = IngestionOrchestrator(repository=repo2)

    # Ingest again with the exact same data
    result2 = await orchestrator2.ingest_connector(connector2)
    assert result2["status"] == "SUCCESS"
    # Even if feed returns entries, duplicate suppression prevents duplicates
    articles_run2 = repo2.get_articles(connector_id="rss_test")
    assert len(articles_run2) == 2  # Exactly 2 articles, zero duplicates!


@pytest.mark.asyncio
@respx.mock
async def test_no_cursor_loss_on_unpersisted_crash(tmp_path, rss_xml):
    """
    Acceptance Test 4:
    Simulate a crash after fetching but BEFORE persistence.
    Verify that unpersisted records are fetched and persisted cleanly after restarting.
    """
    db_file = tmp_path / "crash_test.db"
    db_path = str(db_file)
    apply_migrations(db_path)

    feed_url = "https://news.example.com/rss.xml"
    respx.get(feed_url).mock(return_value=httpx.Response(200, text=rss_xml))

    repo = Repository(db_path)
    connector = RSSConnector(connector_id="rss_crash", feed_url=feed_url)

    # 1. Fetch happens
    batch = await connector.fetch()
    assert len(batch.items) == 2

    # 2. Simulate CRASH before save_batch_atomic is called
    # (Notice: no save_batch_atomic called, no ack called)
    cursor_before_crash = repo.get_connector_cursor("rss_crash")
    assert cursor_before_crash is None  # Cursor remained None!
    assert len(repo.get_articles(connector_id="rss_crash")) == 0

    # 3. Simulate Restart & Recovery:
    # Orchestrator reads cursor from DB (which is still None), fetches again, and commits
    orchestrator = IngestionOrchestrator(repository=repo)
    result = await orchestrator.ingest_connector(connector)

    assert result["status"] == "SUCCESS"
    assert result["inserted"] == 2
    assert len(repo.get_articles(connector_id="rss_crash")) == 2

    # Now cursor is committed
    cursor_after_recovery = repo.get_connector_cursor("rss_crash")
    assert cursor_after_recovery is not None


@pytest.mark.asyncio
@respx.mock
async def test_multiple_independent_connectors(tmp_path, rss_xml, sec_json):
    """
    Acceptance Test 1:
    At least two connectors (RSS & SEC) operate independently and successfully.
    """
    db_file = tmp_path / "multi_conn.db"
    db_path = str(db_file)
    apply_migrations(db_path)

    rss_url = "https://news.example.com/rss.xml"
    respx.get(rss_url).mock(return_value=httpx.Response(200, text=rss_xml))

    cik = "0000320193"
    sec_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    respx.get(sec_url).mock(return_value=httpx.Response(200, text=sec_json))

    repo = Repository(db_path)
    orchestrator = IngestionOrchestrator(repository=repo)

    rss_conn = RSSConnector(connector_id="rss_wire", feed_url=rss_url)
    sec_conn = SECConnector(
        connector_id="sec_edgar",
        user_agent="TickerWatch/0.1 admin@example.com",
        tracked_ciks={cik: "AAPL"},
    )

    res_rss = await orchestrator.ingest_connector(rss_conn)
    res_sec = await orchestrator.ingest_connector(sec_conn)

    assert res_rss["status"] == "SUCCESS"
    assert res_sec["status"] == "SUCCESS"
    assert res_rss["inserted"] == 2
    assert res_sec["inserted"] == 3

    # Inspect total persisted articles
    all_articles = repo.get_articles(limit=50)
    assert len(all_articles) == 5

    # Verify canonical schema fields on all articles (Acceptance Test 2)
    for a in all_articles:
        assert a.publisher
        assert a.url
        assert a.connector_id in ("rss_wire", "sec_edgar")
        assert a.observed_at_utc
        assert a.source_item_id
        assert a.content_hash
