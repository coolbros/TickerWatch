"""Unit tests for the Normalisation and Deduplication services."""

import time
from datetime import datetime, timezone, timedelta
import pytest

from backend.ingestion.normalizer import (
    clean_html_and_whitespace,
    normalize_url,
    parse_timestamp_to_utc,
    normalize_record,
    NormalizationError,
)
from backend.ingestion.deduplication import compute_content_hash, derive_stable_source_id


def test_clean_html_and_whitespace():
    raw = "   <p>Apple announces <b>new</b> M4 chips &amp; Macs!</p> \n\n  "
    cleaned = clean_html_and_whitespace(raw)
    assert cleaned == "Apple announces new M4 chips & Macs!"


def test_clean_html_empty():
    assert clean_html_and_whitespace(None) == ""
    assert clean_html_and_whitespace("   ") == ""


def test_normalize_url():
    raw = "https://example.com/news/article?id=123&utm_source=twitter&utm_medium=social"
    cleaned = normalize_url(raw)
    assert "utm_source" not in cleaned
    assert "utm_medium" not in cleaned
    assert "id=123" in cleaned
    assert cleaned.startswith("https://example.com/news/article")


def test_parse_timestamp_to_utc_iso():
    iso_str = "2026-11-05T14:30:00+05:30"
    parsed = parse_timestamp_to_utc(iso_str)
    assert parsed is not None
    assert parsed.tzinfo == timezone.utc
    # 14:30 +05:30 is 09:00 UTC
    assert parsed.hour == 9
    assert parsed.minute == 0


def test_parse_timestamp_to_utc_rfc822():
    rfc_str = "Thu, 05 Nov 2026 09:00:00 GMT"
    parsed = parse_timestamp_to_utc(rfc_str)
    assert parsed is not None
    assert parsed.tzinfo == timezone.utc
    assert parsed.year == 2026
    assert parsed.hour == 9


def test_parse_timestamp_to_utc_missing_or_malformed():
    assert parse_timestamp_to_utc(None) is None
    assert parse_timestamp_to_utc("") is None
    assert parse_timestamp_to_utc("not-a-real-date-string") is None


def test_parse_timestamp_struct_time():
    st = time.gmtime(1700000000)
    parsed = parse_timestamp_to_utc(st)
    assert parsed is not None
    assert parsed.tzinfo == timezone.utc


def test_normalize_record_valid():
    item = normalize_record(
        connector_id="test_rss",
        publisher="Reuters",
        source_type="rss",
        raw_id="guid-12345",
        raw_title="<b>Record Revenue</b> for Q3",
        raw_url="https://reuters.com/markets/123?utm_campaign=daily",
        raw_published="2026-11-06T10:00:00Z",
        raw_content="<p>Full article summary text.</p>",
        tickers=["aapl", "AAPL", "MSFT "],
    )
    assert item.connector_id == "test_rss"
    assert item.source_item_id == "guid-12345"
    assert item.title == "Record Revenue for Q3"
    assert "utm_campaign" not in item.url
    assert item.published_at_utc == datetime(2026, 11, 6, 10, 0, 0, tzinfo=timezone.utc)
    assert item.published_at_original == "2026-11-06T10:00:00Z"
    assert item.tickers == ["AAPL", "MSFT"]  # Deduplicated and uppercase
    assert item.content_text == "Full article summary text."
    assert len(item.content_hash) == 64  # SHA-256


def test_normalize_record_fatal_empty_title():
    with pytest.raises(NormalizationError, match="title is required"):
        normalize_record(
            connector_id="test_rss",
            publisher="Reuters",
            source_type="rss",
            raw_id="guid-1",
            raw_title="   <b> </b>  ",
            raw_url="https://example.com/article",
            raw_published=None,
        )


def test_normalize_record_fatal_empty_url():
    with pytest.raises(NormalizationError, match="URL is required"):
        normalize_record(
            connector_id="test_rss",
            publisher="Reuters",
            source_type="rss",
            raw_id="guid-1",
            raw_title="Valid Title",
            raw_url="",
            raw_published=None,
        )


def test_content_hash_deterministic():
    h1 = compute_content_hash("Apple reports earnings", "Quarterly profit rises.")
    h2 = compute_content_hash("  APPLE reports earnings  ", "<p>Quarterly profit rises.</p>")
    assert h1 == h2


def test_derive_stable_source_id_prefers_raw_id():
    sid = derive_stable_source_id("rss", "article-999", "https://example.com/item")
    assert sid == "article-999"


def test_derive_stable_source_id_fallback_url():
    sid = derive_stable_source_id("rss", None, "https://example.com/item")
    assert sid.startswith("url_")
