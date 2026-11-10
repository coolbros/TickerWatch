#!/usr/bin/env python3
"""Comprehensive Performance Benchmark Suite for TickerWatch Alpha Phase 2."""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import gc
import json
import os
from pathlib import Path
import struct
import sys
import time
from typing import Any, Dict, List

root_dir = Path(__file__).parent.parent.resolve()
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.alpha.alignment import TemporalAlignmentEngine
from backend.alpha.instruments import get_instrument_master
from backend.alpha.market_data import KiteConnectBinaryDecoder
from backend.alpha.replay import DeterministicReplayEngine
from backend.alpha.schemas import DepthLevel, MarketEvent, MarketMessageType, NewsEvent
from backend.alpha.storage import PartitionedMarketDataReader, PartitionedMarketDataWriter


def percentile(data: List[float], p: float) -> float:
    """Compute p-th percentile from sorted list."""
    if not data:
        return 0.0
    data_sorted = sorted(data)
    idx = int(len(data_sorted) * (p / 100.0))
    idx = min(idx, len(data_sorted) - 1)
    return round(data_sorted[idx], 4)


def run_comprehensive_benchmark() -> Dict[str, Any]:
    print("=" * 80)
    print("    RUNNING TICKERWATCH ALPHA PHASE 2 BENCHMARK HARNESS    ")
    print("=" * 80)

    # 1. Binary Decode Latency Benchmark
    # Kite Full Mode packet = 184 bytes + 4 bytes header = 188 bytes
    print("\n[Benchmark 1/5] Binary Market Packet Decoding (10,000 packets)...")
    im = get_instrument_master()
    token = 256265
    raw_packet = (
        struct.pack(">H", 1)  # 1 packet
        + struct.pack(">H", 184)  # packet len
        + struct.pack(">IIIIIIIIIII", token, 245050, 25, 245000, 1000000, 500, 450, 244000, 246000, 243500, 244500)
        + struct.pack(">I", 1794280000)  # LTT
        + b"\x00" * 16  # unused padding
        + (struct.pack(">IIHH", 50, 245050, 5, 0) * 10)  # 10 depth levels (5 bids + 5 asks)
    )

    decoder = KiteConnectBinaryDecoder({str(token): "NSE:NIFTY_FUT:CURRENT"})

    decode_latencies_us: List[float] = []
    for _ in range(10000):
        t0 = time.perf_counter_ns()
        evs = decoder.decode_packet(raw_packet)
        t1 = time.perf_counter_ns()
        decode_latencies_us.append((t1 - t0) / 1000.0)

    decode_p50 = percentile(decode_latencies_us, 50)
    decode_p95 = percentile(decode_latencies_us, 95)
    decode_p99 = percentile(decode_latencies_us, 99)
    decode_throughput = round(10000 / (sum(decode_latencies_us) / 1_000_000.0), 1)

    print(f"  Decode Throughput:  {decode_throughput:,} packets/sec")
    print(f"  Decode Latency:     p50={decode_p50} us | p95={decode_p95} us | p99={decode_p99} us")

    # 2. Canonical Normalization & Model Validation
    print("\n[Benchmark 2/5] Canonical Normalization (10,000 events)...")
    now_utc = datetime.now(timezone.utc)
    dummy_depth = [DepthLevel(price=Decimal("24500.00"), quantity=100, orders_count=2)]

    norm_latencies_us: List[float] = []
    events_pool: List[MarketEvent] = []
    for i in range(10000):
        t0 = time.perf_counter_ns()
        ev = MarketEvent(
            provider="BENCHMARK",
            venue="NSE",
            segment="FUTIDX",
            instrument_id="NSE:NIFTY_FUT:CURRENT",
            message_type=MarketMessageType.BOOK_SNAPSHOT,
            local_received_at_utc=now_utc,
            local_monotonic_ns=t0,
            bid_price=Decimal("24500.00"),
            ask_price=Decimal("24500.50"),
            depth_bids=dummy_depth,
            depth_asks=dummy_depth,
            last_traded_price=Decimal("24500.25"),
        )
        t1 = time.perf_counter_ns()
        norm_latencies_us.append((t1 - t0) / 1000.0)
        events_pool.append(ev)

    norm_p50 = percentile(norm_latencies_us, 50)
    norm_p95 = percentile(norm_latencies_us, 95)
    norm_p99 = percentile(norm_latencies_us, 99)
    norm_throughput = round(10000 / (sum(norm_latencies_us) / 1_000_000.0), 1)

    print(f"  Normalize Throughput: {norm_throughput:,} events/sec")
    print(f"  Normalize Latency:    p50={norm_p50} us | p95={norm_p95} us | p99={norm_p99} us")

    # 3. Partitioned Chunked Storage Writer (10,000 events)
    print("\n[Benchmark 3/5] Partitioned Storage Buffer & Gzip Flush (10,000 events)...")
    tmp_bench_dir = Path("data/bench_temp")
    tmp_bench_dir.mkdir(parents=True, exist_ok=True)

    writer = PartitionedMarketDataWriter(base_dir=str(tmp_bench_dir), buffer_size=2000)
    t0_persist = time.perf_counter_ns()
    for ev in events_pool:
        writer.append(ev)
    flushed = writer.flush_all()
    t1_persist = time.perf_counter_ns()

    persist_duration_sec = (t1_persist - t0_persist) / 1_000_000_000.0
    persist_throughput = round(10000 / persist_duration_sec, 1)
    total_bytes = sum(f.stat().st_size for f in flushed)

    print(f"  Persist Duration:   {round(persist_duration_sec, 3)} seconds")
    print(f"  Persist Throughput: {persist_throughput:,} events/sec")
    print(f"  Disk Bytes Written: {total_bytes:,} bytes ({round(total_bytes/1024, 1)} KB)")

    # 4. As-Of Join Temporal Alignment Latency
    print("\n[Benchmark 4/5] As-Of Join Temporal Alignment (1,000 alignment lookups)...")
    align_engine = TemporalAlignmentEngine()
    # Feed 500 pre-event market ticks into engine
    t_start = datetime(2026, 11, 10, 5, 0, 0, tzinfo=timezone.utc)
    for i in range(500):
        t_tick = t_start + timedelta(milliseconds=i * 50)
        align_engine.add_market_event(
            MarketEvent(
                provider="BENCHMARK",
                venue="NSE",
                segment="FUTIDX",
                instrument_id="NSE:NIFTY_FUT:CURRENT",
                local_received_at_utc=t_tick,
                local_monotonic_ns=i * 50_000_000,
                bid_price=Decimal("24500.00"),
                ask_price=Decimal("24500.50"),
                last_traded_price=Decimal("24500.25"),
            )
        )

    t_eval = t_start + timedelta(seconds=20.0)
    news_proto = NewsEvent(
        provider_id="bench_news",
        source_name="Benchmark News",
        source_url="http://benchmark",
        headline="Benchmark News Item",
        local_received_at_utc=t_eval,
        local_monotonic_ns=20_000_000_000,
        content_hash="abc" * 15,
        instrument_mappings=im.resolve_event_mapping("NIFTY", "MACRO"),
    )

    align_latencies_us: List[float] = []
    for _ in range(1000):
        t0 = time.perf_counter_ns()
        rec = align_engine.align_event(news_proto)
        t1 = time.perf_counter_ns()
        align_latencies_us.append((t1 - t0) / 1000.0)

    align_p50 = percentile(align_latencies_us, 50)
    align_p95 = percentile(align_latencies_us, 95)
    align_p99 = percentile(align_latencies_us, 99)
    align_throughput = round(1000 / (sum(align_latencies_us) / 1_000_000.0), 1)

    print(f"  Alignment Throughput: {align_throughput:,} as-of joins/sec")
    print(f"  Alignment Latency:    p50={align_p50} us | p95={align_p95} us | p99={align_p99} us")

    # 5. Deterministic Replay Throughput (25,000 mixed events)
    print("\n[Benchmark 5/5] Deterministic Replay Engine Throughput (25,000 mixed events)...")
    market_fixture = [
        MarketEvent(
            provider="BENCH",
            venue="NSE",
            segment="FUTIDX",
            instrument_id="NSE:NIFTY_FUT:CURRENT",
            local_received_at_utc=t_start + timedelta(milliseconds=i * 20),
            local_monotonic_ns=i * 20_000_000,
            bid_price=Decimal("24500.00"),
            ask_price=Decimal("24500.50"),
            last_traded_price=Decimal("24500.25"),
        )
        for i in range(24900)
    ]
    news_fixture = [
        NewsEvent(
            provider_id="bench_news",
            source_name="Bench News",
            source_url=f"http://bench/{i}",
            headline=f"Macro announcement {i}",
            local_received_at_utc=t_start + timedelta(seconds=i * 5),
            local_monotonic_ns=i * 5_000_000_000,
            content_hash=f"hash_bench_{i:04d}",
            instrument_mappings=im.resolve_event_mapping("NIFTY", "MACRO"),
        )
        for i in range(100)
    ]

    replay_engine = DeterministicReplayEngine()
    recs, metrics = replay_engine.replay_streams(news_fixture, market_fixture)

    print(f"  Replay Total Events: {metrics.total_events_processed:,}")
    print(f"  Replay Duration:     {metrics.duration_seconds} sec")
    print(f"  Replay Throughput:   {metrics.throughput_eps:,} events/sec")
    print(f"  Replay Checksum:     {metrics.deterministic_sha256[:16]}...")

    # Cleanup temp benchmark files
    try:
        import shutil
        shutil.rmtree(tmp_bench_dir)
    except Exception:
        pass

    results = {
        "decode_throughput_eps": decode_throughput,
        "decode_latency_us": {"p50": decode_p50, "p95": decode_p95, "p99": decode_p99},
        "normalize_throughput_eps": norm_throughput,
        "normalize_latency_us": {"p50": norm_p50, "p95": norm_p95, "p99": norm_p99},
        "persist_throughput_eps": persist_throughput,
        "alignment_throughput_eps": align_throughput,
        "alignment_latency_us": {"p50": align_p50, "p95": align_p95, "p99": align_p99},
        "replay_throughput_eps": metrics.throughput_eps,
        "replay_duration_sec": metrics.duration_seconds,
        "deterministic_checksum": metrics.deterministic_sha256,
    }
    return results


if __name__ == "__main__":
    res = run_comprehensive_benchmark()
    output_file = Path("docs/alpha/benchmark_results.json")
    output_file.write_text(json.dumps(res, indent=2), encoding="utf-8")
    print("\nBenchmark results saved to docs/alpha/benchmark_results.json")
