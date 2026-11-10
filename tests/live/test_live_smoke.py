"""Opt-in live integration and smoke tests hitting external feeds.

Execute explicitly via:
    pytest -m live
"""

from datetime import datetime, timezone
import pytest

from backend.connectors.models import FetchBatch, SourceItem
from backend.connectors.rss import RSSConnector
from backend.connectors.sec import SECConnector


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_sec_press_releases_rss():
    """Live smoke test against official SEC Press Releases RSS feed."""
    feed_url = "https://www.sec.gov/news/pressreleases.rss"
    connector = RSSConnector(
        connector_id="live_sec_press",
        feed_url=feed_url,
        name="SEC Press Releases Live Smoke",
    )

    batch = await connector.fetch()
    assert isinstance(batch, FetchBatch)
    assert batch.source_status in ("OK", "NOT_MODIFIED")

    if batch.source_status == "OK":
        assert len(batch.items) > 0
        first_item = batch.items[0]
        assert isinstance(first_item, SourceItem)
        assert first_item.title
        assert first_item.url.startswith("https://")
        assert first_item.content_hash

        # Test conditional polling on second fetch
        batch2 = await connector.fetch(cursor=batch.next_cursor)
        assert batch2.source_status in ("OK", "NOT_MODIFIED")


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_sec_edgar_submissions():
    """Live smoke test against official SEC EDGAR submissions API for Apple Inc."""
    cik = "0000320193"  # Apple Inc.
    connector = SECConnector(
        connector_id="live_sec_edgar",
        user_agent="TickerWatch/0.1 test-audit@example.com",
        tracked_ciks={cik: "AAPL"},
    )

    batch = await connector.fetch()
    assert isinstance(batch, FetchBatch)
    assert batch.source_status == "OK"
    assert len(batch.items) > 0

    first_filing = batch.items[0]
    assert isinstance(first_filing, SourceItem)
    assert first_filing.tickers == ["AAPL"]
    assert first_filing.source_item_id.startswith("sec_")
    assert "form" in first_filing.raw_metadata
