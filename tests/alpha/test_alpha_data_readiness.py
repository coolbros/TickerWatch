"""Unit tests for Phase 5 Work Package B Real-Data Readiness Auditor."""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import pytest

from backend.alpha.data_readiness import RealDataReadinessAuditor, ReadinessStatus
from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.historical_store import PersistentHistoricalEventStore, StoredOutcomeRecord
from backend.alpha.nlp_pipeline import get_nlp_pipeline


def test_real_data_readiness_segregates_synthetic_and_flags_blocked(tmp_path):
    """Verify synthetic records are never counted as real empirical data and gaps flag BLOCKED."""
    legacy_db = tmp_path / "legacy.db"
    hist_db = tmp_path / "hist.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    # Create a synthetic benchmark file in data/ that must NOT count as real market observations
    (data_dir / "alpha_phase2_benchmark_test.jsonl").write_text('{"synthetic": 1}\n{"synthetic": 2}\n', encoding="utf-8")

    conn = sqlite3.connect(str(legacy_db))
    conn.execute(
        """
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY,
            connector_id TEXT,
            title TEXT,
            snippet TEXT,
            published_at_utc TEXT,
            published_at_original TEXT,
            observed_at_utc TEXT,
            content_hash TEXT,
            tickers TEXT,
            raw_metadata TEXT
        )
        """
    )
    # Insert 1 synthetic row and 2 real rows sharing same batch backfill timestamp
    conn.execute(
        "INSERT INTO articles VALUES (1, 'test_api_conn', 'Synthetic AAPL Test', 'Test body', '2026-11-06T10:00:00Z', '', '2026-11-06T10:00:00Z', 'h1', '[\"AAPL\"]', '{}')"
    )
    conn.execute(
        "INSERT INTO articles VALUES (2, 'sec_edgar', 'Apple Inc. (AAPL) filed Form 8-K', 'Item 2.02', '2024-01-15T21:00:00Z', '2024-01-15', '2026-11-08T09:34:42Z', 'h2', '[\"AAPL\"]', '{\"form\":\"8-K\"}')"
    )
    conn.execute(
        "INSERT INTO articles VALUES (3, 'sec_edgar', 'Apple Inc. (AAPL) filed Form 8-K/A', 'Amended filing', '2026-01-15T21:00:00Z', '2026-01-15', '2026-11-08T09:34:43Z', 'h3', '[\"AAPL\"]', '{\"form\":\"8-K/A\"}')"
    )
    conn.commit()
    conn.close()

    # Put a synthetic event in PersistentHistoricalEventStore -> must NOT leak into real counts!
    store = PersistentHistoricalEventStore(db_path=hist_db)
    nlp = get_nlp_pipeline()
    t0 = datetime(2026, 2, 1, 5, 0, tzinfo=timezone.utc)
    rep = nlp.process_raw_text("TCS wins order worth Rs 2,000 crore", decision_timestamp=t0, event_id="syn_1")
    store.upsert_event(rep, jurisdiction=MarketJurisdiction.IN, is_synthetic=True)
    store.save_outcome(
        StoredOutcomeRecord(
            event_id="syn_1",
            instrument_id="NSE:TCS:EQ",
            horizon_label="1d",
            outcome_start_ts=t0,
            outcome_end_ts=datetime(2026, 2, 2, 5, 0, tzinfo=timezone.utc),
            return_simple=0.02,
        )
    )

    auditor = RealDataReadinessAuditor(legacy_db_path=legacy_db, historical_store_path=hist_db, data_dir=data_dir)
    inv = auditor.audit()

    assert inv.synthetic_events_count == 1
    assert inv.real_events_count == 2
    assert inv.unique_real_issuers == ["AAPL"]
    assert inv.revision_or_amendment_count_real == 1
    assert inv.persistent_store_events_synthetic == 1
    assert inv.persistent_store_events_real == 0
    assert inv.valid_real_market_observations == 0
    assert inv.synthetic_market_benchmark_files == 1
    assert inv.matured_real_labels_by_instrument_horizon == {}
    assert inv.readiness_status_in == ReadinessStatus.BLOCKED
    assert inv.overall_empirical_training_status == ReadinessStatus.BLOCKED
    assert len(inv.blocking_gaps) >= 3
