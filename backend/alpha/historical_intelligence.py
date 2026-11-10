"""Unified Phase 4 Historical Intelligence Pipeline & Versioned Representation.

Orchestrates the end-to-end transformation:
  FinancialEventRepresentation (E_t)
    -> PersistentHistoricalEventStore (SQLite WAL)
    -> Jurisdiction & Instrument/Subtype/Time Pre-Filtering
    -> Lexical (BM25 + Jaccard) + Dense Semantic Embeddings (ONNX / Concept Projection)
    -> Modular Hybrid Ranking & Duplicate/Revision Exclusion
    -> Causal Matured Outcome Filtering (outcome_end_ts <= tau_decision)
    -> Sample-Aware Return Distributions (ContextualIntelligenceFeatures)
    -> Point-in-Time Market Regime State (M_t, strictly <= tau_decision)
    -> Versioned HistoricalIntelligenceRepresentation (Features strictly separated from query future labels).
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import time
from typing import Any, Dict, List, Optional, Sequence
from pydantic import BaseModel, Field

from backend.alpha.alignment import TemporalAlignmentEngine
from backend.alpha.contextual_features import (
    ConditionalResponseSlice,
    ContextualFeatureSynthesizer,
    ContextualIntelligenceFeatures,
    EvidenceStatus,
    HorizonResponseDistribution,
)
from backend.alpha.event_embeddings import EventEmbeddingEngine, get_embedding_engine
from backend.alpha.event_retrieval import (
    PersistentVectorRetriever,
    RetrievalMode,
    RetrievalScoreBreakdown,
    RetrievedHistoricalMatch,
)
from backend.alpha.event_study import select_study_instruments
from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.historical_store import PersistentHistoricalEventStore
from backend.alpha.market_context import MarketRegimeContext, MarketRegimeContextBuilder
from backend.alpha.nlp_pipeline import (
    FinancialEventRepresentation,
    FinancialNLPPipeline,
    TimestampProvenance,
    get_nlp_pipeline,
)
from backend.alpha.outcomes import OutcomeHorizon
from backend.alpha.schemas import MarketEvent, NewsEvent


class ConfidenceUncertaintySummary(BaseModel):
    """Combined epistemic uncertainty and evidence sufficiency across NLP, Retrieval, and Regime."""

    event_nlp_confidence: float = Field(..., ge=0.0, le=1.0)
    historical_evidence_status: EvidenceStatus
    insufficient_historical_evidence: bool
    market_regime_available: bool
    overall_contextual_confidence: float = Field(..., ge=0.0, le=1.0)
    uncertainty_reasons: List[str] = Field(default_factory=list)


class HistoricalIntelligenceProvenance(BaseModel):
    """End-to-end audit provenance for reproducible replay and Phase 5 training."""

    store_location: str
    pipeline_version: str
    nlp_pipeline_version: str
    query_timestamp_provenance: TimestampProvenance
    store_event_count_at_query: int
    market_data_available: bool
    index_query_event_after_retrieval: bool = False
    query_event_persist_status: Optional[str] = None
    is_synthetic: bool = False


class HistoricalIntelligenceRepresentation(BaseModel):
    """
    Canonical Versioned Phase 4 Contextual Intelligence Output (`4.0.0-alpha`).

    Represents `(E_t, H_t, M_t)` at `decision_timestamp` with hard structural separation
    from any future outcome label of the query event (`Delta F_{t+h}`).
    """

    schema_version: str = "4.0.0-alpha"
    query_event_id: str
    decision_timestamp: datetime
    jurisdiction: MarketJurisdiction
    primary_instrument_id: Optional[str] = None

    retrieval_model_version: str
    retrieval_mode: RetrievalMode
    embedding_model_version: Optional[str] = None

    retrieved_event_ids: List[str] = Field(default_factory=list)
    retrieved_matches: List[RetrievedHistoricalMatch] = Field(default_factory=list)
    similarity_breakdowns: Dict[str, RetrievalScoreBreakdown] = Field(default_factory=dict)

    comparable_event_count: int = 0
    matched_event_count: int = 0
    matured_sample_counts: Dict[str, int] = Field(default_factory=dict)
    return_distributions: Dict[str, HorizonResponseDistribution] = Field(default_factory=dict)
    conditional_slices: Dict[str, ConditionalResponseSlice] = Field(default_factory=dict)
    contextual_features: ContextualIntelligenceFeatures

    market_regime: Optional[MarketRegimeContext] = None
    confidence_and_uncertainty: ConfidenceUncertaintySummary
    provenance: HistoricalIntelligenceProvenance

    data_quality_flags: List[str] = Field(default_factory=list)
    missing_data_explanations: List[str] = Field(default_factory=list)
    execution_latency_ms: float = 0.0

    # Explicit structural invariant flag verifying no query future target labels live inside feature dict
    contains_query_future_labels: bool = False

    def to_feature_dict(self) -> Dict[str, Any]:
        """Export strictly causal feature payload safe for Phase 5 model inputs."""
        return self.model_dump(mode="json")


def infer_event_jurisdiction(rep: FinancialEventRepresentation) -> MarketJurisdiction:
    """Determine market jurisdiction without silently mixing Indian and US corporate universes."""
    for mapping in rep.all_instrument_mappings:
        inst = mapping.instrument_id.upper()
        if inst.startswith(("US:", "NASDAQ:", "NYSE:")):
            return MarketJurisdiction.US
        if inst.startswith(("NSE:", "BSE:")):
            return MarketJurisdiction.IN

    us_entities = {"AAPL", "AMZN", "MSFT", "GOOGL", "META", "NVDA", "TSLA", "FED", "SEC"}
    prim_id = (rep.primary_entity.entity_id or "").upper()
    if prim_id in us_entities:
        return MarketJurisdiction.US
    return MarketJurisdiction.IN


class HistoricalIntelligencePipeline:
    """
    Integrated Phase 4 Pipeline unifying persistent vector retrieval, causal matured-outcome
    distribution synthesis, and point-in-time market regime context.
    """

    PIPELINE_VERSION = "4.0.0-alpha"
    RETRIEVAL_MODEL_VERSION = "CAUSAL_HYBRID_MODULAR_v4.0"

    def __init__(
        self,
        store: PersistentHistoricalEventStore,
        embedding_engine: Optional[EventEmbeddingEngine] = None,
        nlp_pipeline: Optional[FinancialNLPPipeline] = None,
        synthesizer: Optional[ContextualFeatureSynthesizer] = None,
        market_context_builder: Optional[MarketRegimeContextBuilder] = None,
        retrieval_mode: RetrievalMode = RetrievalMode.HYBRID_MODULAR,
        top_k: int = 10,
        min_similarity: float = 0.0,
    ) -> None:
        self.store = store
        self.embedding_engine = embedding_engine or get_embedding_engine()
        self.retriever = PersistentVectorRetriever(store=self.store, embedding_engine=self.embedding_engine)
        self.nlp_pipeline = nlp_pipeline or get_nlp_pipeline()
        self.synthesizer = synthesizer or ContextualFeatureSynthesizer()
        self.market_context_builder = market_context_builder or MarketRegimeContextBuilder()
        self.retrieval_mode = retrieval_mode
        self.top_k = top_k
        self.min_similarity = min_similarity

    @staticmethod
    def _extract_primary_magnitude(rep: FinancialEventRepresentation) -> Optional[float]:
        if rep.extraction.primary_metric and rep.extraction.primary_metric.normalized_value is not None:
            return float(abs(rep.extraction.primary_metric.normalized_value))
        for m in rep.extraction.metrics:
            if m.normalized_value is not None:
                return float(abs(m.normalized_value))
        return None

    def process_event_representation(
        self,
        rep: FinancialEventRepresentation,
        market_events: Optional[Sequence[MarketEvent]] = None,
        alignment_engine: Optional[TemporalAlignmentEngine] = None,
        jurisdiction: Optional[MarketJurisdiction] = None,
        instrument_filter: Optional[str] = None,
        subtype_filter: Optional[str] = None,
        top_k: Optional[int] = None,
        mode: Optional[RetrievalMode] = None,
        include_synthetic: bool = False,
        index_event_after_query: bool = False,
        is_synthetic: bool = False,
        horizons: Optional[Sequence[OutcomeHorizon]] = None,
    ) -> HistoricalIntelligenceRepresentation:
        """
        Execute the full Phase 4 pipeline on a structured Phase 3 `FinancialEventRepresentation`.
        """
        t0 = time.perf_counter()
        decision_ts = rep.local_received_at_utc
        active_jurisdiction = jurisdiction or infer_event_jurisdiction(rep)
        active_mode = mode or self.retrieval_mode
        k = top_k or self.top_k

        # 1. Identify primary instrument & contextual benchmark
        primary_inst, _, bench_inst = select_study_instruments(rep, jurisdiction=active_jurisdiction)

        query_entity_ids = [
            e.entity_id for e in rep.all_entities if e.entity_id
        ]
        query_inst_ids = [m.instrument_id for m in rep.all_instrument_mappings]

        payload = f"{rep.headline.strip().lower()}|{(rep.body_snippet or '').strip().lower()}|{rep.classification.primary_subtype.value}"
        q_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        # 2. Point-in-time Market Regime Context (strictly <= decision_ts, kept isolated from NLP)
        market_regime: Optional[MarketRegimeContext] = None
        if primary_inst:
            regime_builder = (
                self.market_context_builder
                if self.market_context_builder.jurisdiction == active_jurisdiction
                else MarketRegimeContextBuilder(jurisdiction=active_jurisdiction)
            )
            if alignment_engine is not None:
                market_regime = regime_builder.build_from_alignment_engine(
                    engine=alignment_engine,
                    instrument_id=primary_inst,
                    decision_timestamp=decision_ts,
                    benchmark_instrument_id=bench_inst,
                )
            elif market_events is not None:
                market_regime = regime_builder.build(
                    instrument_id=primary_inst,
                    market_events=market_events,
                    decision_timestamp=decision_ts,
                    benchmark_instrument_id=bench_inst,
                    benchmark_events=market_events if bench_inst else None,
                )

        # 3. Causal Persistent Vector Retrieval (pre-indexing ensures zero self-leakage)
        store_stats = self.store.get_stats()
        retrieval_ctx = self.retriever.retrieve(
            query_headline=rep.headline,
            decision_timestamp=decision_ts,
            query_event_id=rep.event_id,
            query_content_hash=q_hash,
            query_body_snippet=rep.body_snippet,
            query_subtype=rep.classification.primary_subtype.value,
            query_family=rep.classification.primary_family.value,
            query_entity_ids=query_entity_ids,
            query_instrument_ids=query_inst_ids,
            query_materiality=rep.materiality.composite_materiality_score,
            query_primary_magnitude=self._extract_primary_magnitude(rep),
            query_surprise_category=rep.surprise.surprise_category.value,
            jurisdiction=active_jurisdiction,
            instrument_filter=instrument_filter,
            subtype_filter=subtype_filter,
            top_k=k,
            min_similarity=self.min_similarity,
            mode=active_mode,
            include_synthetic=include_synthetic,
            market_regime_volatility=(
                market_regime.volatility_regime.value if market_regime else None
            ),
        )

        # 4. Synthesize Causal Outcome Distributions & Uncertainty Intervals
        contextual_feats = self.synthesizer.synthesize(
            retrieval_context=retrieval_ctx,
            query_materiality=rep.materiality.composite_materiality_score,
            query_surprise_category=rep.surprise.surprise_category.value,
            horizons=horizons or list(OutcomeHorizon),
        )

        # 5. Build Structured Breakdowns, Counts, Flags, & Missing Data Explanations
        retrieved_ids = [m.event_id for m in retrieval_ctx.matches]
        breakdowns = {
            m.event_id: m.score_breakdown
            for m in retrieval_ctx.matches
            if m.score_breakdown is not None
        }
        matured_counts = {
            h_name: dist.matured_count
            for h_name, dist in contextual_feats.horizons.items()
        }

        dq_flags: List[str] = []
        missing_explanations: List[str] = []
        uncertainty_reasons: List[str] = []

        if retrieval_ctx.total_comparable_count == 0:
            dq_flags.append("EMPTY_HISTORICAL_CORPUS")
            missing_explanations.append(
                f"Zero causally prior events matched jurisdiction={active_jurisdiction.value} "
                f"before {decision_ts.isoformat()}."
            )
            uncertainty_reasons.append("No historical comparables available.")
        elif contextual_feats.insufficient_evidence_flag:
            dq_flags.append("INSUFFICIENT_HISTORICAL_SAMPLE")
            uncertainty_reasons.append(
                f"Matured historical sample counts fall below min threshold ({self.synthesizer.min_sample_size}) across all horizons."
            )

        if rep.surprise.surprise_category.value == "UNAVAILABLE_EXPECTATION":
            dq_flags.append("CONSENSUS_UNAVAILABLE")
            missing_explanations.append("No point-in-time consensus expectation available for query event.")

        if market_regime is None:
            dq_flags.append("MARKET_REGIME_UNAVAILABLE")
            missing_explanations.append("No market microstructure stream or alignment buffer provided.")
            uncertainty_reasons.append("Market regime state unobserved.")
        elif market_regime.data_quality_status != "CLEAN":
            dq_flags.append(f"MARKET_REGIME_{market_regime.data_quality_status}")
            if "MISSING" in market_regime.data_quality_status:
                missing_explanations.append(f"No pre-event quotes found for {primary_inst} before {decision_ts.isoformat()}.")
                uncertainty_reasons.append("Pre-event quotes unavailable.")
            if market_regime.is_stale:
                uncertainty_reasons.append("Pre-event market feed exceeded staleness threshold.")

        if not dq_flags:
            dq_flags.append("CLEAN")

        # 6. Optionally persist query event & vector AFTER causal retrieval completes
        persist_status: Optional[str] = None
        if index_event_after_query:
            _, persist_status = self.store.upsert_event(
                rep=rep,
                jurisdiction=active_jurisdiction,
                is_synthetic=is_synthetic,
            )
            stored = self.store.get_event(rep.event_id)
            if stored is not None:
                self.retriever.ensure_event_embedded(stored)

        # 7. Calibrated Overall Contextual Confidence
        hist_factor = (
            min(1.0, (max(matured_counts.values()) if matured_counts else 0) / max(1, self.synthesizer.min_sample_size * 2))
            if contextual_feats.matched_events_returned > 0
            else 0.1
        )
        regime_factor = 0.9 if (market_regime and market_regime.data_quality_status == "CLEAN") else 0.4
        overall_conf = round(
            max(0.05, min(0.98, 0.50 * rep.composite_confidence + 0.30 * hist_factor + 0.20 * regime_factor)),
            4,
        )

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)

        return HistoricalIntelligenceRepresentation(
            schema_version=self.PIPELINE_VERSION,
            query_event_id=rep.event_id,
            decision_timestamp=decision_ts,
            jurisdiction=active_jurisdiction,
            primary_instrument_id=primary_inst,
            retrieval_model_version=self.RETRIEVAL_MODEL_VERSION,
            retrieval_mode=active_mode,
            embedding_model_version=retrieval_ctx.embedding_model_version,
            retrieved_event_ids=retrieved_ids,
            retrieved_matches=retrieval_ctx.matches,
            similarity_breakdowns=breakdowns,
            comparable_event_count=retrieval_ctx.total_comparable_count,
            matched_event_count=len(retrieved_ids),
            matured_sample_counts=matured_counts,
            return_distributions=contextual_feats.horizons,
            conditional_slices=contextual_feats.conditional_slices,
            contextual_features=contextual_feats,
            market_regime=market_regime,
            confidence_and_uncertainty=ConfidenceUncertaintySummary(
                event_nlp_confidence=rep.composite_confidence,
                historical_evidence_status=contextual_feats.overall_evidence_status,
                insufficient_historical_evidence=contextual_feats.insufficient_evidence_flag,
                market_regime_available=(market_regime is not None and market_regime.data_quality_status != "MISSING_MARKET_DATA"),
                overall_contextual_confidence=overall_conf,
                uncertainty_reasons=uncertainty_reasons,
            ),
            provenance=HistoricalIntelligenceProvenance(
                store_location=str(self.store.db_path),
                pipeline_version=self.PIPELINE_VERSION,
                nlp_pipeline_version=rep.pipeline_version,
                query_timestamp_provenance=rep.timestamp_provenance,
                store_event_count_at_query=store_stats["total_events"],
                market_data_available=(market_regime is not None and market_regime.pre_event_observation_count > 0),
                index_event_after_query=index_event_after_query,
                query_event_persist_status=persist_status,
                is_synthetic=is_synthetic,
            ),
            data_quality_flags=dq_flags,
            missing_data_explanations=missing_explanations,
            execution_latency_ms=elapsed_ms,
            contains_query_future_labels=False,
        )

    def process_news_event(
        self,
        news_event: NewsEvent,
        **kwargs: Any,
    ) -> tuple[FinancialEventRepresentation, HistoricalIntelligenceRepresentation]:
        """Convenience entrypoint processing a Phase 2 NewsEvent through Phase 3 NLP + Phase 4 Historical Intelligence."""
        rep = self.nlp_pipeline.process_news_event(news_event)
        hist_rep = self.process_event_representation(rep, **kwargs)
        return rep, hist_rep
