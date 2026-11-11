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


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_rbi_press_releases_rss(tmp_path):
    """Live opt-in smoke test against official Reserve Bank of India (RBI) Press Releases RSS."""
    from backend.alpha.live_news_adapters import RBIPressReleaseAdapter
    from backend.alpha.storage import RawPayloadStore

    adapter = RBIPressReleaseAdapter(
        enabled=True,
        raw_payload_store=RawPayloadStore(base_dir=str(tmp_path / "rbi_raw")),
    )
    events, next_cursor, quarantined = await adapter.fetch_news_events(cursor=None)
    assert len(events) > 0
    assert events[0].provider_id == "rbi_press_rss"
    assert events[0].published_at_utc is not None
    assert events[0].published_at_utc.tzinfo == timezone.utc
    assert events[0].raw_payload_ref is not None
    assert next_cursor is not None
    # Conditional 304 check
    events2, _, _ = await adapter.fetch_news_events(cursor=next_cursor)
    assert events2 == []
