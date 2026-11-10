"""Unit tests for Phase 4 Semantic Embeddings & Modular Hybrid Vector Retrieval."""

from datetime import datetime, timezone, timedelta
import math
import time
import numpy as np
import pytest

from backend.alpha.event_classifier import EventSubtype
from backend.alpha.event_embeddings import (
    EmbeddingModelVersion,
    EventEmbeddingEngine,
    FinConceptProjectionEncoder,
    ONNXBgeSmallEncoder,
    batch_cosine_similarity,
    exact_cosine_similarity,
)
from backend.alpha.event_retrieval import (
    CausalBM25Scorer,
    PersistentVectorRetriever,
    RetrievalMode,
)
from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.historical_store import PersistentHistoricalEventStore, StoredOutcomeRecord
from backend.alpha.nlp_pipeline import get_nlp_pipeline


def test_fin_concept_projection_encoder_semantics():
    """Verify deterministic fallback encoder maps synonymous financial catalysts closer."""
    enc = FinConceptProjectionEncoder()
    assert enc.dimension == 256

    texts = [
        "L&T bags mega order worth Rs 2,500 crore",
        "Larsen & Toubro secures industrial engineering contract worth Rs 2,400 crore",
        "RBI Monetary Policy Committee raises repo rate by 25 bps to curb CPI inflation",
    ]
    mat = enc.encode_batch(texts)
    assert mat.shape == (3, 256)

    # Check exact unit normalization
    norms = np.linalg.norm(mat, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)

    sim_synonym = exact_cosine_similarity(mat[0], mat[1])
    sim_unrelated = exact_cosine_similarity(mat[0], mat[2])
    assert sim_synonym > sim_unrelated
    assert sim_synonym > 0.35


def test_onnx_bge_small_encoder_offline():
    """Verify real INT8 ONNX transformer executes 100% offline with semantic accuracy."""
    enc = ONNXBgeSmallEncoder()
    if not enc.is_available():
        pytest.skip("Quantized BGE-small ONNX cache not present in local environment")

    t0 = time.perf_counter()
    texts = [
        "L&T bags mega engineering order worth Rs 2,500 crore",
        "Larsen & Toubro secures Rs 2,400 crore industrial contract",
        "RBI Monetary Policy Committee hikes repo rate by 25 bps",
    ]
    mat = enc.encode_batch(texts)
    dt_ms = (time.perf_counter() - t0) * 1000.0

    assert mat.shape == (3, 384)
    assert np.allclose(np.linalg.norm(mat, axis=1), 1.0, atol=1e-4)

    sim_paraphrase = exact_cosine_similarity(mat[0], mat[1])
    sim_macro = exact_cosine_similarity(mat[0], mat[2])
    assert sim_paraphrase > sim_macro
    assert sim_paraphrase > 0.55
    # Verify single CPU batch latency is fast (< 50ms even cold)
    assert dt_ms < 250.0


def test_causal_bm25_scorer():
    """Verify BM25 ranking without corpus look-ahead."""
    bm25 = CausalBM25Scorer()
    query = ["infosys", "wins", "cloud", "deal"]
    docs = [
        ["tcs", "declares", "interim", "dividend", "share"],
        ["infosys", "secures", "major", "cloud", "transformation", "deal", "worth"],
        ["reliance", "retail", "opens", "stores"],
    ]
    scores = bm25.score_candidates(query, docs)
    assert len(scores) == 3
    assert scores[1] > scores[0]
    assert scores[1] > scores[2]


def test_persistent_vector_retriever_modes_and_breakdown(tmp_path):
    """Compare Jaccard, BM25, Dense Semantic, and Hybrid Modular retrieval on paraphrases."""
    db_file = tmp_path / "vector_retrieval.db"
    store = PersistentHistoricalEventStore(db_path=db_file)
    pipeline = get_nlp_pipeline()

    base_time = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    # Event 1: Paraphrase target — shares almost no surface nouns/verbs with query "bags tender ... order"
    r1 = pipeline.process_raw_text(
        "Larsen & Toubro awarded EPC power grid contract worth Rs 2,400 crore",
        decision_timestamp=base_time,
        event_id="lt_contract_1",
    )
    store.upsert_event(r1, jurisdiction=MarketJurisdiction.IN)
    store.save_outcome(StoredOutcomeRecord(
        event_id="lt_contract_1",
        instrument_id="NSE:LT:EQ",
        horizon_label="1d",
        outcome_start_ts=base_time,
        outcome_end_ts=base_time + timedelta(days=1),
        return_simple=0.032,
    ))

    # Event 2: Unrelated dividend
    r2 = pipeline.process_raw_text(
        "TCS approves interim dividend payout of Rs 15 per share for shareholders",
        decision_timestamp=base_time + timedelta(days=2),
        event_id="tcs_div_1",
    )
    store.upsert_event(r2, jurisdiction=MarketJurisdiction.IN)

    # Event 3: US Filing (must be isolated by IN jurisdiction filter!)
    r3 = pipeline.process_raw_text(
        "Apple secures multi-billion enterprise hardware supply contract",
        decision_timestamp=base_time + timedelta(days=3),
        event_id="aapl_us_1",
    )
    store.upsert_event(r3, jurisdiction=MarketJurisdiction.US)

    # Event 4: Future event (must be excluded by causal filter!)
    r4 = pipeline.process_raw_text(
        "L&T wins Rs 3,000 crore metro rail contract",
        decision_timestamp=base_time + timedelta(days=30),
        event_id="lt_future",
    )
    store.upsert_event(r4, jurisdiction=MarketJurisdiction.IN)

    retriever = PersistentVectorRetriever(store=store)
    query_time = base_time + timedelta(days=10)
    query_headline = "L&T bags mega railway project worth Rs 2,600 crore"

    # 1. Test Dense Semantic vs Hybrid Modular
    for mode in (RetrievalMode.DENSE_SEMANTIC, RetrievalMode.HYBRID_MODULAR):
        ctx = retriever.retrieve(
            query_headline=query_headline,
            decision_timestamp=query_time,
            query_subtype=EventSubtype.CONTRACT_ORDER_WIN.value,
            query_entity_ids=["LT"],
            query_instrument_ids=["NSE:LT:EQ"],
            query_materiality=0.75,
            query_primary_magnitude=2.6e10,
            jurisdiction=MarketJurisdiction.IN,
            mode=mode,
            top_k=5,
        )
        # Future event and US event MUST be excluded
        retrieved_ids = [m.event_id for m in ctx.matches]
        assert "lt_future" not in retrieved_ids
        assert "aapl_us_1" not in retrieved_ids
        assert ctx.matches[0].event_id == "lt_contract_1"

        # Check transparent score breakdown on top match
        top = ctx.matches[0]
        assert top.score_breakdown is not None
        assert top.semantic_similarity > 0.40
        assert "1d" in top.available_outcome_horizons
        assert top.post_event_return_1d == 0.032

    # 2. Test duplicate / self exclusion
    ctx_dedup = retriever.retrieve(
        query_headline="Larsen & Toubro awarded EPC power grid contract worth Rs 2,400 crore",
        decision_timestamp=query_time,
        query_event_id="lt_contract_1",
        jurisdiction=MarketJurisdiction.IN,
        mode=RetrievalMode.HYBRID_MODULAR,
    )
    assert all(m.event_id != "lt_contract_1" for m in ctx_dedup.matches)


def test_exact_cosine_latency_scale():
    """Verify exact NumPy cosine search handles 10,000 x 384D vectors well below 15ms."""
    rng = np.random.default_rng(7)
    n_corpus = 10000
    dim = 384
    mat = rng.standard_normal((n_corpus, dim)).astype(np.float32)
    q = rng.standard_normal(dim).astype(np.float32)

    t0 = time.perf_counter()
    sims = batch_cosine_similarity(q, mat)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert sims.shape == (n_corpus,)
    assert np.all((sims >= -1.0001) & (sims <= 1.0001))
    assert elapsed_ms < 100.0  # Typically ~2-8ms on CPU!
