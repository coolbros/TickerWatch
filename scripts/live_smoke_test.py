"""Controlled live smoke test for external RSS and SEC EDGAR endpoints.
Designed to verify live internet interoperability without flooding external servers.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.connectors.rss import RSSConnector
from backend.connectors.sec import SECConnector


async def smoke_test_rss() -> bool:
    print("\n--- 1. Testing Live Public RSS Feed ---")
    feed_url = "https://www.sec.gov/news/pressreleases.rss"
    print(f"Connecting to: {feed_url}")

    connector = RSSConnector(
        connector_id="live_rss_smoke",
        feed_url=feed_url,
        name="SEC Press Releases Live Smoke",
    )
    try:
        batch = await connector.fetch()
        print(f"Success! Status: {batch.source_status}")
        print(f"Items fetched: {len(batch.items)} (Raw count: {batch.raw_count})")
        if batch.items:
            first = batch.items[0]
            print(f"Sample Headline: {first.title[:80]}...")
            print(f"Published (UTC): {first.published_at_utc}")
            print(f"URL: {first.url[:80]}...")
        return len(batch.items) > 0
    except Exception as exc:
        print(f"RSS Live Smoke Test Failed: {type(exc).__name__}: {exc}")
        return False


async def smoke_test_sec() -> bool:
    print("\n--- 2. Testing Official SEC EDGAR Submissions API ---")
    # Apple Inc. CIK
    cik = "0000320193"
    print(f"Target: Apple Inc. (CIK {cik}) via data.sec.gov")

    connector = SECConnector(
        connector_id="live_sec_smoke",
        user_agent="TickerWatch/0.1 admin-audit@example.com",
        tracked_ciks={cik: "AAPL"},
    )

    try:
        batch = await connector.fetch()
        print(f"Success! Status: {batch.source_status}")
        print(f"Filings extracted: {len(batch.items)} (Total recent: {batch.raw_count})")
        if batch.items:
            recent_filing = batch.items[0]
            print(f"Sample Filing: {recent_filing.title}")
            print(f"Accession: {recent_filing.source_item_id}")
            print(f"Document URL: {recent_filing.url}")
            print(f"Form: {recent_filing.raw_metadata.get('form')}")
            print(f"Filing Date: {recent_filing.raw_metadata.get('filing_date')}")
        return len(batch.items) > 0
    except Exception as exc:
        print(f"SEC Live Smoke Test Failed: {type(exc).__name__}: {exc}")
        return False


async def main() -> None:
    print("=" * 60)
    print("TICKERWATCH LIVE SMOKE TEST")
    print("=" * 60)
    rss_ok = await smoke_test_rss()
    sec_ok = await smoke_test_sec()

    print("\n" + "=" * 60)
    print("SMOKE TEST SUMMARY")
    print(f"Public RSS Feed:      {'PASSED' if rss_ok else 'FAILED / BLOCKED'}")
    print(f"SEC EDGAR API:        {'PASSED' if sec_ok else 'FAILED / BLOCKED'}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
