"""Unit tests for RSS and Atom connector using mocked HTTP responses and fixtures."""

import json
from pathlib import Path
import httpx
import pytest
import respx

from backend.connectors.models import ConnectorStatus
from backend.connectors.rss import RSSConnector

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def valid_rss2_content():
    return (FIXTURES_DIR / "valid_rss2.xml").read_text(encoding="utf-8")


@pytest.fixture
def valid_atom_content():
    return (FIXTURES_DIR / "valid_atom.xml").read_text(encoding="utf-8")


@pytest.fixture
def missing_dates_content():
    return (FIXTURES_DIR / "missing_dates.xml").read_text(encoding="utf-8")


@pytest.fixture
def relative_urls_content():
    return (FIXTURES_DIR / "relative_urls.xml").read_text(encoding="utf-8")


@pytest.fixture
def empty_feed_content():
    return (FIXTURES_DIR / "empty_feed.xml").read_text(encoding="utf-8")


@pytest.mark.asyncio
@respx.mock
async def test_rss2_valid_parsing(valid_rss2_content):
    url = "https://marketwire.example.com/rss.xml"
    respx.get(url).mock(return_value=httpx.Response(
        200,
        text=valid_rss2_content,
        headers={"etag": '"v123"', "last-modified": "Thu, 06 Nov 2026 12:00:00 GMT"}
    ))

    connector = RSSConnector(connector_id="marketwire", feed_url=url, tickers=["AAPL", "MSFT"])
    batch = await connector.fetch()

    assert batch.connector_id == "marketwire"
    assert len(batch.items) == 2
    assert batch.source_status == "OK"

    item1 = batch.items[0]
    assert item1.title == "Apple Reports Record Services Revenue in Q4"
    assert item1.source_item_id == "marketwire-guid-1001"
    assert item1.published_at_utc is not None
    assert item1.tickers == ["AAPL", "MSFT"]
    assert "utm_source" not in batch.items[1].url

    # Next cursor contains etag
    cursor_dict = json.loads(batch.next_cursor or "{}")
    assert cursor_dict["etag"] == '"v123"'


@pytest.mark.asyncio
@respx.mock
async def test_atom_valid_parsing(valid_atom_content):
    url = "https://globalequities.example.com/atom.xml"
    respx.get(url).mock(return_value=httpx.Response(200, text=valid_atom_content))

    connector = RSSConnector(connector_id="atom_test", feed_url=url, tickers=["AMZN"])
    batch = await connector.fetch()

    assert len(batch.items) == 1
    item = batch.items[0]
    assert item.title == "Amazon Completes Logistics Robotics Rollout"
    assert item.source_item_id == "tag:globalequities.example.com,2026:atom-entry-501"
    assert item.published_at_utc is not None
    assert item.tickers == ["AMZN"]


@pytest.mark.asyncio
@respx.mock
async def test_empty_feed(empty_feed_content):
    url = "https://example.com/empty"
    respx.get(url).mock(return_value=httpx.Response(200, text=empty_feed_content))

    connector = RSSConnector(connector_id="empty_test", feed_url=url)
    batch = await connector.fetch()

    assert len(batch.items) == 0
    assert batch.raw_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_malformed_xml():
    url = "https://example.com/malformed"
    respx.get(url).mock(return_value=httpx.Response(200, text="<unclosed_tag><not valid xml>"))

    connector = RSSConnector(connector_id="malformed_test", feed_url=url)
    batch = await connector.fetch()
    # Feedparser handles malformed XML gracefully, producing 0 entries
    assert len(batch.items) == 0


@pytest.mark.asyncio
@respx.mock
async def test_missing_publication_date(missing_dates_content):
    url = "https://example.com/missing-dates"
    respx.get(url).mock(return_value=httpx.Response(200, text=missing_dates_content))

    connector = RSSConnector(connector_id="missing_dates_test", feed_url=url)
    batch = await connector.fetch()

    assert len(batch.items) == 1
    item = batch.items[0]
    # Date must not be fabricated
    assert item.published_at_utc is None
    assert item.published_at_original is None
    assert item.observed_at_utc is not None


@pytest.mark.asyncio
@respx.mock
async def test_relative_urls_resolution(relative_urls_content):
    url = "https://exchange.example.com/rss/feed"
    respx.get(url).mock(return_value=httpx.Response(200, text=relative_urls_content))

    connector = RSSConnector(connector_id="rel_test", feed_url=url)
    batch = await connector.fetch()

    assert len(batch.items) == 1
    item = batch.items[0]
    assert item.url == "https://exchange.example.com/filings/2026/quarterly-report.html"


@pytest.mark.asyncio
@respx.mock
async def test_http_304_not_modified():
    url = "https://example.com/rss"
    cursor = '{"etag": "\\"old-etag\\"", "last_modified": "Wed, 05 Nov 2026 10:00:00 GMT", "seen_ids": ["id-1"]}'

    route = respx.get(url).mock(return_value=httpx.Response(304))

    connector = RSSConnector(connector_id="not_modified_test", feed_url=url)
    batch = await connector.fetch(cursor=cursor)

    assert route.calls.last.request.headers["if-none-match"] == '"old-etag"'
    assert route.calls.last.request.headers["if-modified-since"] == "Wed, 05 Nov 2026 10:00:00 GMT"
    assert len(batch.items) == 0
    assert batch.source_status == "NOT_MODIFIED"
    assert batch.next_cursor == cursor


@pytest.mark.asyncio
@respx.mock
async def test_http_429_rate_limiting():
    url = "https://example.com/rate-limited"
    respx.get(url).mock(return_value=httpx.Response(429, text="Too Many Requests"))

    connector = RSSConnector(connector_id="rate_limited_test", feed_url=url)
    with pytest.raises(httpx.HTTPStatusError):
        await connector.fetch()

    health = await connector.health()
    assert health.consecutive_failures == 1
    assert "429" in (health.last_error or "")


@pytest.mark.asyncio
@respx.mock
async def test_http_500_server_error():
    url = "https://example.com/server-error"
    respx.get(url).mock(return_value=httpx.Response(500, text="Internal Server Error"))

    connector = RSSConnector(connector_id="error_test", feed_url=url)
    with pytest.raises(httpx.HTTPStatusError):
        await connector.fetch()

    health = await connector.health()
    assert health.consecutive_failures == 1


@pytest.mark.asyncio
@respx.mock
async def test_connection_timeout():
    url = "https://example.com/timeout"
    respx.get(url).mock(side_effect=httpx.ReadTimeout("Connection timed out"))

    connector = RSSConnector(connector_id="timeout_test", feed_url=url)
    with pytest.raises(httpx.ReadTimeout):
        await connector.fetch()

    health = await connector.health()
    assert health.consecutive_failures == 1
    assert "timed out" in (health.last_error or "")


@pytest.mark.asyncio
@respx.mock
async def test_lookback_deduplication(valid_rss2_content):
    url = "https://marketwire.example.com/rss.xml"
    respx.get(url).mock(return_value=httpx.Response(200, text=valid_rss2_content))

    connector = RSSConnector(connector_id="lookback_test", feed_url=url)

    # First fetch: should return both items
    batch1 = await connector.fetch()
    assert len(batch1.items) == 2

    # Second fetch with cursor: should deduplicate previously seen IDs
    batch2 = await connector.fetch(cursor=batch1.next_cursor)
    assert len(batch2.items) == 0  # Both items already seen in cursor window
