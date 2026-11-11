# Phase 6 Real-Time Provider Adapters & Ingestion Architecture (`PHASE_6_LIVE_INGESTION`)

**Modules:**
- [`backend/alpha/live_news_adapters.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/live_news_adapters.py)
- [`backend/alpha/live_market_adapters.py`](file:///c:/Users/Agastya/Downloads/tickerwatch/backend/alpha/live_market_adapters.py)

---

## 1. Reuse & Non-Duplication Design

Phase 6 extends Phase 1/2 contracts directly rather than reimplementing parsing or HTTP infrastructure:
- News adapters extend Phase 2 `BaseNewsAdapter` & `NSECorporateAnnouncementsAdapter`, delegate SEC/RSS conditional fetching to Phase 1 `SECConnector` & `RSSConnector`, reuse `RetryPolicy` / `execute_with_retry`, and archive raw bytes into `RawPayloadStore`.
- Market adapters extend Phase 2 `BaseMarketDataProvider`, reuse `FeedIntegrityMonitor` & `KiteConnectBinaryDecoder`, and enforce fail-closed authentication cleanly via `ProviderAuthError` with automatic secret redaction.

---

## 2. Timestamp & Causal Provenance Guarantees

1. **RBI naive IST Localization:** Because live probing showed `https://www.rbi.org.in/pressreleases_rss.xml` emits naive IST timestamps (e.g. `Wed, 11 Nov 2026 18:10:00`), `RBIPressReleaseAdapter` applies `parse_source_timestamp_to_utc(..., default_tz=IST_ZONE)` (`18:10 IST -> 12:40 UTC`) alongside server `Last-Modified` header (`provider_observed_at_utc`), `local_received_at_utc`, `local_monotonic_ns`, and `ingestion_completed_at_utc`.
2. **SEC EST/EDT & Amendment Lineage:** `SECEdgarLiveNewsAdapter` localizes date-only SEC strings to `17:30 ET` (`America/New_York`), links `/A` amendments via `replaces_event_id` & incremented `version`, and preserves ETag / CIK cursor checkpoints.
3. **Quarantine & Gap Accounting:** Missing headlines emit `QualityQuarantineRecord` rather than dropping silently; market packet sequence gaps mark `needs_resync[instrument_id]` and invoke `on_resync_required`; queue overflow explicitly increments `queue_overflow_drops`.
4. **Disabled Source Isolation:** `NSECorporateLiveAdapter` (default `enabled=False`), `BSEDisabledAdapter`, `ZerodhaKiteLiveAdapter` (when keys missing), and `TrueDataDisabledAdapter` stay cleanly inert until authorized credentials exist — **no mock data is ever emitted as a live feed.**
