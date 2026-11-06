"""Resource consumption and performance benchmark script for TickerWatch Phase 1."""

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import psutil

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.connectors.models import SourceItem
from backend.database.connection import apply_migrations
from backend.database.repository import Repository


def get_current_process_memory_mb() -> float:
    """Read physical RSS (Working Set) memory in MB using psutil."""
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / (1024 * 1024)


def make_batch(count: int, prefix: str) -> list[SourceItem]:
    items = []
    now = datetime.now(timezone.utc)
    for i in range(count):
        items.append(
            SourceItem(
                connector_id="bench_conn",
                source_item_id=f"{prefix}_item_{i}",
                publisher="Benchmark Publisher",
                source_type="benchmark",
                title=f"Benchmark Article Headline {i} - Major Market Event",
                url=f"https://benchmark.example.com/news/{prefix}/{i}",
                published_at_utc=now,
                published_at_original=now.isoformat(),
                observed_at_utc=now,
                content_text="Detailed financial analysis snippet text for benchmarking SQLite insertion and memory throughput.",
                content_hash=f"hash_{prefix}_{i}",
                raw_metadata={"index": i, "batch": prefix},
                tickers=["AAPL", "GOOGL", "MSFT", "NVDA"],
            )
        )
    return items


def benchmark_persistence():
    print("=" * 65)
    print("TICKERWATCH PERFORMANCE & RESOURCE AUDIT")
    print("=" * 65)

    db_path = "benchmark_test.db"
    for p in (db_path, db_path + "-wal", db_path + "-shm"):
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    mem_baseline = get_current_process_memory_mb()
    print(f"Baseline Idle RSS Memory:           {mem_baseline:.2f} MB")

    apply_migrations(db_path)
    repo = Repository(db_path)

    # 1. Measure insertion of small batches (typical poll: 20 items)
    batch_small = make_batch(20, "small")
    t0 = time.monotonic()
    ins, skip = repo.save_batch_atomic("bench_conn", batch_small, "cur_small")
    t_small_ms = (time.monotonic() - t0) * 1000.0
    mem_after_small = get_current_process_memory_mb()

    print(f"Small Batch (20 items) Duration:    {t_small_ms:.2f} ms ({ins} inserted)")
    print(f"Memory After Small Batch:           {mem_after_small:.2f} MB")

    # 2. Measure insertion of large batch (burst: 1,000 items)
    batch_large = make_batch(1000, "large")
    t0 = time.monotonic()
    ins_large, skip_large = repo.save_batch_atomic("bench_conn", batch_large, "cur_large")
    t_large_ms = (time.monotonic() - t0) * 1000.0
    mem_peak = get_current_process_memory_mb()

    print(f"Large Burst (1,000 items) Duration: {t_large_ms:.2f} ms ({ins_large} inserted)")
    print(f"Peak Memory During 1,000 Items:     {mem_peak:.2f} MB (Delta: +{mem_peak - mem_baseline:.2f} MB)")

    # 3. Measure repeated polling (100 sequential cycles of duplicate/empty batches)
    t0 = time.monotonic()
    for cycle in range(100):
        # Simulate duplicate poll: suppresses rows, updates cursor
        repo.save_batch_atomic("bench_conn", batch_small, f"cur_cycle_{cycle}")
    t_cycles_ms = (time.monotonic() - t0) * 1000.0
    mem_after_100_cycles = get_current_process_memory_mb()

    print(f"100 Repeated Polling Cycles:        {t_cycles_ms:.2f} ms (Avg: {t_cycles_ms/100:.2f} ms/poll)")
    print(f"Memory After 100 Cycles:            {mem_after_100_cycles:.2f} MB (Delta: +{mem_after_100_cycles - mem_baseline:.2f} MB)")

    # 4. Measure Database File Size
    db_size_kb = os.path.getsize(db_path) / 1024 if os.path.exists(db_path) else 0
    wal_size_kb = os.path.getsize(db_path + "-wal") / 1024 if os.path.exists(db_path + "-wal") else 0
    print(f"Database File Size:                 {db_size_kb:.2f} KB (WAL: {wal_size_kb:.2f} KB)")
    print("=" * 65)

    # Clean up
    for p in (db_path, db_path + "-wal", db_path + "-shm"):
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass


if __name__ == "__main__":
    benchmark_persistence()
