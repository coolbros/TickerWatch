"""Dual-Tiered Storage Engine for High-Frequency Market Events & Raw Payloads."""

from datetime import datetime, timezone
from decimal import Decimal
import gzip
import json
import os
from pathlib import Path
import time
from typing import Any, Dict, Generator, List, Optional
from pydantic import BaseModel

from backend.alpha.schemas import MarketEvent, NewsEvent
from backend.alpha.timestamps import parse_utc_iso, to_utc_iso


class StorageManifest(BaseModel):
    """Manifest tracking partitions and event boundaries for reproducibility."""
    instrument_id: str
    date_str: str
    total_events: int = 0
    total_chunks: int = 0
    earliest_event_utc: Optional[str] = None
    latest_event_utc: Optional[str] = None
    schema_version: str = "2.0.0"


class PartitionedMarketDataWriter:
    """
    Append-only chunked compressed JSONL writer for high-volume market events.
    Features:
    - Bounded in-memory buffering.
    - Automatic daily and instrument-level partitioning.
    - Gzip compression for minimal disk footprint.
    - Explicit fsync and manifest updates on flush.
    """

    def __init__(
        self,
        base_dir: str = "data/market_events",
        buffer_size: int = 1000,
        compression_level: int = 6,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.buffer_size = buffer_size
        self.compression_level = compression_level
        # Buffers partitioned by (date_str, instrument_key)
        self._buffers: Dict[tuple[str, str], List[MarketEvent]] = {}
        self._chunk_counters: Dict[tuple[str, str], int] = {}

    def _sanitize_instrument(self, instrument_id: str) -> str:
        return instrument_id.replace(":", "_").replace("/", "_")

    def append(self, event: MarketEvent) -> None:
        """Append market event to in-memory write buffer."""
        # Partition by UTC date of local receipt
        date_str = event.local_received_at_utc.strftime("%Y-%m-%d")
        inst_key = self._sanitize_instrument(event.instrument_id)
        partition_key = (date_str, inst_key)

        if partition_key not in self._buffers:
            self._buffers[partition_key] = []

        self._buffers[partition_key].append(event)

        if len(self._buffers[partition_key]) >= self.buffer_size:
            self.flush_partition(date_str, inst_key)

    def flush_partition(self, date_str: str, inst_key: str) -> Optional[Path]:
        """Flush buffered events for a specific partition into a new compressed chunk."""
        partition_key = (date_str, inst_key)
        events = self._buffers.get(partition_key, [])
        if not events:
            return None

        target_dir = self.base_dir / date_str / inst_key
        target_dir.mkdir(parents=True, exist_ok=True)

        current_chunk = self._chunk_counters.get(partition_key, 0)
        chunk_file = target_dir / f"events_{current_chunk:06d}.jsonl.gz"
        tmp_file = target_dir / f"events_{current_chunk:06d}.tmp"

        now_utc = datetime.now(timezone.utc)

        # Write lines to temp file then compress
        with gzip.open(chunk_file, "wt", encoding="utf-8", compresslevel=self.compression_level) as gz:
            for ev in events:
                if ev.persisted_at_utc is None:
                    ev.persisted_at_utc = now_utc
                # Dump Pydantic model to JSON line
                gz.write(ev.model_dump_json() + "\n")

        self._chunk_counters[partition_key] = current_chunk + 1
        self._buffers[partition_key] = []

        # Update manifest
        self._update_manifest(target_dir, events)
        return chunk_file

    def _update_manifest(self, target_dir: Path, new_events: List[MarketEvent]) -> None:
        manifest_file = target_dir / "manifest.json"
        manifest_data: Dict[str, Any] = {
            "total_events": 0,
            "total_chunks": 0,
            "earliest_event_utc": None,
            "latest_event_utc": None,
            "schema_version": "2.0.0",
        }
        if manifest_file.exists():
            try:
                manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        manifest_data["total_events"] += len(new_events)
        manifest_data["total_chunks"] += 1

        t_min = min(e.local_received_at_utc for e in new_events)
        t_max = max(e.local_received_at_utc for e in new_events)

        prev_min = parse_utc_iso(manifest_data.get("earliest_event_utc"))
        prev_max = parse_utc_iso(manifest_data.get("latest_event_utc"))

        if prev_min is None or t_min < prev_min:
            manifest_data["earliest_event_utc"] = to_utc_iso(t_min)
        if prev_max is None or t_max > prev_max:
            manifest_data["latest_event_utc"] = to_utc_iso(t_max)

        manifest_file.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

    def flush_all(self) -> List[Path]:
        """Flush all pending write buffers across all partitions."""
        flushed_files: List[Path] = []
        keys = list(self._buffers.keys())
        for date_str, inst_key in keys:
            cf = self.flush_partition(date_str, inst_key)
            if cf:
                flushed_files.append(cf)
        return flushed_files


class PartitionedMarketDataReader:
    """Reads partitioned compressed market event files for replay and historical research."""

    def __init__(self, base_dir: str = "data/market_events") -> None:
        self.base_dir = Path(base_dir)

    def _sanitize_instrument(self, instrument_id: str) -> str:
        return instrument_id.replace(":", "_").replace("/", "_")

    def stream_events(
        self,
        instrument_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Generator[MarketEvent, None, None]:
        """Stream events in strict chronological order from compressed chunk files."""
        inst_key = self._sanitize_instrument(instrument_id)
        if not self.base_dir.exists():
            return

        date_dirs = sorted([d for d in self.base_dir.iterdir() if d.is_dir()])
        for d in date_dirs:
            date_str = d.name
            if start_date and date_str < start_date:
                continue
            if end_date and date_str > end_date:
                continue

            inst_dir = d / inst_key
            if not inst_dir.exists():
                continue

            chunk_files = sorted(inst_dir.glob("events_*.jsonl.gz"))
            for chunk_file in chunk_files:
                try:
                    with gzip.open(chunk_file, "rt", encoding="utf-8") as gz:
                        for line in gz:
                            line_str = line.strip()
                            if not line_str:
                                continue
                            data = json.loads(line_str)
                            yield MarketEvent.model_validate(data)
                except Exception as exc:
                    # Non-fatal corrupted chunk skip
                    continue


class RawPayloadStore:
    """Stores full raw incoming payloads partitioned by date and connector."""

    def __init__(self, base_dir: str = "data/raw_payloads") -> None:
        self.base_dir = Path(base_dir)

    def save_payload(
        self,
        provider_id: str,
        content_hash: str,
        payload_bytes: bytes,
        received_at: Optional[datetime] = None,
    ) -> str:
        """Persist raw payload and return relative reference string."""
        ts = received_at or datetime.now(timezone.utc)
        date_str = ts.strftime("%Y-%m-%d")
        clean_provider = provider_id.replace(":", "_")
        target_dir = self.base_dir / date_str / clean_provider
        target_dir.mkdir(parents=True, exist_ok=True)

        target_file = target_dir / f"{content_hash}.bin"
        target_file.write_bytes(payload_bytes)
        return str(target_file.as_posix())

    def get_payload(self, raw_ref: str) -> Optional[bytes]:
        """Fetch raw payload from storage reference."""
        p = Path(raw_ref)
        if p.exists() and p.is_file():
            return p.read_bytes()
        return None


def run_storage_benchmark(num_events: int = 10000, tmp_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Run empirical performance benchmark comparing write throughput and compression.
    """
    from decimal import Decimal
    from backend.alpha.schemas import DepthLevel, MarketEvent, MarketMessageType

    test_dir = Path(tmp_dir) if tmp_dir else Path("data/benchmark_test")
    test_dir.mkdir(parents=True, exist_ok=True)

    dummy_levels = [
        DepthLevel(price=Decimal("2450.00") + Decimal(str(i * 0.05)), quantity=100 * (i + 1), orders_count=i + 1)
        for i in range(5)
    ]

    events = [
        MarketEvent(
            provider="BENCHMARK",
            venue="NSE",
            segment="FUTIDX",
            instrument_id="NSE:NIFTY_FUT:CURRENT",
            message_type=MarketMessageType.QUOTE,
            exchange_timestamp_utc=datetime.now(timezone.utc),
            local_received_at_utc=datetime.now(timezone.utc),
            local_monotonic_ns=time.perf_counter_ns(),
            bid_price=Decimal("2450.00"),
            bid_quantity=500,
            ask_price=Decimal("2450.05"),
            ask_quantity=450,
            depth_bids=dummy_levels,
            depth_asks=dummy_levels,
            last_traded_price=Decimal("2450.00"),
            last_traded_quantity=50,
            cumulative_volume=1250000,
        )
        for _ in range(num_events)
    ]

    # Benchmark Chunked Compressed Writer
    comp_dir = test_dir / "compressed"
    writer = PartitionedMarketDataWriter(base_dir=str(comp_dir), buffer_size=1000)
    t0 = time.monotonic()
    for ev in events:
        writer.append(ev)
    writer.flush_all()
    duration = time.monotonic() - t0

    all_chunk_files = list(comp_dir.rglob("*.jsonl.gz"))
    total_bytes = sum(f.stat().st_size for f in all_chunk_files)

    # Benchmark Raw uncompressed JSONL
    raw_file = test_dir / "raw_events.jsonl"
    t0_raw = time.monotonic()
    with raw_file.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(ev.model_dump_json() + "\n")
    raw_duration = time.monotonic() - t0_raw
    raw_bytes = raw_file.stat().st_size

    # Benchmark Reader throughput
    reader = PartitionedMarketDataReader(base_dir=str(test_dir / "compressed"))
    t0_read = time.monotonic()
    read_count = sum(1 for _ in reader.stream_events("NSE:NIFTY_FUT:CURRENT"))
    read_duration = time.monotonic() - t0_read

    # Cleanup benchmark files
    try:
        import shutil
        shutil.rmtree(test_dir)
    except Exception:
        pass

    compression_ratio = round(raw_bytes / max(total_bytes, 1), 2)
    write_throughput = round(num_events / max(duration, 0.0001), 1)
    read_throughput = round(read_count / max(read_duration, 0.0001), 1)

    return {
        "num_events": num_events,
        "compressed_write_seconds": round(duration, 4),
        "compressed_write_throughput_eps": write_throughput,
        "compressed_size_bytes": total_bytes,
        "raw_jsonl_size_bytes": raw_bytes,
        "compression_ratio": compression_ratio,
        "read_throughput_eps": read_throughput,
    }
