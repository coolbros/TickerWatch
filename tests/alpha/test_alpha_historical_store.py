"""Unit tests for Persistent Historical Event Store Engine."""

from datetime import datetime, timezone, timedelta
import pytest

from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.historical_store import (
    PersistentHistoricalEventStore,
    StoredOutcomeRecord,
)
from backend.alpha.nlp_pipeline import get_nlp_pipeline


def test_store_idempotency_revisions_and_recovery(tmp_path):
    """Verify idempotent upsert, duplicate detection, revision tracking, and crash recovery."""
    db_file = tmp_path / "hist_store.db"
    store1 = PersistentHistoricalEventStore(db_path=db_file)
    pipeline = get_nlp_pipeline()

    t0 = datetime(2026, 2, 10, 9, 30, 0, tzinfo=timezone.utc)
    rep1 = pipeline.process_raw_text(
        "L&T wins order worth Rs 2,500 crore from Middle East",
        decision_timestamp=t0,
        event_id="evt_lt_01",
    )

    # 1. Initial Insert
    eid, status = store1.upsert_event(rep1, jurisdiction=MarketJurisdiction.IN)
    assert eid == "evt_lt_01"
    assert status == "INSERTED"

    # 2. Idempotent re-insert of exact same representation
    eid2, status2 = store1.upsert_event(rep1, jurisdiction=MarketJurisdiction.IN)
    assert status2 == "SKIPPED_DUPLICATE"

    # 3. Duplicate content hash under a different event ID
    rep_dup = pipeline.process_raw_text(
        "L&T wins order worth Rs 2,500 crore from Middle East",
        decision_timestamp=t0 + timedelta(seconds=5),
        event_id="evt_lt_01_duplicate",
    )
    eid3, status3 = store1.upsert_event(rep_dup, jurisdiction=MarketJurisdiction.IN)
    assert eid3 == "evt_lt_01"
    assert status3 == "SKIPPED_DUPLICATE"

    # 4. Revision update (headline/text changed on same canonical event_id)
    rep_rev = pipeline.process_raw_text(
        "L&T wins revised mega order worth Rs 3,200 crore from Middle East",
        decision_timestamp=t0 + timedelta(minutes=15),
        event_id="evt_lt_01",
    )
    eid4, status4 = store1.upsert_event(rep_rev, jurisdiction=MarketJurisdiction.IN)
    assert eid4 == "evt_lt_01"
    assert status4 == "UPDATED_REVISION"

    # Check revisions trail
    revs = store1.get_revisions("evt_lt_01")
    assert len(revs) == 2
    assert revs[0]["revision_number"] == 1
    assert "2,500 crore" in revs[0]["headline"]
    assert revs[1]["revision_number"] == 2
    assert "3,200 crore" in revs[1]["headline"]

    # 5. Recovery in fresh process instance
    store2 = PersistentHistoricalEventStore(db_path=db_file)
    assert store2.verify_integrity() is True
    loaded = store2.get_event("evt_lt_01")
    assert loaded is not None
    assert loaded.revision_number == 2
    assert "3,200 crore" in loaded.headline
    assert loaded.jurisdiction == MarketJurisdiction.IN


def test_causal_query_and_instrument_filtering(tmp_path):
    """Verify strict causal time filtering and instrument/subtype/jurisdiction filters."""
    db_file = tmp_path / "causal_query.db"
    store = PersistentHistoricalEventStore(db_path=db_file)
    pipeline = get_nlp_pipeline()

    t1 = datetime(2026, 1, 5, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 10, 10, 0, 0, tzinfo=timezone.utc)
    t3 = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

    r1 = pipeline.process_raw_text("TCS reports Q3 net profit of Rs 12,000 crore", decision_timestamp=t1, event_id="e1")
    r2 = pipeline.process_raw_text("L&T wins mega infrastructure order worth Rs 5,000 crore", decision_timestamp=t2, event_id="e2")
    r3 = pipeline.process_raw_text("AAPL announces Q1 revenue beat of $95 billion", decision_timestamp=t3, event_id="e3")

    store.upsert_event(r1, jurisdiction=MarketJurisdiction.IN)
    store.upsert_event(r2, jurisdiction=MarketJurisdiction.IN)
    store.upsert_event(r3, jurisdiction=MarketJurisdiction.US)

    # Query as of t2 + 1 hour: only e1 and e2 should be visible, e3 (at t3) must be excluded!
    res = store.query_events(decision_timestamp=t2 + timedelta(hours=1))
    assert [e.event_id for e in res] == ["e2", "e1"]

    # Query as of t2 exactly: strict < prevents seeing t2 itself!
    res_at_t2 = store.query_events(decision_timestamp=t2)
    assert [e.event_id for e in res_at_t2] == ["e1"]

    # Filter by jurisdiction IN at t3 + 1 day
    res_in = store.query_events(decision_timestamp=t3 + timedelta(days=1), jurisdiction=MarketJurisdiction.IN)
    assert len(res_in) == 2
    assert all(e.jurisdiction == MarketJurisdiction.IN for e in res_in)

    # Filter by instrument TCS
    res_tcs = store.query_events(decision_timestamp=t3 + timedelta(days=1), instrument_id="TCS")
    assert len(res_tcs) == 1
    assert res_tcs[0].event_id == "e1"


def test_separated_embeddings_and_matured_outcomes(tmp_path):
    """Verify embedding versioning separation and causal maturity gating on outcomes."""
    db_file = tmp_path / "embeddings_outcomes.db"
    store = PersistentHistoricalEventStore(db_path=db_file)
    pipeline = get_nlp_pipeline()

    t_evt = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
    rep = pipeline.process_raw_text("Infosys wins $500 million cloud deal", decision_timestamp=t_evt, event_id="infy_1")
    store.upsert_event(rep, jurisdiction=MarketJurisdiction.IN)

    # Save versioned embeddings
    vec_v1 = [0.1, 0.2, 0.3, 0.4]
    vec_v2 = [0.9, 0.8, 0.7, 0.6]
    store.save_embedding("infy_1", "bge-small-v1", vec_v1)
    store.save_embedding("infy_1", "finbert-v2", vec_v2)

    # Verify model version isolation
    loaded_v1 = store.get_embedding("infy_1", "bge-small-v1")
    loaded_v2 = store.get_embedding("infy_1", "finbert-v2")
    assert loaded_v1 is not None and pytest.approx(loaded_v1, rel=1e-5) == vec_v1
    assert loaded_v2 is not None and pytest.approx(loaded_v2, rel=1e-5) == vec_v2
    assert store.get_embedding("infy_1", "non_existent_model") is None

    # Save outcomes for 1h, 1d, and 5d horizons
    store.save_outcome(StoredOutcomeRecord(
        event_id="infy_1",
        instrument_id="INFY.NS",
        horizon_label="1h",
        outcome_start_ts=t_evt,
        outcome_end_ts=t_evt + timedelta(hours=1),
        return_simple=0.012,
    ))
    store.save_outcome(StoredOutcomeRecord(
        event_id="infy_1",
        instrument_id="INFY.NS",
        horizon_label="1d",
        outcome_start_ts=t_evt,
        outcome_end_ts=t_evt + timedelta(days=1),
        return_simple=0.035,
    ))
    store.save_outcome(StoredOutcomeRecord(
        event_id="infy_1",
        instrument_id="INFY.NS",
        horizon_label="5d",
        outcome_start_ts=t_evt,
        outcome_end_ts=t_evt + timedelta(days=5),
        return_simple=0.080,
    ))

    # Query outcomes at t_evt + 12 hours -> ONLY 1h horizon is matured!
    out_12h = store.get_matured_outcomes(["infy_1"], decision_timestamp=t_evt + timedelta(hours=12))
    assert len(out_12h["infy_1"]) == 1
    assert out_12h["infy_1"][0].horizon_label == "1h"

    # Query outcomes at t_evt + 2 days -> 1h and 1d are matured, 5d MUST be excluded!
    out_2d = store.get_matured_outcomes(["infy_1"], decision_timestamp=t_evt + timedelta(days=2))
    horizons_2d = {o.horizon_label for o in out_2d["infy_1"]}
    assert horizons_2d == {"1h", "1d"}

    # Query outcomes at t_evt + 6 days -> all 3 matured
    out_6d = store.get_matured_outcomes(["infy_1"], decision_timestamp=t_evt + timedelta(days=6))
    assert len(out_6d["infy_1"]) == 3

    stats = store.get_stats()
    assert stats["total_events"] == 1
    assert stats["total_embeddings"] == 2
    assert stats["total_outcomes"] == 3
