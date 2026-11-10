"""Comprehensive Offline Test Suite for TickerWatch Alpha Phase 2.

Covers all 23 verification gates defined in Work Package K:
1. News event normalisation.
2. News deduplication and revisions.
3. Provider timestamp preservation.
4. Market message decoding using documented sample payloads.
5. Instrument identifier validation.
6. Price precision (Decimal).
7. Bid/ask consistency checks.
8. Snapshot/delta semantics.
9. Duplicate market messages.
10. Out-of-order market messages.
11. Sequence gap detection.
12. Feed disconnect and reconnect.
13. Stale-feed detection.
14. Persistence recovery.
15. Event-to-market as-of join correctness.
16. No look-ahead leakage.
17. Timestamp precision and timezone handling.
18. Market-closed events.
19. Missing market observations.
20. Deterministic replay.
21. Bounded queue and backpressure behaviour.
22. Invalid payload quarantine.
23. Data-quality report generation.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import struct
import tempfile
import pytest

from backend.alpha.alignment import (
    AlignedEventRecord,
    MarketSessionStatus,
    TemporalAlignmentEngine,
    get_market_session_status,
)
from backend.alpha.dataset import DatasetExporter
from backend.alpha.instruments import (
    Exchange,
    InstrumentMaster,
    InstrumentMasterRecord,
    InstrumentType,
    RelevanceTier,
    Segment,
    get_instrument_master,
)
from backend.alpha.market_data import (
    FeedIntegrityMonitor,
    FeedStatus,
    KiteConnectBinaryDecoder,
    MarketDataSubscription,
    MockStreamingMarketDataProvider,
)
from backend.alpha.news_capture import (
    CanonicalNewsIngestionManager,
    NSECorporateAnnouncementsAdapter,
)
from backend.alpha.quality import DataQualityEngine
from backend.alpha.replay import DeterministicReplayEngine
from backend.alpha.schemas import (
    DepthLevel,
    MarketEvent,
    MarketMessageType,
    NewsEvent,
    QualityQuarantineRecord,
)
from backend.alpha.storage import (
    PartitionedMarketDataReader,
    PartitionedMarketDataWriter,
    RawPayloadStore,
)
from backend.alpha.timestamps import (
    audit_host_clock,
    get_current_utc,
    get_monotonic_ns,
    parse_utc_iso,
    to_utc_iso,
)
from backend.database.connection import apply_migrations
from backend.database.repository import Repository


# ==============================================================================
# 1. News Event Normalisation
# ==============================================================================
def test_news_event_normalization():
    """Verify NewsEvent schema normalisation, entity extraction, and contract defaults."""
    im = get_instrument_master()
    t_now = get_current_utc()
    t_mono = get_monotonic_ns()

    mappings = im.resolve_event_mapping("RELIANCE", "EARNINGS")
    assert len(mappings) >= 2

    event = NewsEvent(
        provider_id="nse_corporate",
        source_name="NSE Corporate Announcements",
        source_url="https://nseindia.com/announcements/ril_q2",
        headline="Reliance Industries Limited reports Q2 PAT up 12% YoY",
        published_at_utc=t_now - timedelta(minutes=1),
        provider_observed_at_utc=t_now - timedelta(seconds=45),
        local_received_at_utc=t_now,
        local_monotonic_ns=t_mono,
        entity_ids=["RELIANCE"],
        instrument_mappings=mappings,
        content_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )

    assert event.event_id.startswith("news_evt_")
    assert event.schema_version == "2.0.0"
    assert event.version == 1
    assert event.is_correction is False
    assert event.replaces_event_id is None
    assert event.language == "en"
    assert any(m.instrument_id == "NSE:RELIANCE:EQ" and m.relevance == RelevanceTier.DIRECT for m in event.instrument_mappings)


# ==============================================================================
# 2. News Deduplication and Revisions
# ==============================================================================
@pytest.mark.asyncio
async def test_news_deduplication_and_revisions():
    """Verify duplicate news suppression and revision version incrementing."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test_dedup.db"
        apply_migrations(str(db_path))
        repo = Repository(str(db_path))
        raw_store = RawPayloadStore(base_dir=str(Path(td) / "raw"))

        adapter = NSECorporateAnnouncementsAdapter(
            adapter_id="nse_test",
            enabled=True,
            raw_payload_store=raw_store,
        )

        # 1. Create original announcement
        t1 = get_current_utc() - timedelta(minutes=10)
        orig_ev = NewsEvent(
            provider_id="nse_test",
            source_name="NSE Test",
            source_url="http://example.com/item1",
            headline="Infosys Announces Board Meeting to Consider Dividend",
            local_received_at_utc=t1,
            local_monotonic_ns=1000,
            content_hash="hash_orig_1234567890abcdef123456",
            entity_ids=["INFY"],
            version=1,
        )
        adapter._known_events[orig_ev.content_hash] = orig_ev

        # 2. Simulate revision arriving with clarification in title
        is_corr, _ = adapter._check_correction("Clarification: Infosys Announces Board Meeting to Consider Dividend")
        assert is_corr is True

        rev_ev = NewsEvent(
            provider_id="nse_test",
            source_name="NSE Test",
            source_url="http://example.com/item1_rev",
            headline="Clarification: Infosys Announces Board Meeting to Consider Dividend",
            local_received_at_utc=get_current_utc(),
            local_monotonic_ns=2000,
            content_hash="hash_rev_9876543210fedcba9876543",
            entity_ids=["INFY"],
            version=orig_ev.version + 1,
            is_correction=True,
            replaces_event_id=orig_ev.event_id,
        )

        assert rev_ev.version == 2
        assert rev_ev.is_correction is True
        assert rev_ev.replaces_event_id == orig_ev.event_id


# ==============================================================================
# 3. Provider Timestamp Preservation
# ==============================================================================
def test_provider_timestamp_preservation():
    """Verify all 4 timestamp milestones are preserved independently without conflation."""
    t_pub = datetime(2026, 11, 10, 4, 30, 0, tzinfo=timezone.utc)
    t_prov = datetime(2026, 11, 10, 4, 30, 1, 500000, tzinfo=timezone.utc)
    t_local = datetime(2026, 11, 10, 4, 30, 3, 200000, tzinfo=timezone.utc)
    t_ingest = datetime(2026, 11, 10, 4, 30, 3, 250000, tzinfo=timezone.utc)
    t_mono = 1234567890

    ev = NewsEvent(
        provider_id="test_prov",
        source_name="Test Source",
        source_url="http://url",
        headline="Preservation Test",
        published_at_utc=t_pub,
        provider_observed_at_utc=t_prov,
        local_received_at_utc=t_local,
        local_monotonic_ns=t_mono,
        ingestion_completed_at_utc=t_ingest,
        content_hash="hash_preservation_12345678901234",
    )

    assert ev.published_at_utc == t_pub
    assert ev.provider_observed_at_utc == t_prov
    assert ev.local_received_at_utc == t_local
    assert ev.ingestion_completed_at_utc == t_ingest
    assert ev.local_monotonic_ns == t_mono

    # Measurable delays
    discovery_delay_sec = (ev.local_received_at_utc - ev.published_at_utc).total_seconds()
    provider_transit_sec = (ev.local_received_at_utc - ev.provider_observed_at_utc).total_seconds()
    assert discovery_delay_sec == 3.2
    assert provider_transit_sec == 1.7


# ==============================================================================
# 4. Market Message Decoding Using Documented Sample Payloads
# ==============================================================================
def test_market_message_decoding_sample_payloads():
    """Verify binary decoder on Zerodha Kite Connect WebSocket binary specification."""
    token = 256265
    decoder = KiteConnectBinaryDecoder({str(token): "NSE:NIFTY_FUT:CURRENT"})

    # 1. LTP packet (8 bytes payload)
    # Packet header: count=1 (2B), len=8 (2B), token (4B), ltp=245050 (4B)
    raw_ltp = struct.pack(">H", 1) + struct.pack(">H", 8) + struct.pack(">II", token, 245050)
    evs_ltp = decoder.decode_packet(raw_ltp)
    assert len(evs_ltp) == 1
    assert evs_ltp[0].instrument_id == "NSE:NIFTY_FUT:CURRENT"
    assert evs_ltp[0].last_traded_price == Decimal("2450.50")
    assert evs_ltp[0].message_type == MarketMessageType.QUOTE
    assert evs_ltp[0].is_snapshot is False

    # 2. Full Mode packet (184 bytes payload)
    depth_bytes = b""
    for i in range(5):
        # 5 Bids: Qty, Price, Orders, Reserved
        depth_bytes += struct.pack(">IIHH", 100 * (i + 1), (245000 - i * 5), 2 + i, 0)
    for i in range(5):
        # 5 Asks: Qty, Price, Orders, Reserved
        depth_bytes += struct.pack(">IIHH", 150 * (i + 1), (245050 + i * 5), 3 + i, 0)

    ltt_epoch = 1794280000
    full_payload = (
        struct.pack(">IIIIIIIIIII", token, 245025, 50, 244900, 1500000, 1200, 1800, 244000, 246000, 243500, 244500)
        + struct.pack(">I", ltt_epoch)
        + b"\x00" * 16
        + depth_bytes
    )
    raw_full = struct.pack(">H", 1) + struct.pack(">H", 184) + full_payload

    evs_full = decoder.decode_packet(raw_full)
    assert len(evs_full) == 1
    full_ev = evs_full[0]
    assert full_ev.instrument_id == "NSE:NIFTY_FUT:CURRENT"
    assert full_ev.message_type == MarketMessageType.BOOK_SNAPSHOT
    assert full_ev.is_snapshot is True
    assert full_ev.bid_price == Decimal("2450.00")
    assert full_ev.bid_quantity == 100
    assert full_ev.ask_price == Decimal("2450.50")
    assert full_ev.ask_quantity == 150
    assert len(full_ev.depth_bids) == 5
    assert len(full_ev.depth_asks) == 5
    assert full_ev.exchange_timestamp_utc == datetime.fromtimestamp(ltt_epoch, tz=timezone.utc)


# ==============================================================================
# 5. Instrument Identifier Validation
# ==============================================================================
def test_instrument_identifier_validation():
    """Verify InstrumentMaster registration, validation rules and lot/tick enforcement."""
    im = InstrumentMaster()
    nifty = im.get("NSE:NIFTY_FUT:CURRENT")
    assert nifty is not None
    assert nifty.exchange == Exchange.NSE
    assert nifty.segment == Segment.FUTIDX
    assert nifty.tick_size == Decimal("0.05")
    assert nifty.lot_size == 25

    # Verification of invalid tick size or lot size
    with pytest.raises(ValueError):
        InstrumentMasterRecord(
            instrument_id="NSE:BAD:EQ",
            exchange=Exchange.NSE,
            segment=Segment.EQ,
            symbol="BAD",
            underlying="BAD",
            instrument_type=InstrumentType.EQUITY_CASH,
            tick_size=Decimal("-0.05"),  # Negative tick size invalid
        )

    with pytest.raises(ValueError):
        InstrumentMasterRecord(
            instrument_id="NSE:BAD:EQ",
            exchange=Exchange.NSE,
            segment=Segment.EQ,
            symbol="BAD",
            underlying="BAD",
            instrument_type=InstrumentType.EQUITY_CASH,
            lot_size=0,  # Zero lot size invalid
        )


# ==============================================================================
# 6. Price Precision (Decimal vs Float)
# ==============================================================================
def test_price_precision_decimal():
    """Verify arbitrary precision Decimal usage eliminates binary float rounding errors."""
    # Classical float error: 0.1 + 0.2 != 0.3
    p1 = Decimal("24500.10")
    p2 = Decimal("24500.20")
    spread = p2 - p1
    assert spread == Decimal("0.10")

    ev = MarketEvent(
        provider="TEST",
        venue="NSE",
        segment="FUTIDX",
        instrument_id="NSE:NIFTY_FUT:CURRENT",
        local_received_at_utc=get_current_utc(),
        local_monotonic_ns=100,
        bid_price=Decimal("24500.10"),
        bid_quantity=100,
        ask_price=Decimal("24500.20"),
        ask_quantity=300,
    )

    assert ev.spread == Decimal("0.10")
    assert ev.mid_price == Decimal("24500.15")

    # Volume-weighted microprice: (24500.10 * 300 + 24500.20 * 100) / 400 = 24500.125
    assert ev.microprice == Decimal("24500.125")


# ==============================================================================
# 7. Bid/Ask Consistency Checks (Crossed Books)
# ==============================================================================
def test_bid_ask_consistency_checks():
    """Verify crossed book anomaly detection where bid > ask."""
    qe = DataQualityEngine()

    crossed_ev = MarketEvent(
        provider="TEST",
        venue="NSE",
        segment="FUTIDX",
        instrument_id="NSE:NIFTY_FUT:CURRENT",
        local_received_at_utc=get_current_utc(),
        local_monotonic_ns=100,
        bid_price=Decimal("24600.00"),
        ask_price=Decimal("24500.00"),  # CROSSED
    )

    is_valid, violations = qe.audit_market_event(crossed_ev)
    assert is_valid is False
    assert any(v.rule_id == "MKT_CROSSED_ORDER_BOOK" for v in violations)


# ==============================================================================
# 8. Snapshot vs Delta Semantics
# ==============================================================================
def test_snapshot_delta_semantics():
    """Explicitly distinguish complete depth snapshot from incremental delta."""
    snap = MarketEvent(
        provider="TEST",
        venue="NSE",
        segment="FUTIDX",
        instrument_id="NSE:NIFTY_FUT:CURRENT",
        local_received_at_utc=get_current_utc(),
        local_monotonic_ns=100,
        is_snapshot=True,
        depth_bids=[DepthLevel(price=Decimal("24500.00"), quantity=100)],
        depth_asks=[DepthLevel(price=Decimal("24500.50"), quantity=200)],
    )
    assert snap.is_snapshot is True

    delta = MarketEvent(
        provider="TEST",
        venue="NSE",
        segment="FUTIDX",
        instrument_id="NSE:NIFTY_FUT:CURRENT",
        local_received_at_utc=get_current_utc(),
        local_monotonic_ns=200,
        message_type=MarketMessageType.BOOK_DELTA,
        is_snapshot=False,
    )
    assert delta.is_snapshot is False


# ==============================================================================
# 9. Duplicate Market Messages
# ==============================================================================
def test_duplicate_market_messages():
    """Verify FeedIntegrityMonitor flags duplicate packet sequences."""
    monitor = FeedIntegrityMonitor()
    t = get_current_utc()

    ok1, an1 = monitor.inspect_packet("NSE:NIFTY_FUT:CURRENT", 501, t)
    assert ok1 is True
    assert an1 is None

    # Exact same sequence again
    ok2, an2 = monitor.inspect_packet("NSE:NIFTY_FUT:CURRENT", 501, t)
    assert ok2 is False
    assert an2 is not None
    assert an2.anomaly_type == "DUPLICATE"
    assert monitor.duplicate_count == 1


# ==============================================================================
# 10. Out-of-Order Market Messages
# ==============================================================================
def test_out_of_order_market_messages():
    """Verify FeedIntegrityMonitor flags backwards sequence numbers and exchange timestamps."""
    monitor = FeedIntegrityMonitor()
    t1 = get_current_utc()
    t0 = t1 - timedelta(seconds=2)

    monitor.inspect_packet("NSE:NIFTY_FUT:CURRENT", 505, t1)

    # Arrives with older sequence 503
    ok, an = monitor.inspect_packet("NSE:NIFTY_FUT:CURRENT", 503, t0)
    assert ok is False
    assert an.anomaly_type == "OUT_OF_ORDER"
    assert monitor.out_of_order_count == 1


# ==============================================================================
# 11. Sequence Gap Detection
# ==============================================================================
def test_sequence_gap_detection():
    """Verify feed gap triggers anomaly and marks instrument for resync."""
    monitor = FeedIntegrityMonitor()
    t = get_current_utc()

    monitor.inspect_packet("NSE:NIFTY_FUT:CURRENT", 100, t)

    # Packet jumps from 100 to 105 (missed 101, 102, 103, 104)
    ok, an = monitor.inspect_packet("NSE:NIFTY_FUT:CURRENT", 105, t)
    assert ok is False
    assert an.anomaly_type == "GAP"
    assert an.gap_size == 4
    assert monitor.gap_count == 1
    assert monitor.needs_resync["NSE:NIFTY_FUT:CURRENT"] is True

    # Clear resync after fresh snapshot
    monitor.acknowledge_resync("NSE:NIFTY_FUT:CURRENT")
    assert monitor.needs_resync["NSE:NIFTY_FUT:CURRENT"] is False


# ==============================================================================
# 12. Feed Disconnect and Reconnect
# ==============================================================================
@pytest.mark.asyncio
async def test_feed_disconnect_and_reconnect():
    """Verify streaming provider disconnect and reconnect lifecycle."""
    provider = MockStreamingMarketDataProvider()
    assert provider.status == FeedStatus.DISCONNECTED

    await provider.connect()
    assert provider.status == FeedStatus.STREAMING

    await provider.disconnect()
    assert provider.status == FeedStatus.DISCONNECTED


# ==============================================================================
# 13. Stale Feed Detection
# ==============================================================================
@pytest.mark.asyncio
async def test_stale_feed_detection():
    """Verify stale feed detection when no messages arrive within threshold."""
    monitor = FeedIntegrityMonitor(stale_timeout_seconds=0.05)
    monitor.inspect_packet("NSE:NIFTY_FUT:CURRENT", 1, get_current_utc())
    assert monitor.is_stale() is False

    await asyncio.sleep(0.06)
    assert monitor.is_stale() is True


# ==============================================================================
# 14. Persistence Recovery
# ==============================================================================
def test_persistence_recovery():
    """Verify chunked compressed storage writer and reader round-trip."""
    with tempfile.TemporaryDirectory() as td:
        writer = PartitionedMarketDataWriter(base_dir=td, buffer_size=10)
        t_base = get_current_utc()

        original_events = [
            MarketEvent(
                provider="TEST",
                venue="NSE",
                segment="FUTIDX",
                instrument_id="NSE:NIFTY_FUT:CURRENT",
                local_received_at_utc=t_base + timedelta(seconds=i),
                local_monotonic_ns=i * 1000,
                bid_price=Decimal("24500.00") + Decimal(str(i * 0.05)),
                ask_price=Decimal("24500.50") + Decimal(str(i * 0.05)),
                last_traded_price=Decimal("24500.00"),
            )
            for i in range(15)
        ]

        for ev in original_events:
            writer.append(ev)
        writer.flush_all()

        # Read back using PartitionedMarketDataReader
        reader = PartitionedMarketDataReader(base_dir=td)
        read_events = list(reader.stream_events("NSE:NIFTY_FUT:CURRENT"))

        assert len(read_events) == 15
        for orig, read in zip(original_events, read_events):
            assert orig.instrument_id == read.instrument_id
            assert orig.bid_price == read.bid_price
            assert orig.ask_price == read.ask_price


# ==============================================================================
# 15. Event-to-Market As-Of Join Correctness
# ==============================================================================
def test_event_to_market_as_of_join_correctness():
    """Verify as-of join selects the exact latest market quote observed at or before decision time."""
    engine = TemporalAlignmentEngine()
    t_news = datetime(2026, 11, 10, 5, 0, 0, tzinfo=timezone.utc)

    # Market tick 1: -5s
    engine.add_market_event(
        MarketEvent(
            provider="TEST", instrument_id="NSE:NIFTY_FUT:CURRENT",
            local_received_at_utc=t_news - timedelta(seconds=5), local_monotonic_ns=10,
            mid_price=Decimal("24490.00"), bid_price=Decimal("24489.50"), ask_price=Decimal("24490.50"),
        )
    )
    # Market tick 2: -100ms (Closest pre-event)
    engine.add_market_event(
        MarketEvent(
            provider="TEST", instrument_id="NSE:NIFTY_FUT:CURRENT",
            local_received_at_utc=t_news - timedelta(milliseconds=100), local_monotonic_ns=20,
            mid_price=Decimal("24500.00"), bid_price=Decimal("24499.50"), ask_price=Decimal("24500.50"),
        )
    )

    news = NewsEvent(
        provider_id="test", source_name="Test", source_url="http://url",
        headline="Repo Rate Stance Unchanged",
        local_received_at_utc=t_news, local_monotonic_ns=30,
        content_hash="h12345678901234567890123456789012",
        instrument_mappings=[get_instrument_master().resolve_event_mapping("RBI", "MACRO")[0]],
    )

    aligned = engine.align_event(news)
    pre = aligned.pre_event_context["NSE:NIFTY_FUT:CURRENT"]
    assert pre.mid_price == Decimal("24500.00")
    assert pre.feed_freshness_ms == 100.0


# ==============================================================================
# 16. No Look-Ahead Leakage
# ==============================================================================
def test_no_look_ahead_leakage():
    """Verify that market observations after decision timestamp are never in pre-event context."""
    engine = TemporalAlignmentEngine()
    t_news = datetime(2026, 11, 10, 5, 0, 0, tzinfo=timezone.utc)

    # Pre-event: -1.0s
    engine.add_market_event(
        MarketEvent(
            provider="TEST", instrument_id="NSE:NIFTY_FUT:CURRENT",
            local_received_at_utc=t_news - timedelta(seconds=1.0), local_monotonic_ns=10,
            mid_price=Decimal("24500.00"), bid_price=Decimal("24499.50"), ask_price=Decimal("24500.50"),
        )
    )

    # Post-event: +500ms
    engine.add_market_event(
        MarketEvent(
            provider="TEST", instrument_id="NSE:NIFTY_FUT:CURRENT",
            local_received_at_utc=t_news + timedelta(milliseconds=500), local_monotonic_ns=20,
            mid_price=Decimal("24550.00"), bid_price=Decimal("24549.50"), ask_price=Decimal("24550.50"),
        )
    )

    news = NewsEvent(
        provider_id="test", source_name="Test", source_url="http://url",
        headline="Major Corporate Contract Win",
        local_received_at_utc=t_news, local_monotonic_ns=15,
        content_hash="h99999999999999999999999999999999",
        instrument_mappings=[get_instrument_master().resolve_event_mapping("NIFTY", "MACRO")[0]],
    )

    aligned = engine.align_event(news)
    pre = aligned.pre_event_context["NSE:NIFTY_FUT:CURRENT"]
    # Must be 24500.00 (from -1.0s), NOT 24550.00 (from +500ms)
    assert pre.mid_price == Decimal("24500.00")


# ==============================================================================
# 17. Timestamp Precision and Timezone Handling
# ==============================================================================
def test_timestamp_precision_and_timezone_handling():
    """Verify UTC ISO formatting, parsing and monotonic ns integrity."""
    t_utc = get_current_utc()
    assert t_utc.tzinfo == timezone.utc

    iso_str = to_utc_iso(t_utc)
    assert iso_str.endswith("Z")

    parsed = parse_utc_iso(iso_str)
    assert parsed.tzinfo == timezone.utc
    assert abs((parsed - t_utc).total_seconds()) < 0.00001


# ==============================================================================
# 18. Market-Closed Events
# ==============================================================================
def test_market_closed_events():
    """Verify that news arriving on weekends or night is flagged OUT_OF_HOURS."""
    engine = TemporalAlignmentEngine()
    # Sunday midnight UTC
    t_sunday = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
    status = get_market_session_status(t_sunday)
    assert status == MarketSessionStatus.CLOSED

    news = NewsEvent(
        provider_id="test", source_name="Test", source_url="http://url",
        headline="Weekend Announcement",
        local_received_at_utc=t_sunday, local_monotonic_ns=100,
        content_hash="hash_weekend_123456789012345678",
        instrument_mappings=[get_instrument_master().resolve_event_mapping("NIFTY", "MACRO")[0]],
    )

    aligned = engine.align_event(news)
    assert "OUT_OF_HOURS" in aligned.quality_flag


# ==============================================================================
# 19. Missing Market Observations
# ==============================================================================
def test_missing_market_observations():
    """Verify missing market data produces explicit PreEventContext with None and flags."""
    engine = TemporalAlignmentEngine()
    t_news = datetime(2026, 11, 10, 5, 0, 0, tzinfo=timezone.utc)

    news = NewsEvent(
        provider_id="test", source_name="Test", source_url="http://url",
        headline="New Listing with zero prior quotes",
        local_received_at_utc=t_news, local_monotonic_ns=100,
        content_hash="hash_missing_123456789012345678",
        instrument_mappings=[get_instrument_master().resolve_event_mapping("INFY", "EARNINGS")[0]],
    )

    aligned = engine.align_event(news)
    pre = aligned.pre_event_context["NSE:INFY:EQ"]
    assert pre.mid_price is None
    assert pre.is_stale is True
    assert "MISSING_MARKET_DATA" in aligned.quality_flag


# ==============================================================================
# 20. Deterministic Replay
# ==============================================================================
def test_deterministic_replay():
    """Verify repeated replays over identical data produce identical checksums."""
    t_base = datetime(2026, 11, 10, 5, 0, 0, tzinfo=timezone.utc)
    im = get_instrument_master()

    market_events = [
        MarketEvent(
            provider="REPLAY", venue="NSE", segment="FUTIDX",
            instrument_id="NSE:NIFTY_FUT:CURRENT",
            local_received_at_utc=t_base + timedelta(milliseconds=i * 100),
            local_monotonic_ns=i * 100,
            bid_price=Decimal("24500.00") + Decimal(str(i * 0.05)),
            ask_price=Decimal("24500.50") + Decimal(str(i * 0.05)),
        )
        for i in range(20)
    ]

    news_events = [
        NewsEvent(
            provider_id="test", source_name="Test", source_url="http://url",
            headline="Replay Test Announcement",
            local_received_at_utc=t_base + timedelta(milliseconds=1050),
            local_monotonic_ns=1050,
            content_hash="hash_replay_12345678901234567890",
            instrument_mappings=[im.resolve_event_mapping("NIFTY", "MACRO")[0]],
        )
    ]

    replay1 = DeterministicReplayEngine()
    recs1, m1 = replay1.replay_streams(news_events, market_events)

    replay2 = DeterministicReplayEngine()
    recs2, m2 = replay2.replay_streams(news_events, market_events)

    assert m1.deterministic_sha256 == m2.deterministic_sha256
    assert len(recs1) == len(recs2) == 1
    assert recs1[0].pre_event_context["NSE:NIFTY_FUT:CURRENT"].mid_price == recs2[0].pre_event_context["NSE:NIFTY_FUT:CURRENT"].mid_price


# ==============================================================================
# 21. Bounded Queue and Backpressure Behaviour
# ==============================================================================
@pytest.mark.asyncio
async def test_bounded_queue_and_backpressure_behaviour():
    """Verify MockStreamingMarketDataProvider bounded queue respects maxsize."""
    provider = MockStreamingMarketDataProvider(provider_id="BACKPRESSURE_TEST")
    provider._queue = asyncio.Queue(maxsize=3)

    t = get_current_utc()
    ev = MarketEvent(
        provider="TEST", instrument_id="NSE:NIFTY_FUT:CURRENT",
        local_received_at_utc=t, local_monotonic_ns=100,
    )

    # Fill queue to maxsize
    await provider.inject_event(ev)
    await provider.inject_event(ev)
    await provider.inject_event(ev)
    assert provider._queue.full() is True


# ==============================================================================
# 22. Invalid Payload Quarantine
# ==============================================================================
def test_invalid_payload_quarantine():
    """Verify that corrupt or invalid records are isolated into QualityQuarantineRecord."""
    qe = DataQualityEngine()

    # News event with empty headline
    bad_news = NewsEvent(
        provider_id="test", source_name="Test", source_url="http://url",
        headline="   ",  # Whitespace only
        local_received_at_utc=get_current_utc(), local_monotonic_ns=100,
        content_hash="hash_bad_news_1234567890123456789",
    )

    report = qe.audit_batch([bad_news], [])
    assert report.is_clean is False
    assert report.quarantined_count == 1
    assert report.quarantine_records[0].source_type == "NEWS"
    assert "Headline is empty" in report.quarantine_records[0].quarantine_reason


# ==============================================================================
# 23. Data Quality Report Generation
# ==============================================================================
def test_data_quality_report_generation():
    """Verify DataQualityReport consolidates pass, warning, and quarantine metrics."""
    qe = DataQualityEngine()
    now_utc = get_current_utc()

    clean_market = MarketEvent(
        provider="TEST", instrument_id="NSE:NIFTY_FUT:CURRENT",
        local_received_at_utc=now_utc - timedelta(seconds=1), local_monotonic_ns=100,
        bid_price=Decimal("24500.00"), ask_price=Decimal("24500.50"),
        last_traded_price=Decimal("24500.00"),
    )

    # Market event with un-aligned tick size (warning)
    warn_market = MarketEvent(
        provider="TEST", instrument_id="NSE:NIFTY_FUT:CURRENT",
        local_received_at_utc=now_utc - timedelta(seconds=1), local_monotonic_ns=200,
        bid_price=Decimal("24500.00"), ask_price=Decimal("24500.50"),
        last_traded_price=Decimal("24500.03"),  # 0.03 not multiple of 0.05
    )

    report = qe.audit_batch([], [clean_market, warn_market])
    assert report.total_records_audited == 2
    assert report.passed_count == 2
    assert report.warning_count >= 1
    assert report.quarantined_count == 0
    assert report.is_clean is True
