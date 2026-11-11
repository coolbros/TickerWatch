"""Phase 6 Offline Test Suite Part 1: Live Adapters, Resilience, Persistence & Restart Recovery (WP-K)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import struct
import httpx
import pytest
import respx

from backend.alpha.instruments import Segment
from backend.alpha.live_data_capture import LiveCaptureStore
from backend.alpha.live_instruments import LiveInstrumentStatus, LiveInstrumentUniverse
from backend.alpha.live_market_adapters import (
    DisabledMarketDataProvider,
    ProviderAuthError,
    ResilientMarketFeedRunner,
    ZerodhaKiteLiveAdapter,
)
from backend.alpha.live_news_adapters import (
    DisabledNewsSourceAdapter,
    NSECorporateLiveAdapter,
    RBIPressReleaseAdapter,
    SECEdgarLiveNewsAdapter,
    SECPressReleaseRSSAdapter,
)
from backend.alpha.market_data import MarketDataSubscription, MockStreamingMarketDataProvider
from backend.alpha.schemas import MarketEvent, MarketMessageType
from backend.alpha.storage import RawPayloadStore
from backend.connectors.models import ConnectorStatus
from backend.connectors.sec import SECConfigurationError


def _build_kite_full_packet(token_int: int, ltp_paise: int, ltt_epoch: int, bid_paise: int, ask_paise: int) -> bytes:
    header = struct.pack(">IIIIIIIIIII", token_int, ltp_paise, 50, ltp_paise, 12000, 300, 250, ltp_paise, ltp_paise, ltp_paise, ltp_paise)
    ltt = struct.pack(">I", ltt_epoch) + (b"\x00" * 16)
    bids = b"".join(struct.pack(">IIHH", 100 * (i + 1), bid_paise - i * 5, i + 1, 0) for i in range(5))
    asks = b"".join(struct.pack(">IIHH", 120 * (i + 1), ask_paise + i * 5, i + 1, 0) for i in range(5))
    pkt = header + ltt + bids + asks
    assert len(pkt) == 184
    return struct.pack(">HH", 1, 184) + pkt


@pytest.mark.asyncio
async def test_01_06_07_08_rbi_live_adapter_ist_localization_dedup_and_revisions(tmp_path: Path) -> None:
    rss_xml_v1 = b"""<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0"><channel><title>RBI Press Releases</title>
      <item>
        <title>Monetary Policy Statement: Repo Rate Unchanged</title>
        <link>https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=63958</link>
        <description>RBI Monetary Policy Committee decision summary.</description>
        <pubDate>Wed, 11 Nov 2026 18:10:00</pubDate>
      </item>
    </channel></rss>"""

    rss_xml_v2 = b"""<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0"><channel><title>RBI Press Releases</title>
      <item>
        <title>Monetary Policy Statement: Repo Rate Unchanged - Clarification</title>
        <link>https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=63958</link>
        <description>Updated corridor rate annex.</description>
        <pubDate>Wed, 11 Nov 2026 18:25:00</pubDate>
      </item>
    </channel></rss>"""

    raw_store = RawPayloadStore(base_dir=str(tmp_path / "raw"))
    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://www.rbi.org.in/pressreleases_rss.xml").mock(
            side_effect=[
                httpx.Response(200, content=rss_xml_v1, headers={"etag": 'W/"v1"', "last-modified": "Wed, 11 Nov 2026 12:41:00 GMT"}),
                httpx.Response(304),
                httpx.Response(200, content=rss_xml_v2, headers={"etag": 'W/"v2"', "last-modified": "Wed, 11 Nov 2026 12:56:00 GMT"}),
            ]
        )
        async with httpx.AsyncClient() as client:
            adapter = RBIPressReleaseAdapter(enabled=True, raw_payload_store=raw_store, http_client=client)
            evs1, cur1, q1 = await adapter.fetch_news_events(cursor=None)
            assert len(evs1) == 1 and len(q1) == 0
            # Verify naive IST 18:10:00 became 12:40:00 UTC!
            assert evs1[0].published_at_utc == datetime(2026, 11, 11, 12, 40, tzinfo=timezone.utc)
            assert evs1[0].raw_payload_ref and Path(evs1[0].raw_payload_ref).exists()

            # Conditional 304 poll yields zero duplicates
            evs_304, cur2, _ = await adapter.fetch_news_events(cursor=cur1)
            assert len(evs_304) == 0 and cur2 == cur1

            # Revised content same PRID links via replaces_event_id & version=2
            evs2, cur3, _ = await adapter.fetch_news_events(cursor=cur2)
            assert len(evs2) == 1
            assert evs2[0].version == 2
            assert evs2[0].is_correction is True
            assert evs2[0].replaces_event_id == evs1[0].event_id
            assert evs2[0].published_at_utc == datetime(2026, 11, 11, 12, 55, tzinfo=timezone.utc)
            assert route.call_count == 3


@pytest.mark.asyncio
async def test_sec_press_sec_edgar_and_disabled_news_adapters(tmp_path: Path) -> None:
    raw_store = RawPayloadStore(base_dir=str(tmp_path / "raw_sec"))
    sec_json = {
        "name": "Apple Inc.",
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-26-000100", "0000320193-26-000101"],
                "filingDate": ["2026-11-10", "2026-11-11"],
                "acceptanceDateTime": ["2026-11-10T21:30:00.000Z", "2026-11-11T14:00:00.000Z"],
                "form": ["8-K", "8-K/A"],
                "primaryDocument": ["f8k.htm", "f8ka.htm"],
                "primaryDocDescription": ["Current report", "Amended current report"],
            }
        },
    }
    sec_rss = b"""<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0"><channel><title>SEC Press</title>
      <item>
        <title>SEC Charges Firm With Reporting Failure</title>
        <link>https://www.sec.gov/news/press-release/2026-99</link>
        <description>Official press release.</description>
        <pubDate>Wed, 11 Nov 2026 10:00:00 -0500</pubDate>
      </item>
    </channel></rss>"""

    with respx.mock(assert_all_called=True) as router:
        router.get("https://data.sec.gov/submissions/CIK0000320193.json").mock(return_value=httpx.Response(200, json=sec_json))
        router.get("https://www.sec.gov/news/pressreleases.rss").mock(return_value=httpx.Response(200, content=sec_rss))
        async with httpx.AsyncClient() as client:
            edgar = SECEdgarLiveNewsAdapter(
                user_agent="TickerWatchTest/0.6 test@example.com",
                tracked_ciks={"0000320193": "AAPL"},
                raw_payload_store=raw_store,
                http_client=client,
            )
            e_evs, _, _ = await edgar.fetch_news_events()
            assert len(e_evs) == 2
            assert any(e.is_correction for e in e_evs)

            press = SECPressReleaseRSSAdapter(raw_payload_store=raw_store, http_client=client)
            p_evs, _, _ = await press.fetch_news_events()
            assert len(p_evs) == 1 and p_evs[0].published_at_utc == datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)

    # Disabled feeds never fabricate events & keep clean disabled health
    nse_dis = NSECorporateLiveAdapter(enabled=False)
    bse_dis = DisabledNewsSourceAdapter("bse_notices", "BSE Notices", "HTTP 403 WAF")
    assert (await nse_dis.fetch_news_events())[0] == []
    assert (await bse_dis.fetch_news_events())[0] == []
    assert (await nse_dis.health()).status == ConnectorStatus.DISABLED
    assert (await bse_dis.health()).status == ConnectorStatus.DISABLED


@pytest.mark.asyncio
async def test_02_auth_failure_redacts_secrets() -> None:
    secret_tok = "SUPER_SECRET_ACCESS_TOKEN_XYZ"
    secret_key = "MY_API_KEY_12345"

    # Missing creds blocks cleanly
    kite_empty = ZerodhaKiteLiveAdapter(api_key="", access_token="")
    with pytest.raises(ProviderAuthError):
        await kite_empty.connect()

    # Transport error redacts keys cleanly
    def failing_factory():
        raise RuntimeError(f"401 Unauthorized for api_key={secret_key}&access_token={secret_tok}")

    kite_err = ZerodhaKiteLiveAdapter(api_key=secret_key, access_token=secret_tok, frame_source_factory=failing_factory)
    with pytest.raises(RuntimeError):
        await kite_err.connect()
    assert secret_tok not in (kite_err.last_error or "")
    assert secret_key not in (kite_err.last_error or "")
    assert "REDACTED" in (kite_err.last_error or "")

    # Bad SEC UA raises SECConfigurationError and sets AUTH_ERROR without crashing health
    bad_sec = SECEdgarLiveNewsAdapter(user_agent="bad-ua-no-email", tracked_ciks={"0000320193": "AAPL"})
    h = await bad_sec.health()
    assert h.status == ConnectorStatus.AUTH_ERROR
    with pytest.raises(SECConfigurationError):
        await bad_sec.fetch_news_events()


@pytest.mark.asyncio
async def test_03_04_05_reconnect_sequence_gaps_staleness_and_kite_decoder(tmp_path: Path) -> None:
    # A. Mocked Kite 184B binary WS packets
    pkt1 = _build_kite_full_packet(256265, 2450050, 1794382200, 2450000, 2450100)
    attempts = {"n": 0}

    async def flaky_stream():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionResetError("Simulated transient WS drop")
        yield pkt1

    kite = ZerodhaKiteLiveAdapter(
        api_key="test_key",
        access_token="test_tok",
        token_to_instrument={"256265": "NSE:NIFTY_FUT:CURRENT"},
        frame_source_factory=flaky_stream,
    )
    runner = ResilientMarketFeedRunner(
        provider=kite,
        base_backoff_seconds=0.01,
        max_backoff_seconds=0.05,
        max_reconnects=3,
    )
    tel = await runner.run(
        subscriptions=[MarketDataSubscription(instrument_id="NSE:NIFTY_FUT:CURRENT", contract_token="256265")],
        max_events=1,
    )
    assert tel.reconnect_attempts == 1
    assert tel.events_received == 1 and tel.events_dispatched == 1

    # B. Sequence Gap Resync Callback & Staleness Detection
    resyncs = []
    checkpoints = []
    provider = MockStreamingMarketDataProvider(stale_timeout_seconds=0.02)

    async def on_resync(inst_id: str, last_seq: int) -> None:
        resyncs.append((inst_id, last_seq))

    async def on_cp(seq_map: dict, ts: datetime) -> None:
        checkpoints.append((seq_map, ts))

    t0 = datetime(2026, 11, 11, 5, 0, tzinfo=timezone.utc)
    e1 = MarketEvent(provider="MOCK", instrument_id="NSE:NIFTY_FUT:CURRENT", message_type=MarketMessageType.QUOTE, local_received_at_utc=t0, local_monotonic_ns=100, sequence_number=1, bid_price=Decimal("24500"), ask_price=Decimal("24501"))
    # Jump sequence 1 -> 5 (GAP) + sleep briefly to trigger staleness check cleanly
    e_gap = MarketEvent(provider="MOCK", instrument_id="NSE:NIFTY_FUT:CURRENT", message_type=MarketMessageType.QUOTE, local_received_at_utc=t0 + timedelta(seconds=1), local_monotonic_ns=200, sequence_number=5, bid_price=Decimal("24505"), ask_price=Decimal("24506"))

    await provider.inject_event(e1)
    runner2 = ResilientMarketFeedRunner(
        provider=provider,
        heartbeat_interval_seconds=0.01,
        on_resync_required=on_resync,
        on_checkpoint=on_cp,
    )

    async def delayed_inject() -> None:
        await asyncio.sleep(0.05)
        await provider.inject_event(e_gap)

    inj_task = asyncio.create_task(delayed_inject())
    tel2 = await runner2.run(max_events=2)
    await inj_task

    assert tel2.gap_count == 1
    assert tel2.resync_requests == 1
    assert resyncs == [("NSE:NIFTY_FUT:CURRENT", 5)]
    assert tel2.heartbeat_timeouts >= 1
    assert len(checkpoints) >= 1 and tel2.last_checkpoint_utc is not None

    # Disabled market adapter check
    td_dis = DisabledMarketDataProvider("TRUEDATA", "SUBSCRIPTION_NOT_CONFIGURED")
    with pytest.raises(ProviderAuthError):
        await td_dis.connect()


def test_09_10_persistent_capture_restart_recovery_and_instruments(tmp_path: Path) -> None:
    cap_db = tmp_path / "live_cap.db"
    mkt_dir = tmp_path / "live_mkt"
    cap1 = LiveCaptureStore(cap_db, mkt_dir, buffer_size=2)
    cap1.save_checkpoint("rbi_press_rss", cursor_json='{"etag":"abc"}', sequence_map={"NSE:NIFTY_FUT:CURRENT": 42})
    cap1.record_connection_event("ZERODHA_KITE", "STREAMING", "ok")
    cap1.record_feed_incident("ZERODHA_KITE", "GAP", "missed seq 2", instrument_id="NSE:NIFTY_FUT:CURRENT", expected_sequence=2, observed_sequence=3, gap_size=1)

    t0 = datetime(2026, 11, 11, 5, 0, tzinfo=timezone.utc)
    cap1.record_market_event(
        MarketEvent(provider="MOCK", instrument_id="NSE:NIFTY_FUT:CURRENT", message_type=MarketMessageType.QUOTE, local_received_at_utc=t0, local_monotonic_ns=100, sequence_number=1, bid_price=Decimal("24500"), ask_price=Decimal("24501"))
    )
    cap1.flush_market_archive()

    # Simulated process restart: fresh LiveCaptureStore opens same DB cleanly
    cap2 = LiveCaptureStore(cap_db, mkt_dir, buffer_size=2)
    cur, seqs = cap2.load_checkpoint("rbi_press_rss")
    assert cur == '{"etag":"abc"}' and seqs["NSE:NIFTY_FUT:CURRENT"] == 42
    stats = cap2.get_storage_stats()
    assert stats.total_market_snapshots == 1 and stats.total_feed_incidents == 1 and stats.partitioned_jsonl_gz_files == 1

    # Universe contract non-expiry & separated mapping check
    univ = LiveInstrumentUniverse(as_of_date=date(2026, 11, 11))
    assert univ.validate_contract_not_expired("NIFTY", Segment.FUTIDX, date(2026, 10, 29), date(2026, 11, 11)) == LiveInstrumentStatus.EXPIRED
    corp_map = univ.map_event_separated("TCS", "EARNINGS")
    macro_map = univ.map_event_separated("RBI", "MACRO")
    assert corp_map.cash_equity[0].instrument_id == "NSE:TCS:EQ"
    assert macro_map.index_futures[0].instrument_id == "NSE:NIFTY_FUT:CURRENT"
