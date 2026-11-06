"""Contract tests verifying that all connector implementations adhere to BaseConnector interface."""

from pathlib import Path
import httpx
import pytest
import respx

from backend.connectors.base import BaseConnector
from backend.connectors.models import ConnectorHealth, FetchBatch, SourceItem
from backend.connectors.rss import RSSConnector
from backend.connectors.sec import SECConnector

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def rss_fixture():
    return (FIXTURES_DIR / "valid_rss2.xml").read_text(encoding="utf-8")


@pytest.fixture
def sec_fixture():
    return (FIXTURES_DIR / "sec_submissions_sample.json").read_text(encoding="utf-8")


@pytest.mark.asyncio
@respx.mock
async def test_rss_connector_contract(rss_fixture):
    """Verify RSSConnector contract compliance."""
    url = "https://example.com/contract-rss.xml"
    respx.get(url).mock(return_value=httpx.Response(200, text=rss_fixture))

    connector = RSSConnector(connector_id="contract_rss", feed_url=url)
    assert isinstance(connector, BaseConnector)

    # 1. Health contract
    health = await connector.health()
    assert isinstance(health, ConnectorHealth)
    assert health.connector_id == "contract_rss"
    assert health.enabled is True

    # 2. Fetch contract
    batch = await connector.fetch(cursor=None)
    assert isinstance(batch, FetchBatch)
    assert batch.connector_id == "contract_rss"
    assert len(batch.items) > 0
    for item in batch.items:
        assert isinstance(item, SourceItem)
        assert item.connector_id == "contract_rss"
        assert item.source_item_id
        assert item.publisher
        assert item.title
        assert item.url
        assert item.observed_at_utc is not None
        assert item.content_hash

    # 3. Ack contract
    await connector.ack(batch.next_cursor)
    assert connector.committed_cursor == batch.next_cursor


@pytest.mark.asyncio
@respx.mock
async def test_sec_connector_contract(sec_fixture):
    """Verify SECConnector contract compliance."""
    cik = "0000320193"
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    respx.get(url).mock(return_value=httpx.Response(200, text=sec_fixture))

    connector = SECConnector(
        connector_id="contract_sec",
        user_agent="TickerWatch/0.1 admin@example.com",
        tracked_ciks={cik: "AAPL"},
    )
    assert isinstance(connector, BaseConnector)

    # 1. Health contract
    health = await connector.health()
    assert isinstance(health, ConnectorHealth)
    assert health.connector_id == "contract_sec"

    # 2. Fetch contract
    batch = await connector.fetch(cursor=None)
    assert isinstance(batch, FetchBatch)
    assert batch.connector_id == "contract_sec"
    assert len(batch.items) > 0
    for item in batch.items:
        assert isinstance(item, SourceItem)
        assert item.connector_id == "contract_sec"
        assert item.source_item_id.startswith("sec_")
        assert item.publisher
        assert item.title
        assert item.url
        assert item.observed_at_utc is not None
        assert item.content_hash

    # 3. Ack contract
    await connector.ack(batch.next_cursor)
    assert connector.committed_cursor == batch.next_cursor
