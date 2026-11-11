"""CLI Daily Operational & Shadow Health Report Runner (Phase 6 WP-H)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.alpha.live_data_capture import LiveCaptureStore
from backend.alpha.shadow_ledger import ShadowSignalLedger
from backend.alpha.shadow_monitoring import ShadowOperationalMonitor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TickerWatch Alpha Phase 6 Shadow Operational Report")
    parser.add_argument("--capture-db", default="data/alpha_live_capture.db", help="Path to LiveCaptureStore SQLite DB")
    parser.add_argument("--market-dir", default="data/live_market_events", help="Path to partitioned .jsonl.gz market dir")
    parser.add_argument("--ledger-db", default="data/alpha_shadow_ledger.db", help="Path to ShadowSignalLedger SQLite DB")
    parser.add_argument("--output-json", default=None, help="Optional file path to persist machine-readable JSON summary")
    parser.add_argument("--exclude-synthetic", action="store_true", help="Report strictly on non-synthetic rows")
    args = parser.parse_args(argv)

    cap = LiveCaptureStore(db_path=args.capture_db, market_events_dir=args.market_dir)
    ledger = ShadowSignalLedger(db_path=args.ledger_db)
    mon = ShadowOperationalMonitor(capture_store=cap, shadow_ledger=ledger)
    report = mon.build_report(include_synthetic=not args.exclude_synthetic)

    if args.output_json:
        out_p = Path(args.output_json)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    print("================================================================================")
    print("             TICKERWATCH ALPHA -- PHASE 6 OPERATIONAL HEALTH SUMMARY            ")
    print("================================================================================")
    print(f"Generated At (UTC)    : {report.generated_at_utc.isoformat()}")
    print(f"News Articles/Revs    : {report.news_health.articles_received} / {report.news_health.total_revisions_recorded} (Missing PubTS: {report.news_health.missing_publication_ts_count})")
    print(f"Market Msg / Gaps     : {report.market_health.message_throughput_total} / {report.market_health.sequence_gap_count} (Uptime: {report.market_health.feed_uptime_fraction:.1%})")
    print(f"AI Shadow Evals       : {report.ai_health.events_evaluated} (NO_TRADE rate: {report.ai_health.no_trade_frequency:.1%})")
    print(f"Matured Outcomes      : {report.predictive_monitoring.matured_evaluations} (RMSE: {report.predictive_monitoring.realized_rmse})")
    print(f"Storage DB+WAL (KB)   : {(report.storage_system_health.capture_db_bytes + report.storage_system_health.ledger_db_bytes)/1024:.1f} KB | RAM: {report.storage_system_health.process_ram_mb} MB | Disk Free: {report.storage_system_health.disk_free_gb} GB")
    if report.alerts:
        print(f"Active Alerts         : {', '.join(report.alerts)}")
    print("================================================================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
