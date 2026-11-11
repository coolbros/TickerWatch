"""Integration tests for Phase 4 Unified Historical Intelligence Pipeline (Work Package H)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from backend.alpha.event_embeddings import EventEmbeddingEngine, FinConceptProjectionEncoder
from backend.alpha.event_study import EventStudyEngine, MarketCalendarConfig
from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.historical_intelligence import (
    HistoricalIntelligencePipeline,
    HistoricalIntelligenceRepresentation,
)
from backend.alpha.historical_store import PersistentHistoricalEventStore
from backend.alpha.nlp_pipeline import get_nlp_pipeline
from backend.alpha.outcomes import OutcomeHorizon, PriceObservation
from backend.alpha.schemas import MarketEvent, MarketMessageType


def test_end_to_end_historical_intelligence_pipeline_and_label_separation(tmp_path):
    """
    Verify end-to-end Phase 3 -> Store -> EventStudy -> Vector Retrieval -> Contextual Features
    -> Market Regime -> HistoricalIntelligenceRepresentation without look-ahead leakage.
    """
    db_path = tmp_path / "hist_intel.db"
    store = PersistentHistoricalEventStore(db_path=db_path)
    emb_engine = EventEmbeddingEngine(prefer_onnx=False)
    nlp = get_nlp_pipeline()
    study_engine = EventStudyEngine(calendar=MarketCalendarConfig.for_jurisdiction(MarketJurisdiction.IN))

    base_ts = datetime(2026, 2, 2, 5, 0, tzinfo=timezone.utc)  # Monday 10:30 IST open trading session
    # Index 6 historical LT contract wins + matured 1d outcomes via EventStudyEngine
    for i in range(6):
        t_ev = base_ts + timedelta(days=i * 7)
        rep = nlp.process_raw_text(
            event_id=f"hist_lt_{i}",
            headline=f"Larsen & Toubro secures Rs {2500 + i * 300} crore infrastructure EPC order #{i}",
            local_received_at=t_ev,
        )
        store.upsert_event(rep, jurisdiction=MarketJurisdiction.IN, sector="Infrastructure", is_synthetic=True)

        # Synthetic 1d daily price move for historical event i
        obs = [
            PriceObservation(timestamp_utc=t_ev, price=3500.0),
            PriceObservation(timestamp_utc=t_ev + timedelta(days=1), price=3500.0 * (1.015 + i * 0.002)),
        ]
        lbl = study_engine.compute_outcome_label(
            event_id=f"hist_lt_{i}",
            instrument_id="NSE:LT:EQ",
            horizon=OutcomeHorizon.D1,
            event_timestamp_utc=t_ev,
            decision_timestamp_utc=t_ev,
            observations=obs,
            is_synthetic=True,
        )
        stored_out = lbl.to_stored_outcome_record()
        assert stored_out is not None
        store.save_outcome(stored_out)

    # Also insert 1 future event AFTER query timestamp that MUST NEVER be retrieved
    future_ts = datetime(2026, 4, 15, 5, 0, tzinfo=timezone.utc)
    rep_fut = nlp.process_raw_text(
        event_id="hist_lt_future",
        headline="Larsen & Toubro wins Rs 8000 crore future rail project",
        local_received_at=future_ts,
    )
    store.upsert_event(rep_fut, jurisdiction=MarketJurisdiction.IN, is_synthetic=True)

    # Query Event arriving at 2026-04-01 05:00 UTC (10:30 IST)
    query_ts = datetime(2026, 4, 1, 5, 0, tzinfo=timezone.utc)
    query_rep = nlp.process_raw_text(
        event_id="query_lt_now",
        headline="L&T bags mega Rs 3200 crore power transmission contract",
        local_received_at=query_ts,
    )

    # Pre-event market ticks for LT (STRICTLY <= query_ts) + post-event tick AFTER query_ts that must be excluded
    mkt_events = []
    for m in range(30, -1, -1):
        ts = query_ts - timedelta(minutes=m)
        p = 3600.0 + (30 - m) * 0.5
        mkt_events.append(
            MarketEvent(
                provider="REPLAY",
                venue="NSE",
                instrument_id="NSE:LT:EQ",
                message_type=MarketMessageType.QUOTE,
                local_received_at_utc=ts,
                local_monotonic_ns=int(ts.timestamp() * 1e9),
                bid_price=Decimal(str(p - 0.5)),
                ask_price=Decimal(str(p + 0.5)),
                bid_quantity=500,
                ask_quantity=250,
                last_traded_price=Decimal(str(p)),
            )
        )
    # Post-event spike that must NOT leak into M_t
    mkt_events.append(
        MarketEvent(
            provider="REPLAY",
            venue="NSE",
            instrument_id="NSE:LT:EQ",
            message_type=MarketMessageType.QUOTE,
            local_received_at_utc=query_ts + timedelta(seconds=5),
            local_monotonic_ns=int((query_ts + timedelta(seconds=5)).timestamp() * 1e9),
            bid_price=Decimal("4200.0"),
            ask_price=Decimal("4205.0"),
            bid_quantity=1000,
            ask_quantity=100,
        )
    )

    pipeline = HistoricalIntelligencePipeline(store=store, embedding_engine=emb_engine, nlp_pipeline=nlp)
    result = pipeline.process_event_representation(
        rep=query_rep,
        market_events=mkt_events,
        include_synthetic=True,
        index_event_after_query=True,
        is_synthetic=True,
    )

    assert isinstance(result, HistoricalIntelligenceRepresentation)
    assert result.schema_version == "4.0.0-alpha"
    assert result.jurisdiction == MarketJurisdiction.IN
    assert result.primary_instrument_id == "NSE:LT:EQ"
    assert result.contains_query_future_labels is False
    assert "hist_lt_future" not in result.retrieved_event_ids
    assert query_rep.event_id not in result.retrieved_event_ids
    assert result.comparable_event_count == 6
    assert result.matched_event_count == 6
    assert result.matured_sample_counts["1d"] == 6
    assert result.return_distributions["1d"].insufficient_evidence is False
    assert result.return_distributions["1d"].mean_return is not None and result.return_distributions["1d"].mean_return > 0.015
    assert result.market_regime is not None
    assert result.market_regime.excluded_future_events_count == 1
    assert result.market_regime.mid_price == pytest.approx(3615.0, abs=0.5)
    assert result.provenance.query_event_persist_status == "INSERTED"

    # Also test backwards-compatible Phase 3 bridge
    bridge_res = nlp.build_historical_intelligence(query_rep, store=store, include_synthetic=True)
    assert bridge_res.query_event_id == query_rep.event_id
    # Re-querying must still exclude itself!
    assert query_rep.event_id not in bridge_res.retrieved_event_ids

    from backend.alpha.historical_intelligence import assert_valid_phase4_training_input

    validated = assert_valid_phase4_training_input(result)
    assert validated.historical_context_source == "PHASE_4_PERSISTENT_MATURITY_VERIFIED"
    with pytest.raises(TypeError, match="legacy Phase 3 EventRetrievalContext is prohibited"):
        assert_valid_phase4_training_input(query_rep.historical_context)
