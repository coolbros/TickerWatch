"""Unit tests for SEC EDGAR submissions connector using mocked HTTP responses and fixtures."""

import json
from pathlib import Path
import httpx
import pytest
import respx

from backend.connectors.models import ConnectorStatus
from backend.connectors.sec import (
    SECConfigurationError,
    SECConnector,
    normalize_cik,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def sec_sample_json():
    return (FIXTURES_DIR / "sec_submissions_sample.json").read_text(encoding="utf-8")


def test_normalize_cik():
    assert normalize_cik("320193") == "0000320193"
    assert normalize_cik("0000320193") == "0000320193"
    assert normalize_cik("CIK0000320193") == "0000320193"
    assert normalize_cik("  789019 ") == "0000789019"


def test_unconfigured_user_agent_error():
    """Connector must fail or mark status AUTH_ERROR if required SEC User-Agent is missing contact."""
    connector = SECConnector(
        user_agent="InvalidAgentWithoutEmail",
        tracked_ciks={"0000320193": "AAPL"},
    )
    assert connector.status == ConnectorStatus.AUTH_ERROR

    with pytest.raises(SECConfigurationError, match="User-Agent is not properly configured"):
        # Calling fetch should raise configuration error
        import asyncio
        asyncio.run(connector.fetch())


@pytest.mark.asyncio
@respx.mock
async def test_sec_valid_submissions_parsing(sec_sample_json):
    cik = "0000320193"
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    respx.get(url).mock(return_value=httpx.Response(200, text=sec_sample_json))

    connector = SECConnector(
        user_agent="TickerWatch/0.1 admin@example.com",
        tracked_ciks={cik: "AAPL"},
    )
    batch = await connector.fetch()

    assert batch.connector_id == "sec_edgar"
    assert len(batch.items) == 3
    assert batch.source_status == "OK"

    # Test 10-K filing
    item1 = batch.items[0]
    assert item1.source_item_id == "sec_0000320193-24-000106"
    assert "Form 10-K" in item1.title
    assert "Annual Report" in item1.title
    assert item1.tickers == ["AAPL"]
    # Check official URL construction
    assert item1.url == "https://www.sec.gov/Archives/edgar/data/320193/000032019324000106/aapl-20240928.htm"
    assert item1.published_at_utc is not None
    assert item1.raw_metadata["form"] == "10-K"
    assert item1.raw_metadata["report_date"] == "2024-09-28"

    # Test amended filing (10-Q/A)
    item3 = batch.items[2]
    assert "Form 10-Q/A" in item3.title
    assert item3.raw_metadata["form"] == "10-Q/A"

    # Verify cursor generated
    assert batch.next_cursor is not None
    cursor_data = json.loads(batch.next_cursor)
    assert cik in cursor_data["ciks"]
    assert cursor_data["ciks"][cik]["last_accession"] == "0000320193-24-000106"


@pytest.mark.asyncio
@respx.mock
async def test_sec_incremental_polling(sec_sample_json):
    cik = "0000320193"
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    respx.get(url).mock(return_value=httpx.Response(200, text=sec_sample_json))

    connector = SECConnector(
        user_agent="TickerWatch/0.1 admin@example.com",
        tracked_ciks={cik: "AAPL"},
    )

    # First fetch gets all 3 filings
    batch1 = await connector.fetch()
    assert len(batch1.items) == 3

    # Subsequent fetch with cursor: should stop because last_accession is already seen
    batch2 = await connector.fetch(cursor=batch1.next_cursor)
    assert len(batch2.items) == 0


@pytest.mark.asyncio
@respx.mock
async def test_sec_missing_primary_doc_fallback(sec_sample_json):
    data = json.loads(sec_sample_json)
    # Clear primaryDocument for the first filing
    data["filings"]["recent"]["primaryDocument"][0] = None
    cik = "0000320193"
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    respx.get(url).mock(return_value=httpx.Response(200, json=data))

    connector = SECConnector(
        user_agent="TickerWatch/0.1 admin@example.com",
        tracked_ciks={cik: "AAPL"},
    )
    batch = await connector.fetch()
    # First item should fall back to browse URL
    assert batch.items[0].url == f"https://www.sec.gov/edgar/browse/?CIK={cik}"


@pytest.mark.asyncio
@respx.mock
async def test_sec_rate_limiting_429():
    cik = "0000320193"
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    respx.get(url).mock(return_value=httpx.Response(429, text="Too Many Requests"))

    connector = SECConnector(
        user_agent="TickerWatch/0.1 admin@example.com",
        tracked_ciks={cik: "AAPL"},
    )

    with pytest.raises(httpx.HTTPStatusError):
        await connector.fetch()

    health = await connector.health()
    assert health.consecutive_failures == 1
    assert "429" in (health.last_error or "")


@pytest.mark.asyncio
@respx.mock
async def test_sec_invalid_json():
    cik = "0000320193"
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    respx.get(url).mock(return_value=httpx.Response(200, text="NOT JSON AT ALL"))

    connector = SECConnector(
        user_agent="TickerWatch/0.1 admin@example.com",
        tracked_ciks={cik: "AAPL"},
    )
    batch = await connector.fetch()
    # Malformed JSON should record failure and yield 0 items without crashing entire batch
    assert len(batch.items) == 0
    assert connector.consecutive_failures >= 1


@pytest.mark.asyncio
@respx.mock
async def test_sec_multi_cik_partial_failure(sec_sample_json):
    """
    Verify multi-company isolation: if one tracked CIK returns invalid data,
    the valid company filings are still extracted and status reflects DEGRADED partial success.
    """
    cik_apple = "0000320193"
    cik_bad = "0001018724"

    url_apple = f"https://data.sec.gov/submissions/CIK{cik_apple}.json"
    url_bad = f"https://data.sec.gov/submissions/CIK{cik_bad}.json"

    respx.get(url_apple).mock(return_value=httpx.Response(200, text=sec_sample_json))
    respx.get(url_bad).mock(return_value=httpx.Response(200, text="INVALID_JSON_STREAM"))

    connector = SECConnector(
        user_agent="TickerWatch/0.1 admin@example.com",
        tracked_ciks={cik_apple: "AAPL", cik_bad: "AMZN"},
    )
    batch = await connector.fetch()

    # Apple filings successfully extracted (3 items)
    assert len(batch.items) == 3
    # Status is DEGRADED because one CIK failed
    assert connector.status == ConnectorStatus.DEGRADED

