"""Comprehensive Phase 4 Verification Suite: Historical Retrieval, Event Study & Contextual Intelligence.

Explicitly tests all 22 Phase 4 verification requirements offline using synthetic test data
cleanly flagged with `is_synthetic=True` and isolated inside temporary directories.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest

from backend.alpha.contextual_features import EvidenceStatus
from backend.alpha.event_classifier import EventSubtype
from backend.alpha.event_embeddings import EventEmbeddingEngine
from backend.alpha.event_retrieval import PersistentVectorRetriever, RetrievalMode
from backend.alpha.event_study import (
    AnchorMode,
    EventStudyEngine,
    FuturesRolloverMethod,
    FuturesRolloverSpec,
    MarketCalendarConfig,
)
from backend.alpha.historical_evaluation import verify_event_study_suite
from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.historical_intelligence import (
    HistoricalIntelligencePipeline,
    HistoricalIntelligenceRepresentation,
)
from backend.alpha.historical_store import PersistentHistoricalEventStore, StoredOutcomeRecord
from backend.alpha.nlp_pipeline import get_nlp_pipeline
from backend.alpha.outcomes import OutcomeDataQualityStatus, OutcomeHorizon, PriceObservation
from backend.alpha.schemas import DepthLevel, MarketEvent, MarketMessageType



def _utc(y: int, m: int, d: int, hh: int = 5, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def test_store_recovery_dedup_revisions_and_embedding_versioning(tmp_path):
    """Covers: persistent store recovery, event deduplication, revision handling, embedding versioning."""
    db_file = tmp_path / "phase4_recovery.db"
    nlp = get_nlp_pipeline()
    t1 = _utc(2026, 1, 5, 5, 0)

    store1 = PersistentHistoricalEventStore(db_path=db_file)
    rep_v1 = nlp.process_raw_text(
        event_id="evt_rev_01",
        headline="TCS wins Rs 1500 crore European telecom transformation deal",
        local_received_at=t1,
    )
    eid, status1 = store1.upsert_event(rep_v1, jurisdiction=MarketJurisdiction.IN, is_synthetic=True)
    assert eid == "evt_rev_01" and status1 == "INSERTED"

    # Duplicate content under a DIFFERENT event_id must be deduplicated
    rep_dup = nlp.process_raw_text(
        event_id="evt_dup_99",
        headline="  TCS wins Rs 1500 crore European telecom transformation deal  ",
        local_received_at=t1 + timedelta(minutes=2),
    )
    _, status_dup = store1.upsert_event(rep_dup, jurisdiction=MarketJurisdiction.IN, is_synthetic=True)
    assert status_dup == "SKIPPED_DUPLICATE"

    # Revision under SAME event_id updates headline and logs revision audit trail
    rep_v2 = nlp.process_raw_text(
        event_id="evt_rev_01",
        headline="Correction: TCS wins Rs 1800 crore European telecom transformation deal",
        local_received_at=t1 + timedelta(minutes=10),
    )
    _, status_rev = store1.upsert_event(rep_v2, jurisdiction=MarketJurisdiction.IN, is_synthetic=True)
    assert status_rev == "UPDATED_REVISION"

    # Persist embedding under explicit model version A
    store1.save_embedding("evt_rev_01", "model-alpha-v1", [0.1] * 16)
    del store1

    # Fresh store process restarts and recovers full state cleanly
    store2 = PersistentHistoricalEventStore(db_path=db_file)
    recovered = store2.get_event("evt_rev_01")
    assert recovered is not None
    assert recovered.revision_number == 2
    assert "Rs 1800 crore" in recovered.headline
    revs = store2.get_revisions("evt_rev_01")
    assert len(revs) == 2

    # Embedding versioning: model-alpha-v2 MUST NOT read model-alpha-v1 vector
    assert store2.get_embedding("evt_rev_01", "model-alpha-v1") is not None
    assert store2.get_embedding("evt_rev_01", "model-beta-v2") is None


def test_semantic_vs_lexical_hybrid_filters_and_multi_entity(tmp_path):
    """Covers: semantic retrieval, lexical vs dense ranking, hybrid ranking, filters, multi-entity, empty/undersized."""
    db_file = tmp_path / "phase4_retrieval.db"
    store = PersistentHistoricalEventStore(db_path=db_file)
    nlp = get_nlp_pipeline()
    emb_engine = EventEmbeddingEngine(prefer_onnx=True)
    retriever = PersistentVectorRetriever(store=store, embedding_engine=emb_engine)

    # 1. Empty corpus graceful behavior
    t_now = _utc(2026, 3, 2, 6, 0)
    pipe = HistoricalIntelligencePipeline(store=store, embedding_engine=emb_engine, nlp_pipeline=nlp)
    q_empty = nlp.process_raw_text(
        event_id="q_empty",
        headline="Infosys secures cloud migration engagement",
        local_received_at=t_now,
    )
    empty_res = pipe.process_event_representation(q_empty, include_synthetic=True, is_synthetic=True)
    assert empty_res.comparable_event_count == 0
    assert "EMPTY_HISTORICAL_CORPUS" in empty_res.data_quality_flags
    assert empty_res.confidence_and_uncertainty.historical_evidence_status == EvidenceStatus.NO_MATCHED_OUTCOMES

    # 2. Seed historical events including a multi-entity event (TCS + Infosys) & paraphrase target
    samples = [
        ("h_paraphrase", "Infosys wins digital transformation contract in European insurance", _utc(2026, 1, 10)),
        ("h_distractor", "Infosys annual board governance schedule notice for FY26", _utc(2026, 1, 12)),
        ("h_multi_ent", "TCS and HDFC Bank enter strategic core banking technology alliance deal", _utc(2026, 1, 15)),
        ("h_us_isolated", "Apple announces Q1 iPhone sales milestone of $95 billion", _utc(2026, 1, 18)),
    ]
    for eid, text, ts in samples:
        jur = MarketJurisdiction.US if "Apple" in text else MarketJurisdiction.IN
        rep = nlp.process_raw_text(event_id=eid, headline=text, local_received_at=ts)
        store.upsert_event(rep, jurisdiction=jur, is_synthetic=True)

    # Dense semantic query using low exact word overlap paraphrase
    q_para = "IT major INFY selected as vendor for Nordic carrier software modernization project"
    ctx_jaccard = retriever.retrieve(
        query_headline=q_para,
        decision_timestamp=t_now,
        jurisdiction=MarketJurisdiction.IN,
        mode=RetrievalMode.JACCARD,
        include_synthetic=True,
    )
    ctx_dense = retriever.retrieve(
        query_headline=q_para,
        decision_timestamp=t_now,
        jurisdiction=MarketJurisdiction.IN,
        mode=RetrievalMode.DENSE_SEMANTIC,
        include_synthetic=True,
    )
    ctx_hybrid = retriever.retrieve(
        query_headline=q_para,
        decision_timestamp=t_now,
        query_entity_ids=["INFY"],
        query_instrument_ids=["NSE:INFY:EQ"],
        query_subtype=EventSubtype.CONTRACT_ORDER_WIN.value,
        jurisdiction=MarketJurisdiction.IN,
        mode=RetrievalMode.HYBRID_MODULAR,
        include_synthetic=True,
    )
    # Dense & Hybrid both put h_paraphrase #1 strictly ahead of governance distractor, and NEVER include US Apple
    assert ctx_dense.matches[0].event_id == "h_paraphrase"
    assert ctx_hybrid.matches[0].event_id == "h_paraphrase"
    assert all(m.event_id != "h_us_isolated" for m in ctx_hybrid.matches)
    assert ctx_hybrid.matches[0].score_breakdown is not None
    # Verify Lexical Jaccard vs Dense ranking divergence or ordering
    assert ctx_jaccard.matches[0].score_breakdown.lexical_jaccard <= ctx_dense.matches[0].score_breakdown.semantic_cosine

    # 3. Secondary entity / instrument filter catches HDFC Bank inside multi-entity h_multi_ent
    ctx_hdfc = retriever.retrieve(
        query_headline="HDFC Bank digital systems update",
        decision_timestamp=t_now,
        instrument_filter="NSE:HDFCBANK:EQ",
        jurisdiction=MarketJurisdiction.IN,
        include_synthetic=True,
    )
    assert any(m.event_id == "h_multi_ent" for m in ctx_hdfc.matches)

    # 4. Undersized sample gating (only 2 IN contract wins -> insufficient_evidence_flag=True)
    res_under = pipe.process_event_representation(
        q_empty, include_synthetic=True, is_synthetic=True
    )
    assert res_under.confidence_and_uncertainty.insufficient_historical_evidence is True


def test_causal_timestamps_and_1d_vs_5d_outcome_maturity(tmp_path):
    """Covers: historical event timestamps, immature outcome exclusion, 1d and 5d outcome maturity."""
    db_file = tmp_path / "phase4_maturity.db"
    store = PersistentHistoricalEventStore(db_path=db_file)
    nlp = get_nlp_pipeline()

    # Historical event published 2 days before decision_ts (so 1d outcome HAS matured, 5d outcome HAS NOT!)
    decision_ts = _utc(2026, 3, 10, 5, 0)
    t_hist_2d_ago = decision_ts - timedelta(days=2)

    rep_recent = nlp.process_raw_text(
        event_id="evt_recent_2d",
        headline="TCS reports Q3 net profit of Rs 12000 crore",
        local_received_at=t_hist_2d_ago,
    )
    store.upsert_event(rep_recent, jurisdiction=MarketJurisdiction.IN, is_synthetic=True)

    # Store both 1d (ends 1 day ago <= decision_ts) and 5d (ends 3 days in future > decision_ts)
    store.save_outcome(
        StoredOutcomeRecord(
            event_id="evt_recent_2d",
            instrument_id="NSE:TCS:EQ",
            horizon_label="1d",
            outcome_start_ts=t_hist_2d_ago,
            outcome_end_ts=t_hist_2d_ago + timedelta(days=1),
            return_simple=0.025,
            data_quality_status="VERIFIED",
        )
    )
    store.save_outcome(
        StoredOutcomeRecord(
            event_id="evt_recent_2d",
            instrument_id="NSE:TCS:EQ",
            horizon_label="5d",
            outcome_start_ts=t_hist_2d_ago,
            outcome_end_ts=t_hist_2d_ago + timedelta(days=5),  # 3 days in the FUTURE!
            return_simple=0.099,
            data_quality_status="VERIFIED",
        )
    )

    # Future event occurring AFTER decision_ts must NOT even appear in matches
    rep_future = nlp.process_raw_text(
        event_id="evt_future",
        headline="TCS reports Q4 net profit of Rs 13500 crore",
        local_received_at=decision_ts + timedelta(hours=1),
    )
    store.upsert_event(rep_future, jurisdiction=MarketJurisdiction.IN, is_synthetic=True)

    pipe = HistoricalIntelligencePipeline(
        store=store, embedding_engine=EventEmbeddingEngine(prefer_onnx=False), nlp_pipeline=nlp
    )
    q_now = nlp.process_raw_text(
        event_id="evt_query_now",
        headline="TCS quarterly net earnings rise to Rs 12500 crore",
        local_received_at=decision_ts,
    )
    res = pipe.process_event_representation(q_now, include_synthetic=True, is_synthetic=True)

    assert "evt_future" not in res.retrieved_event_ids
    assert "evt_recent_2d" in res.retrieved_event_ids
    match = next(m for m in res.retrieved_matches if m.event_id == "evt_recent_2d")
    assert match.is_1d_outcome_matured is True
    assert match.post_event_return_1d == pytest.approx(0.025, abs=1e-6)
    # 5d outcome is STRICTLY excluded!
    assert match.is_5d_outcome_matured is False
    assert match.post_event_return_5d is None
    assert "5d" not in match.available_outcome_horizons
    assert res.matured_sample_counts["1d"] == 1
    assert res.matured_sample_counts["5d"] == 0


def test_event_study_suite_sessions_rollover_and_regime_joins(tmp_path):
    """Covers: returns, session boundaries, overnight gaps, missing data, rollover, benchmark adjust, regime join, separation."""
    # 1. All 6 formal event-study invariants
    es_checks = verify_event_study_suite()
    assert all(es_checks.values()), f"Failed checks: {es_checks}"

    # 2. Session-crossing 1h intraday horizon near NSE market close (15:15 IST -> 10:05 IST next day!)
    # NSE opens 03:45 UTC (09:15 IST), closes 10:00 UTC (15:30 IST).
    cal = MarketCalendarConfig.for_jurisdiction(MarketJurisdiction.IN, holidays={date(2026, 3, 4)})
    engine = EventStudyEngine(calendar=cal, anchor_mode=AnchorMode.PRE_EVENT_AS_OF)

    # Build 1m observations on Mon 2026-03-02 (09:15-15:30 IST) and Tue 2026-03-03 (09:15-15:30 IST)
    obs = []
    for d in (date(2026, 3, 2), date(2026, 3, 3)):
        open_utc = datetime(d.year, d.month, d.day, 3, 45, tzinfo=timezone.utc)
        for m in range(376):
            p = 1000.0 if d == date(2026, 3, 2) else 1030.0  # +3% overnight gap up!
            obs.append(PriceObservation(timestamp_utc=open_utc + timedelta(minutes=m), price=p))

    t_near_close = _utc(2026, 3, 2, 9, 45)  # 15:15 IST (15 min before close)
    lbl_1h = engine.compute_outcome_label(
        event_id="evt_close_cross",
        instrument_id="NSE:RELIANCE:EQ",
        horizon=OutcomeHorizon.H1,
        event_timestamp_utc=t_near_close,
        decision_timestamp_utc=t_near_close,
        observations=obs,
        is_synthetic=True,
    )
    assert lbl_1h.data_quality_status == OutcomeDataQualityStatus.VERIFIED
    assert lbl_1h.session_boundary_crossed is True
    assert lbl_1h.overnight_gap_included is True
    # 15m left on Mar 2 + 45m on Mar 3 from 03:45 UTC = Mar 3 04:30 UTC (10:00 IST)
    assert lbl_1h.outcome_end_price_ts == _utc(2026, 3, 3, 4, 30)
    assert lbl_1h.simple_return == pytest.approx(0.03, abs=1e-4)

    # 3. End-to-End Market Regime As-Of Join + Strict Feature/Label Separation
    store = PersistentHistoricalEventStore(db_path=tmp_path / "e2e_sep.db")
    nlp = get_nlp_pipeline()
    pipe = HistoricalIntelligencePipeline(
        store=store, embedding_engine=EventEmbeddingEngine(prefer_onnx=False), nlp_pipeline=nlp
    )
    rep_q = nlp.process_raw_text(
        event_id="evt_sep_q",
        headline="Reliance Industries Q3 net profit surges 15% to Rs 19000 crore",
        local_received_at=_utc(2026, 3, 5, 5, 0),
    )
    mkt_stream = [
        MarketEvent(
            provider="REPLAY",
            venue="NSE",
            instrument_id="NSE:RELIANCE:EQ",
            message_type=MarketMessageType.QUOTE,
            local_received_at_utc=_utc(2026, 3, 5, 5, 0) - timedelta(seconds=10),
            local_monotonic_ns=100,
            bid_price=Decimal("2800.0"),
            ask_price=Decimal("2801.0"),
            bid_quantity=500,
            ask_quantity=500,
            depth_bids=[DepthLevel(price=Decimal("2800.0"), quantity=500)],
            depth_asks=[DepthLevel(price=Decimal("2801.0"), quantity=500)],
        ),
        MarketEvent(
            provider="REPLAY",
            venue="NSE",
            instrument_id="NSE:RELIANCE:EQ",
            message_type=MarketMessageType.QUOTE,
            local_received_at_utc=_utc(2026, 3, 5, 5, 0) + timedelta(seconds=1),  # Future tick!
            local_monotonic_ns=200,
            bid_price=Decimal("3100.0"),
            ask_price=Decimal("3102.0"),
            bid_quantity=100,
            ask_quantity=100,
        ),
    ]
    res = pipe.process_event_representation(
        rep_q, market_events=mkt_stream, include_synthetic=True, is_synthetic=True
    )
    assert isinstance(res, HistoricalIntelligenceRepresentation)
    assert res.contains_query_future_labels is False
    feat_dict = res.to_feature_dict()
    assert feat_dict["contains_query_future_labels"] is False
    assert res.market_regime.excluded_future_events_count == 1
    assert res.market_regime.mid_price == pytest.approx(2800.5, abs=0.01)
