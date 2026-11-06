"""Generic RSS and Atom connector supporting conditional HTTP, lookback deduplication and ETag caching."""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import feedparser
import httpx

from backend.connectors.base import BaseConnector
from backend.connectors.models import FetchBatch, SourceItem
from backend.ingestion.normalizer import NormalizationError, normalize_record

logger = logging.getLogger(__name__)


class RSSConnector(BaseConnector):
    """
    Ingests and normalises financial news from RSS 2.0 and Atom feeds.
    Features:
    - HTTP 304 Not Modified support via ETag and Last-Modified headers.
    - Robust entry identity resolution.
    - Overlapping lookback strategy for feeds without monotonically increasing IDs.
    - Relative URL resolution.
    """

    def __init__(
        self,
        connector_id: str,
        feed_url: str,
        name: Optional[str] = None,
        publisher: Optional[str] = None,
        enabled: bool = True,
        poll_interval_seconds: int = 300,
        tickers: Optional[List[str]] = None,
        language: str = "en",
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(
            connector_id=connector_id,
            name=name or f"RSS Feed: {feed_url}",
            enabled=enabled,
            poll_interval_seconds=poll_interval_seconds,
        )
        self.feed_url = feed_url
        self.publisher = publisher or name or "RSS Publisher"
        self.tickers = tickers or []
        self.language = language
        self._http_client = http_client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client and not self._http_client.is_closed:
            return self._http_client
        return httpx.AsyncClient(timeout=20.0, follow_redirects=True)

    def _parse_cursor(self, cursor: Optional[str]) -> Dict[str, Any]:
        """Deserialize cursor JSON into internal dictionary."""
        if not cursor:
            return {"etag": None, "last_modified": None, "seen_ids": []}
        try:
            data = json.loads(cursor)
            if isinstance(data, dict):
                return {
                    "etag": data.get("etag"),
                    "last_modified": data.get("last_modified"),
                    "seen_ids": data.get("seen_ids", [])[-100:],  # Rolling window of last 100 seen
                }
        except (json.JSONDecodeError, TypeError):
            pass
        return {"etag": None, "last_modified": None, "seen_ids": []}

    def _serialize_cursor(self, etag: Optional[str], last_modified: Optional[str], seen_ids: List[str]) -> str:
        """Serialize state into opaque cursor string."""
        return json.dumps({
            "etag": etag,
            "last_modified": last_modified,
            "seen_ids": seen_ids[-100:],  # Keep bounded rolling buffer
        })

    async def fetch(self, cursor: Optional[str] = None) -> FetchBatch:
        """
        Fetch feed items with conditional HTTP headers (ETag / If-Modified-Since).
        Returns FetchBatch with new items and next cursor.
        """
        self.record_attempt()
        cursor_state = self._parse_cursor(cursor)

        headers = {
            "User-Agent": "TickerWatch/0.1 (+https://github.com/tickerwatch/tickerwatch; financial intelligence engine)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
        }

        if cursor_state.get("etag"):
            headers["If-None-Match"] = cursor_state["etag"]
        if cursor_state.get("last_modified"):
            headers["If-Modified-Since"] = cursor_state["last_modified"]

        client = await self._get_client()
        should_close = client != self._http_client

        try:
            response = await client.get(self.feed_url, headers=headers)

            if response.status_code == 304:
                # Content not modified, zero new items, preserve cursor
                self.record_success(items_count=0)
                return FetchBatch(
                    connector_id=self.connector_id,
                    items=[],
                    next_cursor=cursor,
                    raw_count=0,
                    source_status="NOT_MODIFIED",
                )

            # Raise for HTTP 4xx / 5xx to trigger retry handling
            response.raise_for_status()

            new_etag = response.headers.get("etag") or cursor_state.get("etag")
            new_last_modified = response.headers.get("last-modified") or cursor_state.get("last_modified")

            feed = feedparser.parse(response.content)

            feed_title = feed.feed.get("title") if hasattr(feed, "feed") else None
            effective_publisher = self.publisher or feed_title or "RSS Publisher"

            items: List[SourceItem] = []
            seen_ids = list(cursor_state.get("seen_ids", []))
            seen_set = set(seen_ids)

            for entry in feed.entries:
                raw_id = entry.get("id") or entry.get("guid") or entry.get("link")

                # Resolve title
                raw_title = entry.get("title", "")

                # Resolve link (handle relative URLs)
                raw_link = entry.get("link", "")
                if raw_link and not raw_link.startswith(("http://", "https://")):
                    raw_link = urljoin(self.feed_url, raw_link)

                # Resolve publication date
                raw_published = entry.get("published") or entry.get("updated") or entry.get("created")
                if not raw_published and hasattr(entry, "published_parsed") and entry.published_parsed:
                    raw_published = entry.published_parsed

                # Resolve snippet or content
                raw_content = None
                if hasattr(entry, "content") and entry.content and len(entry.content) > 0:
                    raw_content = entry.content[0].get("value")
                if not raw_content:
                    raw_content = entry.get("summary") or entry.get("description")

                # Raw metadata preservation
                metadata = {
                    "feed_url": self.feed_url,
                    "feed_title": feed_title,
                    "author": entry.get("author"),
                    "tags": [t.get("term") for t in entry.get("tags", []) if t.get("term")],
                }

                try:
                    item = normalize_record(
                        connector_id=self.connector_id,
                        publisher=effective_publisher,
                        source_type="rss",
                        raw_id=raw_id,
                        raw_title=raw_title,
                        raw_url=raw_link,
                        raw_published=raw_published,
                        raw_content=raw_content,
                        raw_metadata=metadata,
                        tickers=self.tickers,
                        language=self.language,
                    )

                    # Deduplication lookback: only include if not in recent rolling seen_ids
                    if item.source_item_id not in seen_set:
                        items.append(item)
                        seen_set.add(item.source_item_id)
                        seen_ids.append(item.source_item_id)

                except NormalizationError as ne:
                    logger.warning(
                        "Skipping malformed RSS entry",
                        extra={"connector_id": self.connector_id, "error": str(ne), "entry_title": raw_title}
                    )
                    continue

            next_cursor = self._serialize_cursor(new_etag, new_last_modified, seen_ids)
            self.record_success(items_count=len(items))

            return FetchBatch(
                connector_id=self.connector_id,
                items=items,
                next_cursor=next_cursor,
                raw_count=len(feed.entries),
                source_status="OK",
            )

        except httpx.HTTPStatusError as e:
            is_auth = e.response.status_code in (401, 403)
            self.record_failure(f"HTTP {e.response.status_code}: {e.response.text[:200]}", is_auth=is_auth)
            raise
        except Exception as e:
            self.record_failure(str(e))
            raise
        finally:
            if should_close:
                await client.aclose()

    async def ack(self, cursor: str) -> None:
        """Acknowledge cursor advancement post-durable persistence."""
        self.committed_cursor = cursor
