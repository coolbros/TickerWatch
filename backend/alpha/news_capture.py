"""Real-Time News and Corporate Disclosure Capture Architecture."""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
import feedparser
import httpx

from backend.alpha.instruments import get_instrument_master
from backend.alpha.schemas import NewsEvent, QualityQuarantineRecord
from backend.alpha.storage import RawPayloadStore
from backend.alpha.timestamps import (
    LatencyTracker,
    LocalTimestamp,
    get_current_utc,
    get_monotonic_ns,
    parse_utc_iso,
    to_utc_iso,
)
from backend.connectors.models import ConnectorHealth, ConnectorStatus
from backend.database.repository import Repository
from backend.ingestion.normalizer import parse_timestamp_to_utc

logger = logging.getLogger("tickerwatch.alpha.news")


class BaseNewsAdapter(ABC):
    """Abstract interface for regulatory and news feed adapters."""

    def __init__(
        self,
        adapter_id: str,
        name: str,
        enabled: bool = False,
        poll_interval_seconds: int = 300,
        rate_limit_per_sec: float = 1.0,
    ) -> None:
        self.adapter_id = adapter_id
        self.name = name
        self.enabled = enabled
        self.poll_interval_seconds = poll_interval_seconds
        self.rate_limit_per_sec = rate_limit_per_sec
        self.last_poll_at_utc: Optional[datetime] = None
        self.last_success_at_utc: Optional[datetime] = None
        self.consecutive_failures: int = 0
        self.last_error: Optional[str] = None
        self.committed_cursor: Optional[str] = None

    @abstractmethod
    async def fetch_news_events(
        self, cursor: Optional[str] = None
    ) -> Tuple[List[NewsEvent], Optional[str], List[QualityQuarantineRecord]]:
        """
        Poll or stream new announcements.
        Returns:
            Tuple of (valid_normalized_events, next_cursor, quarantined_records)
        """
        raise NotImplementedError

    async def health(self) -> ConnectorHealth:
        """Inspect adapter operational health."""
        if not self.enabled:
            status = ConnectorStatus.DISABLED
        elif self.consecutive_failures >= 3:
            status = ConnectorStatus.DEGRADED
        else:
            status = ConnectorStatus.HEALTHY

        return ConnectorHealth(
            connector_id=self.adapter_id,
            enabled=self.enabled,
            status=status,
            poll_interval_seconds=self.poll_interval_seconds,
            last_successful_at_utc=self.last_success_at_utc,
            consecutive_failures=self.consecutive_failures,
            last_error=self.last_error,
        )

    async def ack(self, cursor: str) -> None:
        """Advance acknowledged cursor."""
        self.committed_cursor = cursor


class NSECorporateAnnouncementsAdapter(BaseNewsAdapter):
    """
    Adapter for National Stock Exchange of India (NSE) corporate announcements.
    Supports official RSS feeds and structured JSON feeds with revision tracking.
    Disabled by default until explicit authorization and connectivity are configured.
    """

    def __init__(
        self,
        adapter_id: str = "nse_corporate",
        name: str = "NSE Corporate Announcements & Filings",
        feed_url: Optional[str] = None,
        enabled: bool = False,
        poll_interval_seconds: int = 300,
        raw_payload_store: Optional[RawPayloadStore] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(
            adapter_id=adapter_id,
            name=name,
            enabled=enabled,
            poll_interval_seconds=poll_interval_seconds,
            rate_limit_per_sec=0.5,
        )
        self.feed_url = feed_url or "https://nsearchives.nseindia.com/content/RSS/Corporate_Announcements.xml"
        self.raw_store = raw_payload_store or RawPayloadStore()
        self._http_client = http_client
        self._known_events: Dict[str, NewsEvent] = {}  # content_hash / headline -> event_id
        self.instrument_master = get_instrument_master()

    def _extract_entity(self, title: str, desc: str) -> Tuple[str, List[str]]:
        """Extract canonical corporate entity from announcement text."""
        upper = f"{title} {desc}".upper()
        if "RELIANCE" in upper or "RIL" in upper:
            return "RELIANCE", ["RELIANCE.NS"]
        if "INFOSYS" in upper or "INFY" in upper:
            return "INFY", ["INFY.NS"]
        if "TATA CONSULTANCY" in upper or "TCS" in upper:
            return "TCS", ["TCS.NS"]
        if "HDFC BANK" in upper or "HDFCBANK" in upper:
            return "HDFCBANK", ["HDFCBANK.NS"]
        if "RESERVE BANK" in upper or "RBI" in upper or "MONETARY POLICY" in upper:
            return "RBI", ["NIFTY"]

        # Regex fallback for NSE symbols e.g. [SYMBOL] or SYMBOL -
        match = re.search(r"\b([A-Z0-9]{3,10})\b\s*[-:]", title)
        if match:
            sym = match.group(1)
            return sym, [f"{sym}.NS"]

        return "UNKNOWN", []

    def _classify_family(self, title: str, desc: str) -> str:
        """Categorize into research event family."""
        upper = f"{title} {desc}".upper()
        if any(w in upper for w in ["FINANCIAL RESULT", "EARNINGS", "PROFIT", "LOSS", "Q1", "Q2", "Q3", "Q4", "AUDITED"]):
            return "EARNINGS"
        if any(w in upper for w in ["ORDER", "CONTRACT", "BAGS", "AWARDED", "TENDER", "WINS"]):
            return "CONTRACTS"
        if any(w in upper for w in ["MONETARY POLICY", "REPO RATE", "INFLATION", "CPI", "GDP", "UNION BUDGET"]):
            return "MACRO"
        return "GENERAL"

    def _check_correction(self, title: str) -> Tuple[bool, Optional[str]]:
        """Identify if disclosure is a corrigendum, clarification, or revision."""
        upper = title.upper()
        is_corr = any(w in upper for w in ["CLARIFICATION", "REVISED", "CORRIGENDUM", "AMENDMENT", "ADDENDUM"])
        return is_corr, None

    async def fetch_news_events(
        self, cursor: Optional[str] = None
    ) -> Tuple[List[NewsEvent], Optional[str], List[QualityQuarantineRecord]]:
        now_utc = get_current_utc()
        now_mono = get_monotonic_ns()
        self.last_poll_at_utc = now_utc

        if not self.enabled:
            return [], cursor, []

        cursor_state = {}
        if cursor:
            try:
                cursor_state = json.loads(cursor)
            except Exception:
                pass

        headers = {
            "User-Agent": "TickerWatch-Alpha/2.0 (Research Pipeline; +http://localhost)",
            "Accept": "application/rss+xml, application/xml, text/xml",
        }
        if cursor_state.get("etag"):
            headers["If-None-Match"] = cursor_state["etag"]
        if cursor_state.get("last_modified"):
            headers["If-Modified-Since"] = cursor_state["last_modified"]

        client = self._http_client or httpx.AsyncClient(timeout=15.0, follow_redirects=True)
        should_close = self._http_client is None

        try:
            resp = await client.get(self.feed_url, headers=headers)
            if resp.status_code == 304:
                self.last_success_at_utc = now_utc
                self.consecutive_failures = 0
                return [], cursor, []

            resp.raise_for_status()
            self.last_success_at_utc = now_utc
            self.consecutive_failures = 0

            raw_bytes = resp.content
            feed = feedparser.parse(raw_bytes)

            events: List[NewsEvent] = []
            quarantined: List[QualityQuarantineRecord] = []
            seen_hashes = set(cursor_state.get("seen_hashes", []))

            for entry in feed.entries:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                desc = entry.get("description", entry.get("summary", "")).strip()
                published_raw = entry.get("published", entry.get("updated"))

                # Quarantine checks
                if not title:
                    quarantined.append(
                        QualityQuarantineRecord(
                            source_type="NEWS",
                            source_reference=link or "missing_link",
                            quarantine_reason="Fatal: Empty or missing headline",
                            raw_payload_snippet=str(entry)[:200],
                        )
                    )
                    continue

                # SHA-256 fingerprint
                raw_hash = hashlib.sha256(f"{title}|{link}|{desc}".encode("utf-8")).hexdigest()
                if raw_hash in seen_hashes:
                    continue

                published_utc = parse_timestamp_to_utc(published_raw)
                entity_key, tickers = self._extract_entity(title, desc)
                event_family = self._classify_family(title, desc)
                is_correction, _ = self._check_correction(title)

                # Multi-tier instrument mapping
                mappings = self.instrument_master.resolve_event_mapping(
                    entity_id=entity_key,
                    event_family=event_family,
                    tickers=tickers,
                )

                # Save raw payload
                raw_ref = self.raw_store.save_payload(
                    provider_id=self.adapter_id,
                    content_hash=raw_hash,
                    payload_bytes=raw_bytes,
                    received_at=now_utc,
                )

                # Check if this corrects a prior observed event
                replaces_id = None
                version = 1
                if is_correction:
                    # Look up if an earlier entity announcement exists
                    for h, old_ev in self._known_events.items():
                        if entity_key in old_ev.entity_ids and old_ev.entity_ids[0] != "UNKNOWN":
                            replaces_id = old_ev.event_id
                            version = old_ev.version + 1
                            break

                event = NewsEvent(
                    provider_id=self.adapter_id,
                    source_name=self.name,
                    source_url=link or f"https://nseindia.com/announcements/{raw_hash[:8]}",
                    headline=title,
                    body_text=desc if desc else None,
                    published_at_utc=published_utc,
                    provider_observed_at_utc=published_utc,
                    local_received_at_utc=now_utc,
                    local_monotonic_ns=now_mono,
                    ingestion_completed_at_utc=get_current_utc(),
                    version=version,
                    is_correction=is_correction,
                    replaces_event_id=replaces_id,
                    language="en",
                    entity_ids=[entity_key] if entity_key != "UNKNOWN" else [],
                    instrument_mappings=mappings,
                    content_hash=raw_hash,
                    raw_payload_ref=raw_ref,
                )
                events.append(event)
                seen_hashes.add(raw_hash)
                self._known_events[raw_hash] = event

            # Build next cursor
            next_state = {
                "etag": resp.headers.get("etag"),
                "last_modified": resp.headers.get("last-modified"),
                "seen_hashes": list(seen_hashes)[-200:],  # keep last 200
            }
            return events, json.dumps(next_state), quarantined

        except Exception as exc:
            self.consecutive_failures += 1
            self.last_error = str(exc)
            logger.error(f"Error fetching from {self.adapter_id}: {exc}")
            raise
        finally:
            if should_close:
                await client.aclose()


class CanonicalNewsIngestionManager:
    """Coordinates multi-source news capture, SQLite article sync, and revision handling."""

    def __init__(
        self,
        repository: Repository,
        raw_store: Optional[RawPayloadStore] = None,
    ) -> None:
        self.repo = repository
        self.raw_store = raw_store or RawPayloadStore()
        self._adapters: Dict[str, BaseNewsAdapter] = {}
        self.quarantine_log: List[QualityQuarantineRecord] = []

    def register_adapter(self, adapter: BaseNewsAdapter) -> None:
        """Register a news feed adapter."""
        self._adapters[adapter.adapter_id] = adapter

    def get_adapter(self, adapter_id: str) -> Optional[BaseNewsAdapter]:
        return self._adapters.get(adapter_id)

    async def run_adapter_cycle(self, adapter_id: str) -> Dict[str, Any]:
        """Execute one ingestion cycle for a news adapter."""
        adapter = self._adapters.get(adapter_id)
        if not adapter:
            raise ValueError(f"Unknown adapter: {adapter_id}")

        cursor = self.repo.get_connector_cursor(adapter_id)
        events, next_cursor, quarantined = await adapter.fetch_news_events(cursor)

        self.quarantine_log.extend(quarantined)

        inserted = 0
        skipped = 0

        # Sync to SQLite articles table for backward compatibility with Phase 1
        with self.repo._get_conn() as conn:
            cursor_db = conn.cursor()
            for ev in events:
                try:
                    # Insert into articles
                    cursor_db.execute(
                        """INSERT OR IGNORE INTO articles (
                               connector_id, source_item_id, publisher, source_type,
                               title, url, published_at_utc, observed_at_utc,
                               content_text, content_hash, raw_metadata, tickers, created_at_utc
                           ) VALUES (?, ?, ?, 'regulatory_news', ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                        (
                            ev.provider_id,
                            ev.event_id,
                            ev.source_name,
                            ev.headline,
                            ev.source_url,
                            to_utc_iso(ev.published_at_utc),
                            to_utc_iso(ev.local_received_at_utc),
                            ev.body_text or "",
                            ev.content_hash,
                            json.dumps({"raw_payload_ref": ev.raw_payload_ref, "version": ev.version, "is_correction": ev.is_correction}),
                            json.dumps([m.instrument_id for m in ev.instrument_mappings]),
                            to_utc_iso(get_current_utc()),
                        ),
                    )
                    if cursor_db.rowcount > 0:
                        inserted += 1
                        ev.canonical_article_id = cursor_db.lastrowid
                    else:
                        skipped += 1
                except Exception as exc:
                    logger.warning(f"Failed to persist article {ev.event_id}: {exc}")

            if next_cursor:
                cursor_db.execute(
                    """INSERT INTO connector_state (connector_id, cursor, last_successful_fetch, updated_at_utc)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(connector_id) DO UPDATE SET
                           cursor = excluded.cursor,
                           last_successful_fetch = excluded.last_successful_fetch,
                           updated_at_utc = excluded.updated_at_utc;""",
                    (adapter_id, next_cursor, to_utc_iso(get_current_utc()), to_utc_iso(get_current_utc())),
                )
            conn.commit()

        if next_cursor:
            await adapter.ack(next_cursor)

        return {
            "adapter_id": adapter_id,
            "events_captured": len(events),
            "inserted": inserted,
            "skipped": skipped,
            "quarantined": len(quarantined),
            "status": "SUCCESS",
        }
