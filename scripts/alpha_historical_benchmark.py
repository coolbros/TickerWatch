"""CLI Benchmark and Verification Runner for Phase 4 Historical Retrieval & Event Study."""

import json
from pathlib import Path
import sys

# Bootstrap repository root into sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.alpha.historical_evaluation import run_historical_benchmark


def main() -> int:
    print("=" * 80)
    print(" TICKERWATCH ALPHA — PHASE 4 HISTORICAL RETRIEVAL & EVENT-STUDY BENCHMARK")
    print("=" * 80)

    report = run_historical_benchmark(top_k=5)

    print(f"Platform:                    {report.platform_info} | Python {report.python_version}")
    print(f"ONNX BGE-small-en-v1.5 INT8: {'AVAILABLE (OFFLINE)' if report.onnx_bge_small_available else 'UNAVAILABLE (FALLBACK ONLY)'}")
    print(f"Corpus Sizes:                IN={report.corpus_counts['in_historical_events']} hist / {report.corpus_counts['in_queries']} queries | "
          f"US={report.corpus_counts['us_historical_events']} hist / {report.corpus_counts['us_queries']} queries")
    print(f"Train/Query Leakage Dups:    {report.split_leakage_duplicates}")
    print(f"Process RAM RSS:             {report.ram_rss_mb_before:.2f} MB -> {report.ram_rss_mb_after:.2f} MB")
    print(f"Embedding Latency (ms/ev):   {report.embedding_latency_mean_ms}")
    print(f"Indexing Throughput (ev/s):  {report.indexing_throughput_events_per_sec}")
    print(f"SQLite Store Size (bytes):   {report.sqlite_storage_bytes}")
    print("-" * 80)
    print("COMBINED ABLATION SUMMARY (k=5, 15 queries across IN & US, 5 event strata)")
    print("-" * 80)
    header = f"{'Configuration':<28} {'P@5':>7} {'R@5':>7} {'MRR':>7} {'nDCG@5':>8} {'p95(ms)':>9}"
    print(header)
    for label, stats in report.combined_ablation_summary.items():
        print(
            f"{label:<28} {stats['precision@5']:>7.4f} {stats['recall@5']:>7.4f} "
            f"{stats['mrr']:>7.4f} {stats['ndcg@5']:>8.4f} {stats['p95_latency_ms']:>9.3f}"
        )

    print("-" * 80)
    print("INDIAN MARKET (IN) STRATUM MRR BREAKDOWN")
    for res in report.in_market_comparison:
        print(f"  {res.mode_label:<26} MRR={res.mrr:.4f} | strata={res.by_stratum_mrr}")

    print("-" * 80)
    print("US MARKET (US) STRATUM MRR BREAKDOWN")
    for res in report.us_market_comparison:
        print(f"  {res.mode_label:<26} MRR={res.mrr:.4f} | strata={res.by_stratum_mrr}")

    print("-" * 80)
    print("EVENT-STUDY VERIFICATION SUITE:")
    for chk_name, passed in report.event_study_checks.items():
        print(f"  [{('PASS' if passed else 'FAIL')}] {chk_name}")
    print("=" * 80)

    out_path = Path("docs/alpha/benchmark_historical_results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report.model_dump(mode="json"), f, indent=2)
    print(f"Results JSON saved to: {out_path.resolve()}")

    if report.split_leakage_duplicates > 0 or not report.all_event_study_checks_passed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
