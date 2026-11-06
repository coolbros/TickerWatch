"""Data normalisation service transforming raw provider records to canonical SourceItems."""

import html
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from dateutil import parser as date_parser

from backend.connectors.models import SourceItem
from backend.ingestion.deduplication import compute_content_hash, derive_stable_source_id


class NormalizationError(ValueError):
    """Raised when an incoming source record fails fatal validation criteria."""
    pass


# Common analytics/tracking parameters to strip during URL normalization
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "ref", "source", "feed",
}


def clean_html_and_whitespace(raw_text: Optional[str]) -> str:
    """Strip HTML markup, unescape XML/HTML entities, and normalize whitespace."""
    if not raw_text:
        return ""
    # Unescape HTML entities (e.g., &amp;, &quot;, &#39;)
    unescaped = html.unescape(raw_text)
    # Strip HTML tags
    no_html = re.sub(r"<[^>]+>", " ", unescaped)
    # Collapse multiple whitespace characters and newlines
    normalized = re.sub(r"\s+", " ", no_html).strip()
    return normalized


def normalize_url(raw_url: str) -> str:
    """
    Safely normalise URLs:
    - Lowercase scheme and netloc.
    - Remove tracking query parameters.
    - Strip trailing slashes on root paths.
    """
    if not raw_url:
        return ""
    parsed = urlparse(raw_url.strip())
    if not parsed.scheme or not parsed.netloc:
        return raw_url.strip()

    # Lowercase domain and scheme
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    # Filter tracking params from query string
    qs = parse_qs(parsed.query, keep_blank_values=True)
    filtered_qs = {k: v for k, v in qs.items() if k.lower() not in TRACKING_PARAMS}
    clean_query = urlencode(filtered_qs, doseq=True)

    path = parsed.path
    if path == "/":
        path = ""

    return urlunparse((scheme, netloc, path, parsed.params, clean_query, parsed.fragment))


def parse_timestamp_to_utc(raw_date: Any) -> Optional[datetime]:
    """
    Parse varied timestamp formats (ISO, RFC 822, feedparser struct_time, epoch) into UTC.
    Never fabricates a date. Returns None if unparseable or absent.
    """
    if not raw_date:
        return None

    try:
        # Handle time.struct_time (from feedparser parsed fields)
        if isinstance(raw_date, (time.struct_time, tuple)):
            epoch_secs = time.mktime(raw_date)
            return datetime.fromtimestamp(epoch_secs, tz=timezone.utc)

        # Handle numeric timestamps (epoch seconds or milliseconds)
        if isinstance(raw_date, (int, float)):
            if raw_date > 1e11:  # Milliseconds
                raw_date = raw_date / 1000.0
            return datetime.fromtimestamp(raw_date, tz=timezone.utc)

        # Handle string dates
        if isinstance(raw_date, str):
            clean_str = raw_date.strip()
            if not clean_str:
                return None
            dt = date_parser.parse(clean_str)
            if dt.tzinfo is None:
                # If timezone is omitted, assign UTC
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                # Convert to UTC
                dt = dt.astimezone(timezone.utc)
            return dt

        # Handle datetime objects
        if isinstance(raw_date, datetime):
            if raw_date.tzinfo is None:
                return raw_date.replace(tzinfo=timezone.utc)
            return raw_date.astimezone(timezone.utc)

    except Exception:
        # Intentionally do not crash on invalid date format; return None as required
        return None

    return None


def normalize_record(
    connector_id: str,
    publisher: str,
    source_type: str,
    raw_id: Optional[str],
    raw_title: Optional[str],
    raw_url: Optional[str],
    raw_published: Any,
    raw_content: Optional[str] = None,
    raw_updated: Any = None,
    raw_metadata: Optional[Dict[str, Any]] = None,
    tickers: Optional[List[str]] = None,
    language: Optional[str] = "en",
) -> SourceItem:
    """
    Validate, normalize and construct a canonical SourceItem.
    Raises NormalizationError if required invariants are violated.
    """
    clean_title = clean_html_and_whitespace(raw_title)
    if not clean_title:
        raise NormalizationError("Item title is required and cannot be empty after whitespace stripping")

    clean_url = normalize_url(raw_url or "")
    if not clean_url:
        raise NormalizationError("Item URL is required and cannot be empty")

    published_str = str(raw_published).strip() if raw_published else None
    published_utc = parse_timestamp_to_utc(raw_published)
    updated_utc = parse_timestamp_to_utc(raw_updated)

    clean_content = clean_html_and_whitespace(raw_content) if raw_content else None

    # Derive stable source item ID
    stable_id = derive_stable_source_id(
        connector_id=connector_id,
        raw_id=raw_id,
        url=clean_url,
        title=clean_title,
        published_str=published_str,
    )

    # Compute deterministic content hash for duplicate content detection
    content_hash = compute_content_hash(clean_title, clean_content)

    return SourceItem(
        connector_id=connector_id,
        source_item_id=stable_id,
        publisher=publisher.strip(),
        source_type=source_type.strip(),
        title=clean_title,
        url=clean_url,
        published_at_utc=published_utc,
        published_at_original=published_str,
        observed_at_utc=datetime.now(timezone.utc),
        updated_at_utc=updated_utc,
        content_text=clean_content,
        content_hash=content_hash,
        raw_metadata=raw_metadata or {},
        language=(language or "en").strip().lower(),
        tickers=tickers or [],
    )
