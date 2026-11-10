"""CLI Benchmark and Calibration Runner for Phase 3 Financial NLP."""

import json
from pathlib import Path
import sys

# Bootstrap repository root into sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.alpha.nlp_evaluation import run_nlp_benchmark


def main() -> int:
    print("=" * 72)
    print(" TICKERWATCH ALPHA — PHASE 3 FINANCIAL NLP BENCHMARK & CALIBRATION")
    print("=" * 72)
    print("Running evaluation across 52 diverse, hand-curated gold test cases...\n")

    results = run_nlp_benchmark()

    print(f"Total Test Cases Evaluated:       {results.total_test_cases}")
    print(f"Entity Precision:                 {results.entity_precision * 100:.2f}%")
    print(f"Entity Recall:                    {results.entity_recall * 100:.2f}%")
    print(f"Entity F1 Score:                  {results.entity_f1 * 100:.2f}%  (Target: >= 85.0%)")
    print(f"Ticker Collision False-Pos Rate:  {results.ticker_collision_false_positive_rate * 100:.2f}%  (Target: <= 5.0%)")
    print(f"Event Classification Accuracy:    {results.event_classification_accuracy * 100:.2f}%  (Target: >= 85.0%)")
    print(f"Numerical Extraction Accuracy:    {results.numerical_extraction_accuracy * 100:.2f}%  (Target: >= 85.0%)")
    print(f"Materiality Tier Accuracy:        {results.materiality_tier_accuracy * 100:.2f}%")
    print("-" * 72)
    print(f"Execution Latency P50:            {results.latency_p50_us / 1000:.3f} ms")
    print(f"Execution Latency P95:            {results.latency_p95_us / 1000:.3f} ms  (Target: < 5.0 ms)")
    print(f"Execution Latency P99:            {results.latency_p99_us / 1000:.3f} ms")
    print(f"Process Memory RSS:               {results.memory_rss_mb:.2f} MB")
    print("=" * 72)

    # Save benchmark results to JSON
    output_path = Path("docs/alpha/benchmark_nlp_results.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results.model_dump(mode="json"), f, indent=2)
    print(f"Benchmark results serialized to: {output_path.resolve()}\n")

    if results.all_gates_passed:
        print(">>> ALL PHASE 3 QUALITY CONTROL GATES PASSED SUCCESSFULLY <<<")
        return 0
    else:
        print(">>> CRITICAL FAILURE: ONE OR MORE QUALITY GATES FAILED <<<")
        return 1


if __name__ == "__main__":
    sys.exit(main())
