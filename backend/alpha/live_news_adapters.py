"""Phase 6 Live News & Regulatory Disclosure Adapters (WP-B).

Extends Phase 1/2 connectors and Phase 2 `BaseNewsAdapter` cleanly without duplicating
HTTP/retry logic. Enforces:
- Jurisdiction-accurate timezone localization (e.g. RBI naive strings -> Asia/Kolkata;
  SEC dates/times -> America/New_York EST/EDT -> UTC).
- Conditional HTTP polling (ETag / Last-Modified -> 304 Not Modified), exponential backoff
  via `RetryPolicy`, persistent JSON cursor checkpointing, raw payload preservation,
  duplicate suppression, and amendment/revision linkage (`replaces_event_id`).
- Explicit disabled markers for unauthorized/blocked providers (never substituting
  mock data as a live feed).
"""

from __future__ import annotations

from datetime import datetime, time as dt_time, timezone
import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse
import feedparser
import httpx

from backend.alpha.historical_import import (
    IST_ZONE,
    US_EASTERN_ZONE,
    parse_source_timestamp_to_utc,
)
from backend.alpha.instruments import get_instrument_master
from backend.alpha.news_capture import BaseNewsAdapter, NSECorporateAnnouncementsAdapter
from backend.alpha.schemas import NewsEvent, QualityQuarantineRecord
from backend.alpha.storage import RawPayloadStore
from backend.alpha.timestamps import (
    get_current_utc,
    get_monotonic_ns,
)
from backend.config import Settings, get_settings, get_ssl_context
from backend.connectors.models import ConnectorHealth, ConnectorStatus
from backend.connectors.rss import RSSConnector
from backend.connectors.sec import SECConfigurationError, SECConnector
from backend.ingestion.retry import RetryPolicy, execute_with_retry

logger = logging.getLogger("tickerwatch.alpha.live_news")

_COMMON_RSS_FORMATS = (
    "%a, %d %b %Y %H:%M:%S %Z",
    "%a, %d %b %Y %H:%M:%S %z",
    "%a, %d %b %Y %H:%M:%S",
    "%d %b %Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


def _redact_secret_fragments(text: str, secrets_to_mask: Optional[List[str]] = None) -> str:
    """Prevent accidental logging or error emission of secrets/credentials."""
    cleaned = text
    for sec in secrets_to_mask or ():
        if sec and len(sec) >= 4:
            cleaned = cleaned.replace(sec, "***REDACTED***")
    # Also redact common token/key query parameters
    cleaned = re.sub(r"(api_key|access_token|token|secret)=([^&\s]+)", r"\1=***REDACTED***", cleaned, flags=re.I)
    return cleaned


class RBIPressReleaseAdapter(BaseNewsAdapter):
    """Official Reserve Bank of India (RBI) Press Releases RSS adapter (verified live public feed)."""

    DEFAULT_FEED_URL = "https://www.rbi.org.in/pressreleases_rss.xml"

    def __init__(
        self,
        adapter_id: str = "rbi_press_rss",
        name: str = "Reserve Bank of India Press Releases RSS",
        feed_url: Optional[str] = None,
        enabled: bool = True,
        poll_interval_seconds: int = 300,
        raw_payload_store: Optional[RawPayloadStore] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        retry_policy: Optional[RetryPolicy] = None,
    ) -> None:
        super().__init__(
            adapter_id=adapter_id,
            name=name,
            enabled=enabled,
            poll_interval_seconds=poll_interval_seconds,
            rate_limit_per_sec=0.5,
        )
        self.feed_url = feed_url or self.DEFAULT_FEED_URL
        self.raw_store = raw_payload_store or RawPayloadStore()
        self._http_client = http_client
        self.retry_policy = retry_policy or RetryPolicy(max_retries=2, base_delay_seconds=0.5, max_delay_seconds=4.0, jitter=False)
        self.instrument_master = get_instrument_master()
        self._known_by_prid: Dict[str, Tuple[str, int]] = {}  # prid -> (event_id, version)

    @staticmethod
    def _extract_prid(link: str, raw_hash: str) -> str:
        qs = parse_qs(urlparse(link).query)
        prids = qs.get("prid") or qs.get("Prid")
        if prids and prids[0]:
            return f"rbi_prid_{prids[0].strip()}"
        return f"rbi_{raw_hash[:16]}"

    async def fetch_news_events(
        self, cursor: Optional[str] = None
    ) -> Tuple[List[NewsEvent], Optional[str], List[QualityQuarantineRecord]]:
        now_utc = get_current_utc()
        now_mono = get_monotonic_ns()
        self.last_poll_at_utc = now_utc

        if not self.enabled:
            return [], cursor, []

        cursor_state: Dict[str, Any] = {}
        if cursor:
            try:
                cursor_state = json.loads(cursor)
            except Exception:
                cursor_state = {}

        headers = {
            "User-Agent": "TickerWatch-Alpha-Research/0.6 (+http://localhost; regulatory-news-observer)",
            "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
        }
        if cursor_state.get("etag"):
            headers["If-None-Match"] = str(cursor_state["etag"])
        if cursor_state.get("last_modified"):
            headers["If-Modified-Since"] = str(cursor_state["last_modified"])

        client = self._http_client or httpx.AsyncClient(verify=get_ssl_context(), timeout=15.0, follow_redirects=True)
        should_close = self._http_client is None

        async def _do_get() -> httpx.Response:
            resp = await client.get(self.feed_url, headers=headers)
            if resp.status_code == 304:
                return resp
            resp.raise_for_status()
            return resp

        try:
            resp = await execute_with_retry(_do_get, self.retry_policy, operation_name=f"{self.adapter_id}.get")
            self.last_success_at_utc = now_utc
            self.consecutive_failures = 0
            self.last_error = None

            if resp.status_code == 304:
                return [], cursor, []

            raw_bytes = resp.content
            feed = feedparser.parse(raw_bytes)
            seen_hashes = set(cursor_state.get("seen_hashes", []))

            provider_header_obs_utc: Optional[datetime] = None
            lm_hdr = resp.headers.get("last-modified")
            if lm_hdr:
                try:
                    provider_header_obs_utc, _ = parse_source_timestamp_to_utc(lm_hdr, IST_ZONE, _COMMON_RSS_FORMATS)
                except Exception:
                    provider_header_obs_utc = None

            events: List[NewsEvent] = []
            quarantined: List[QualityQuarantineRecord] = []

            for entry in feed.entries:
                title = str(entry.get("title", "")).strip()
                link = str(entry.get("link", "")).strip()
                desc = str(entry.get("description", entry.get("summary", ""))).strip()
                published_raw = entry.get("published") or entry.get("updated")

                if not title:
                    quarantined.append(
                        QualityQuarantineRecord(
                            source_type="NEWS",
                            source_reference=link or self.feed_url,
                            quarantine_reason="Missing or empty RBI press release title",
                            raw_payload_snippet=str(entry)[:200],
                        )
                    )
                    continue

                raw_hash = hashlib.sha256(f"{title}|{link}|{desc}".encode("utf-8")).hexdigest()
                if raw_hash in seen_hashes:
                    continue

                pub_utc: Optional[datetime] = None
                if published_raw:
                    try:
                        # Crucial live finding: RBI publishes naive local IST (e.g. 'Wed, 11 Nov 2026 18:10:00')
                        pub_utc, _ = parse_source_timestamp_to_utc(
                            published_raw,
                            IST_ZONE,
                            _COMMON_RSS_FORMATS,
                            date_only_time_local=dt_time(18, 0),
                        )
                    except Exception:
                        pub_utc = None

                raw_ref = self.raw_store.save_payload(
                    provider_id=self.adapter_id,
                    content_hash=raw_hash,
                    payload_bytes=raw_bytes,
                    received_at=now_utc,
                )

                prid_key = self._extract_prid(link, raw_hash)
                prior = self._known_by_prid.get(prid_key)
                is_corr = any(tok in title.upper() for tok in ("CORRIGENDUM", "REVISED", "AMENDMENT", "ADDENDUM", "UPDATE"))
                replaces_id: Optional[str] = None
                version = 1
                if prior is not None:
                    replaces_id = prior[0]
                    version = prior[1] + 1
                    is_corr = True

                mappings = self.instrument_master.resolve_event_mapping(
                    entity_id="RBI",
                    event_family="MACRO",
                    tickers=["NIFTY"],
                )

                ev = NewsEvent(
                    event_id=f"{prid_key}_v{version}" if version > 1 else prid_key,
                    provider_id=self.adapter_id,
                    source_name=self.name,
                    source_url=link or self.feed_url,
                    headline=title,
                    body_text=desc or None,
                    published_at_utc=pub_utc,
                    provider_observed_at_utc=provider_header_obs_utc or pub_utc,
                    local_received_at_utc=now_utc,
                    local_monotonic_ns=now_mono,
                    ingestion_completed_at_utc=get_current_utc(),
                    version=version,
                    is_correction=is_corr,
                    replaces_event_id=replaces_id,
                    language="en",
                    entity_ids=["RBI"],
                    instrument_mappings=mappings,
                    content_hash=raw_hash,
                    raw_payload_ref=raw_ref,
                )
                events.append(ev)
                seen_hashes.add(raw_hash)
                self._known_by_prid[prid_key] = (ev.event_id, version)

            next_state = {
                "etag": resp.headers.get("etag"),
                "last_modified": lm_hdr,
                "seen_hashes": list(seen_hashes)[-500:],
            }
            return events, json.dumps(next_state), quarantined
        except Exception as exc:
            self.consecutive_failures += 1
            self.last_error = _redact_secret_fragments(str(exc))
            logger.error("Error fetching RBI press releases", extra={"adapter_id": self.adapter_id, "error": self.last_error})
            raise
        finally:
            if should_close:
                await client.aclose()


class NSECorporateLiveAdapter(NSECorporateAnnouncementsAdapter):
    """Hardened NSE Corporate Announcements adapter, strictly disabled unless authorized & reachable."""

    def __init__(
        self,
        enabled: bool = False,
        access_blocker: str = "BLOCKED_TIMEOUT_OR_UNAUTHORIZED (NSE_RSS_ENABLED=false)",
        **kwargs: Any,
    ) -> None:
        super().__init__(enabled=enabled, **kwargs)
        self.access_blocker = access_blocker

    async def fetch_news_events(
        self, cursor: Optional[str] = None
    ) -> Tuple[List[NewsEvent], Optional[str], List[QualityQuarantineRecord]]:
        if not self.enabled:
            return [], cursor, []
        events, next_cursor, quarantined = await super().fetch_news_events(cursor)
        # Upgrade any naive timestamps cleanly to IST -> UTC semantics
        for ev in events:
            if ev.published_at_utc is not None and ev.published_at_utc.tzinfo is None:
                ev.published_at_utc = ev.published_at_utc.replace(tzinfo=IST_ZONE).astimezone(timezone.utc)
        return events, next_cursor, quarantined

    async def health(self) -> ConnectorHealth:
        h = await super().health()
        if not self.enabled and not h.last_error:
            h.last_error = self.access_blocker
        return h


class DisabledNewsSourceAdapter(BaseNewsAdapter):
    """Explicitly non-live disabled adapter for unauthorized/WAF-blocked feeds (never emits mock data)."""

    def __init__(
        self,
        adapter_id: str,
        name: str,
        access_blocker: str,
        poll_interval_seconds: int = 300,
    ) -> None:
        super().__init__(adapter_id=adapter_id, name=name, enabled=False, poll_interval_seconds=poll_interval_seconds)
        self.access_blocker = access_blocker
        self.last_error = access_blocker

    async def fetch_news_events(
        self, cursor: Optional[str] = None
    ) -> Tuple[List[NewsEvent], Optional[str], List[QualityQuarantineRecord]]:
        self.last_poll_at_utc = get_current_utc()
        return [], cursor, []

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(
            connector_id=self.adapter_id,
            enabled=False,
            status=ConnectorStatus.DISABLED,
            poll_interval_seconds=self.poll_interval_seconds,
            last_error=self.access_blocker,
        )


class SECEdgarLiveNewsAdapter(BaseNewsAdapter):
    """Adapts Phase 1 SECConnector (`data.sec.gov/submissions/`) into canonical Phase 6 NewsEvents."""

    def __init__(
        self,
        user_agent: str,
        tracked_ciks: Optional[Dict[str, str]] = None,
        enabled: bool = True,
        poll_interval_seconds: int = 600,
        raw_payload_store: Optional[RawPayloadStore] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(
            adapter_id="sec_edgar_live",
            name="SEC EDGAR Submissions Live",
            enabled=enabled,
            poll_interval_seconds=poll_interval_seconds,
            rate_limit_per_sec=8.0,
        )
        self.raw_store = raw_payload_store or RawPayloadStore()
        self._user_agent = user_agent
        self._connector = SECConnector(
            connector_id="sec_edgar",
            name="SEC EDGAR Submissions",
            user_agent=user_agent,
            tracked_ciks=tracked_ciks or {},
            enabled=enabled,
            poll_interval_seconds=poll_interval_seconds,
            http_client=http_client,
        )
        self._seen_accessions: Dict[str, str] = {}

    async def fetch_news_events(
        self, cursor: Optional[str] = None
    ) -> Tuple[List[NewsEvent], Optional[str], List[QualityQuarantineRecord]]:
        now_utc = get_current_utc()
        now_mono = get_monotonic_ns()
        self.last_poll_at_utc = now_utc

        if not self.enabled:
            return [], cursor, []

        try:
            batch = await self._connector.fetch(cursor=cursor)
            self.last_success_at_utc = now_utc
            self.consecutive_failures = 0
            self.last_error = None
        except SECConfigurationError as auth_err:
            self.consecutive_failures += 1
            self.last_error = _redact_secret_fragments(str(auth_err), [self._user_agent])
            raise
        except Exception as exc:
            self.consecutive_failures += 1
            self.last_error = _redact_secret_fragments(str(exc), [self._user_agent])
            raise

        events: List[NewsEvent] = []
        for item in batch.items:
            raw_bytes = json.dumps(item.raw_metadata, sort_keys=True, default=str).encode("utf-8")
            raw_ref = self.raw_store.save_payload(
                provider_id=self.adapter_id,
                content_hash=item.content_hash,
                payload_bytes=raw_bytes,
                received_at=now_utc,
            )

            pub_utc = item.published_at_utc
            if pub_utc is None and item.published_at_original:
                try:
                    pub_utc, _ = parse_source_timestamp_to_utc(
                        item.published_at_original,
                        US_EASTERN_ZONE,
                        ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"),
                        date_only_time_local=dt_time(17, 30),
                    )
                except Exception:
                    pub_utc = None

            form = str(item.raw_metadata.get("form", "")).upper()
            is_amend = form.endswith("/A")
            replaces_id: Optional[str] = None
            base_key = f"{','.join(item.tickers)}:{form.rstrip('/A')}"
            if is_amend and base_key in self._seen_accessions:
                replaces_id = self._seen_accessions[base_key]

            ev = NewsEvent(
                event_id=item.source_item_id,
                provider_id=self.adapter_id,
                source_name=item.publisher,
                source_url=item.url,
                headline=item.title,
                body_text=item.content_text,
                published_at_utc=pub_utc,
                provider_observed_at_utc=item.observed_at_utc,
                local_received_at_utc=now_utc,
                local_monotonic_ns=now_mono,
                ingestion_completed_at_utc=get_current_utc(),
                version=2 if replaces_id else 1,
                is_correction=is_amend,
                replaces_event_id=replaces_id,
                language=item.language or "en",
                entity_ids=list(item.tickers),
                instrument_mappings=[],
                content_hash=item.content_hash,
                raw_payload_ref=raw_ref,
            )
            self._seen_accessions[base_key] = ev.event_id
            events.append(ev)

        return events, batch.next_cursor, []

    async def health(self) -> ConnectorHealth:
        if self._connector.status == ConnectorStatus.AUTH_ERROR:
            return ConnectorHealth(
                connector_id=self.adapter_id,
                enabled=self.enabled,
                status=ConnectorStatus.AUTH_ERROR,
                poll_interval_seconds=self.poll_interval_seconds,
                last_error=_redact_secret_fragments(self._connector.last_error or "Invalid SEC configuration", [self._user_agent]),
            )
        return await super().health()


class SECPressReleaseRSSAdapter(BaseNewsAdapter):
    """Wraps Phase 1 RSSConnector for official SEC Press Releases RSS."""

    DEFAULT_FEED_URL = "https://www.sec.gov/news/pressreleases.rss"

    def __init__(
        self,
        feed_url: Optional[str] = None,
        enabled: bool = True,
        poll_interval_seconds: int = 600,
        raw_payload_store: Optional[RawPayloadStore] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(
            adapter_id="sec_news_press_live",
            name="SEC Official Press Releases RSS",
            enabled=enabled,
            poll_interval_seconds=poll_interval_seconds,
            rate_limit_per_sec=0.5,
        )
        self.raw_store = raw_payload_store or RawPayloadStore()
        self._connector = RSSConnector(
            connector_id="sec_news_press",
            feed_url=feed_url or self.DEFAULT_FEED_URL,
            name="SEC Press Releases",
            publisher="SEC",
            enabled=enabled,
            poll_interval_seconds=poll_interval_seconds,
            http_client=http_client,
        )

    async def fetch_news_events(
        self, cursor: Optional[str] = None
    ) -> Tuple[List[NewsEvent], Optional[str], List[QualityQuarantineRecord]]:
        now_utc = get_current_utc()
        now_mono = get_monotonic_ns()
        self.last_poll_at_utc = now_utc
        if not self.enabled:
            return [], cursor, []

        try:
            batch = await self._connector.fetch(cursor=cursor)
            self.last_success_at_utc = now_utc
            self.consecutive_failures = 0
            self.last_error = None
        except Exception as exc:
            self.consecutive_failures += 1
            self.last_error = _redact_secret_fragments(str(exc))
            raise

        events: List[NewsEvent] = []
        for item in batch.items:
            raw_bytes = json.dumps(item.raw_metadata, sort_keys=True, default=str).encode("utf-8")
            raw_ref = self.raw_store.save_payload(self.adapter_id, item.content_hash, raw_bytes, now_utc)
            events.append(
                NewsEvent(
                    event_id=item.source_item_id,
                    provider_id=self.adapter_id,
                    source_name=item.publisher,
                    source_url=item.url,
                    headline=item.title,
                    body_text=item.content_text,
                    published_at_utc=item.published_at_utc,
                    provider_observed_at_utc=item.observed_at_utc,
                    local_received_at_utc=now_utc,
                    local_monotonic_ns=now_mono,
                    ingestion_completed_at_utc=get_current_utc(),
                    version=1,
                    language=item.language or "en",
                    entity_ids=[],
                    instrument_mappings=[],
                    content_hash=item.content_hash,
                    raw_payload_ref=raw_ref,
                )
            )
        return events, batch.next_cursor, []


def build_configured_news_adapters(
    settings: Optional[Settings] = None,
    raw_store: Optional[RawPayloadStore] = None,
) -> Dict[str, BaseNewsAdapter]:
    """Build all audited Indian & US live + disabled news adapters from configuration."""
    cfg = settings or get_settings()
    store = raw_store or RawPayloadStore()

    adapters: Dict[str, BaseNewsAdapter] = {
        "rbi_press_rss": RBIPressReleaseAdapter(enabled=True, raw_payload_store=store),
        "nse_corporate": NSECorporateLiveAdapter(
            enabled=bool(cfg.nse_rss_enabled),
            poll_interval_seconds=cfg.nse_poll_interval_seconds,
            raw_payload_store=store,
        ),
        "bse_notices": DisabledNewsSourceAdapter(
            adapter_id="bse_notices",
            name="BSE Exchange Notices & Filings",
            access_blocker="HTTP 403 Exchange WAF (requires formal BSE feed license)",
        ),
        "listed_company_ir": DisabledNewsSourceAdapter(
            adapter_id="listed_company_ir",
            name="Listed Company Investor Relations Feeds",
            access_blocker="OPT_IN_UNCONFIGURED (add authorized corporate IR RSS URLs explicitly)",
        ),
        "sec_edgar_live": SECEdgarLiveNewsAdapter(
            user_agent=cfg.sec_user_agent,
            tracked_ciks=cfg.get_sec_tracked_dict(),
            enabled=bool(cfg.sec_enabled),
            poll_interval_seconds=cfg.sec_poll_interval_seconds,
            raw_payload_store=store,
        ),
        "sec_news_press_live": SECPressReleaseRSSAdapter(
            enabled=bool(cfg.rss_enabled),
            poll_interval_seconds=cfg.sec_poll_interval_seconds,
            raw_payload_store=store,
        ),
    }
    return adapters
