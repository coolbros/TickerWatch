"""CLI Interface for TickerWatch Alpha Data Capture, Replay, and Alignment."""

import argparse
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
from typing import List

from backend.alpha.alignment import TemporalAlignmentEngine
from backend.alpha.dataset import DatasetExporter
from backend.alpha.instruments import get_instrument_master
from backend.alpha.quality import DataQualityEngine
from backend.alpha.replay import DeterministicReplayEngine
from backend.alpha.schemas import DepthLevel, MarketEvent, MarketMessageType, NewsEvent
from backend.alpha.storage import PartitionedMarketDataReader, run_storage_benchmark
from backend.alpha.timestamps import audit_host_clock


def generate_sample_fixtures() -> tuple[List[NewsEvent], List[MarketEvent]]:
    """Generate realistic fixture data for testing and offline demonstration."""
    im = get_instrument_master()
    # 5 minutes in the past
    t_base = datetime.now(timezone.utc) - timedelta(minutes=5)

    market_events: List[MarketEvent] = []
    # 20 seconds of market data at 100ms intervals (200 quotes)
    for i in range(200):
        t_tick = t_base + timedelta(milliseconds=i * 100)
        p_offset = Decimal(str(round((i % 10) * 0.05, 2)))
        bid = Decimal("24500.00") + p_offset
        ask = bid + Decimal("0.50")
        market_events.append(
            MarketEvent(
                provider="FIXTURE_REPLAY",
                venue="NSE",
                segment="FUTIDX",
                instrument_id="NSE:NIFTY_FUT:CURRENT",
                message_type=MarketMessageType.BOOK_SNAPSHOT,
                exchange_timestamp_utc=t_tick,
                local_received_at_utc=t_tick,
                local_monotonic_ns=i * 100_000_000,
                sequence_number=1000 + i,
                bid_price=bid,
                bid_quantity=100 + i,
                ask_price=ask,
                ask_quantity=150 + i,
                depth_bids=[DepthLevel(price=bid, quantity=100 + i)],
                depth_asks=[DepthLevel(price=ask, quantity=150 + i)],
                last_traded_price=bid,
                last_traded_quantity=25,
                cumulative_volume=500000 + i * 25,
            )
        )

    # News event arrives at t_base + 5.0 seconds
    t_news = t_base + timedelta(seconds=5.0)
    news_events: List[NewsEvent] = [
        NewsEvent(
            provider_id="nse_corporate",
            source_name="NSE Corporate Announcements",
            source_url="https://nseindia.com/announcements/ril_001",
            headline="Reliance Industries announces multi-billion dollar green energy capex",
            published_at_utc=t_news - timedelta(seconds=2.0),
            provider_observed_at_utc=t_news - timedelta(seconds=1.5),
            local_received_at_utc=t_news,
            local_monotonic_ns=5_000_000_000,
            content_hash="d3b07384d113edec49eaa6238ad5ff00112233445566778899aabbccddeeff00",
            entity_ids=["RELIANCE"],
            instrument_mappings=im.resolve_event_mapping("RELIANCE", "CONTRACTS"),
        ),
        NewsEvent(
            provider_id="nse_corporate",
            source_name="NSE Corporate Announcements",
            source_url="https://nseindia.com/announcements/rbi_002",
            headline="Reserve Bank of India Monetary Policy Committee maintains repo rate stance",
            published_at_utc=t_news + timedelta(seconds=2.0),
            provider_observed_at_utc=t_news + timedelta(seconds=2.5),
            local_received_at_utc=t_news + timedelta(seconds=3.0),
            local_monotonic_ns=8_000_000_000,
            content_hash="a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0",
            entity_ids=["RBI"],
            instrument_mappings=im.resolve_event_mapping("RBI", "MACRO"),
        ),
    ]

    return news_events, market_events


def cmd_inspect(args: argparse.Namespace) -> None:
    """Inspect instrument master, clock status, and available datasets."""
    im = get_instrument_master()
    instruments = im.list_all()

    print("================================================================================")
    print("                    TICKERWATCH ALPHA -- SYSTEM STATUS INSPECT                  ")
    print("================================================================================")
    print(f"\n[1] Registered Instruments ({len(instruments)} active in Instrument Master):")
    for inst in instruments:
        print(f"  - {inst.instrument_id:<28} | {inst.segment:<6} | Lot: {inst.lot_size:<4} | Tick: {inst.tick_size} | {inst.source_metadata}")

    print("\n[2] Host Clock Synchronization Audit:")
    audit = audit_host_clock()
    print(f"  - OS: {audit.host_os}")
    print(f"  - Sync Method: {audit.clock_sync_method}")
    print(f"  - Synchronized: {audit.is_synchronized}")
    print(f"  - Uncertainty Bound: +/- {audit.uncertainty_bound_ms} ms")
    print(f"  - Notes: {audit.notes}")

    print("\n[3] Storage Directory Layout:")
    base_data = Path("data")
    if base_data.exists():
        for p in base_data.rglob("*"):
            if p.is_dir():
                print(f"  - {p.as_posix()}/")
    else:
        print("  - data/ directory not yet initialized.")
    print("================================================================================")


def cmd_replay(args: argparse.Namespace) -> None:
    """Run deterministic historical replay over sample or recorded data."""
    print("Initializing Deterministic Replay Engine...")
    news_stream, market_stream = generate_sample_fixtures()
    replay_engine = DeterministicReplayEngine()

    records, metrics = replay_engine.replay_streams(news_stream, market_stream)

    print("\n--- REPLAY COMPLETE ---")
    print(f"Total Events Processed:   {metrics.total_events_processed}")
    print(f"Market Ticks Processed:   {metrics.market_events_processed}")
    print(f"News Disclosures Aligned: {metrics.news_events_processed}")
    print(f"Alignments Produced:      {metrics.alignments_generated}")
    print(f"Replay Duration:          {metrics.duration_seconds} seconds")
    print(f"Replay Throughput:        {metrics.throughput_eps} events/sec")
    print(f"Deterministic Checksum:   {metrics.deterministic_sha256}")

    for r in records:
        print(f"\n[Aligned Event: {r.news_event_id}]")
        print(f"  Headline: {r.headline}")
        print(f"  Decision Time: {r.decision_timestamp_utc.isoformat()}")
        print(f"  Quality Flag:  {r.quality_flag}")
        for inst_id, pre in r.pre_event_context.items():
            print(f"    Target Instrument: {inst_id} ({pre.relevance.value})")
            print(f"      Pre-Event Mid: {pre.mid_price} | Spread: {pre.spread} | Freshness: {pre.feed_freshness_ms}ms")
            horizons = r.forward_outcomes.get(inst_id, {})
            h_str = " | ".join(f"{k}: {v.forward_return_bps} bps" for k, v in horizons.items() if v.forward_return_bps is not None)
            if h_str:
                print(f"      Forward Returns: {h_str}")


def cmd_quality(args: argparse.Namespace) -> None:
    """Run data quality audit on input streams."""
    print("Executing Data Quality Audit...")
    news_stream, market_stream = generate_sample_fixtures()
    qe = DataQualityEngine()

    report = qe.audit_batch(news_stream, market_stream)
    print("\n--- DATA QUALITY REPORT ---")
    print(f"Total Records Audited: {report.total_records_audited}")
    print(f"Passed:                {report.passed_count}")
    print(f"Warnings:              {report.warning_count}")
    print(f"Quarantined:           {report.quarantined_count}")
    print(f"Overall Clean:         {report.is_clean}")

    if report.rule_violations:
        print("\nRule Violations:")
        for v in report.rule_violations:
            print(f"  [{v.severity}] {v.rule_id} on {v.entity_ref}: {v.message}")


def cmd_export(args: argparse.Namespace) -> None:
    """Run replay, alignment, and export reproducible research dataset."""
    fmt = args.format or "jsonl"
    print(f"Generating and exporting research dataset in '{fmt}' format...")
    news_stream, market_stream = generate_sample_fixtures()
    replay_engine = DeterministicReplayEngine()

    records, _ = replay_engine.replay_streams(news_stream, market_stream)

    exporter = DatasetExporter(output_dir=args.outdir or "data/research_datasets")
    data_path, manifest_path = exporter.export_dataset(
        records=records,
        dataset_name="alpha_phase2_benchmark",
        format_type=fmt,
    )

    print(f"\n[SUCCESS] Dataset exported:")
    print(f"  Data File:     {data_path.as_posix()}")
    print(f"  Manifest File: {manifest_path.as_posix()}")
    print(f"  Manifest JSON Preview:\n{manifest_path.read_text(encoding='utf-8')}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="alpha_pipeline",
        description="TickerWatch Alpha — Data Capture, Temporal Alignment & Historical Replay CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # inspect
    subparsers.add_parser("inspect", help="Inspect instrument master, clock status, and storage")

    # replay
    subparsers.add_parser("replay", help="Execute deterministic historical replay")

    # quality
    subparsers.add_parser("quality", help="Run data quality validation audit")

    # export
    export_parser = subparsers.add_parser("export", help="Export research dataset with manifest")
    export_parser.add_argument("--format", choices=["jsonl", "csv"], default="jsonl", help="Dataset format")
    export_parser.add_argument("--outdir", default="data/research_datasets", help="Output directory")

    args = parser.parse_args()

    if args.command == "inspect":
        cmd_inspect(args)
    elif args.command == "replay":
        cmd_replay(args)
    elif args.command == "quality":
        cmd_quality(args)
    elif args.command == "export":
        cmd_export(args)


if __name__ == "__main__":
    main()
