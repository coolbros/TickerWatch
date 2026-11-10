"""Unified Financial NLP Pipeline and Representation Schema (v1.0.0)."""

from datetime import datetime, timezone
import json
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.alpha.entities import (
    FinancialEntityResolver,
    ResolutionState,
    ResolvedEntity,
    get_entity_resolver,
)
from backend.alpha.event_classifier import (
    EventClassificationResult,
    FinancialEventClassifier,
    get_event_classifier,
)
from backend.alpha.event_extraction import (
    FinancialEventExtractor,
    FinancialExtractionResult,
    get_event_extractor,
)
from backend.alpha.event_retrieval import (
    CausalHistoricalEventRetriever,
    EventRetrievalContext,
    get_event_retriever,
)
from backend.alpha.materiality import (
    FinancialMaterialityEngine,
    MaterialityAssessment,
    get_materiality_engine,
)
from backend.alpha.schemas import NewsEvent
from backend.alpha.surprise import (
    NumericalSurpriseEngine,
    NumericalSurpriseResult,
    SurpriseCategory,
    get_surprise_engine,
)


class FinancialEventRepresentation(BaseModel):
    """
    Canonical, versioned financial event representation (v1.0.0).
    Transforms unstructured news/disclosures into structured, quantified, instrument-linked events
    for downstream temporal alignment and event-conditioned predictive modeling.
    """

    event_id: str
    headline: str
    body_snippet: Optional[str] = None
    source_name: str
    decision_timestamp: datetime = Field(..., description="Strict point-in-time publication or receipt timestamp")
    local_received_at_utc: Optional[datetime] = None

    # NLP Sub-components
    entity_resolution: ResolvedEntity
    classification: EventClassificationResult
    extraction: FinancialExtractionResult
    surprise: NumericalSurpriseResult
    materiality: MaterialityAssessment
    historical_context: EventRetrievalContext

    # Calibrated uncertainty and scoring
    composite_confidence: float = Field(..., ge=0.0, le=1.0)

    # Provenance and telemetry
    pipeline_version: str = "1.0.0-alpha"
    execution_latency_us: int = Field(default=0, description="End-to-end pipeline execution time in microseconds")
    processed_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert representation to dictionary for downstream dataframes or storage."""
        return self.model_dump(mode="json")

    def to_json(self, indent: Optional[int] = None) -> str:
        """Serialize representation to JSON string."""
        return self.model_dump_json(indent=indent)


class FinancialNLPPipeline:
    """
    End-to-end Financial NLP Pipeline.
    Orchestrates entity resolution, event classification, structured numerical extraction,
    point-in-time consensus surprise, composite materiality, and causal historical retrieval.
    """

    def __init__(
        self,
        entity_resolver: Optional[FinancialEntityResolver] = None,
        event_classifier: Optional[FinancialEventClassifier] = None,
        event_extractor: Optional[FinancialEventExtractor] = None,
        surprise_engine: Optional[NumericalSurpriseEngine] = None,
        materiality_engine: Optional[FinancialMaterialityEngine] = None,
        historical_retriever: Optional[CausalHistoricalEventRetriever] = None,
    ) -> None:
        self.entity_resolver = entity_resolver or get_entity_resolver()
        self.event_classifier = event_classifier or get_event_classifier()
        self.event_extractor = event_extractor or get_event_extractor()
        self.surprise_engine = surprise_engine or get_surprise_engine()
        self.materiality_engine = materiality_engine or get_materiality_engine()
        self.historical_retriever = historical_retriever or get_event_retriever()

    def process_news_event(self, news_event: NewsEvent) -> FinancialEventRepresentation:
        """Process canonical Phase 2 NewsEvent through the full financial NLP pipeline."""
        # Use publication timestamp if available; otherwise local receipt timestamp
        decision_ts = news_event.published_at_utc or news_event.local_received_at_utc
        return self.process_raw_text(
            headline=news_event.headline,
            body_text=news_event.body_text,
            decision_timestamp=decision_ts,
            event_id=news_event.event_id,
            source_name=news_event.source_name,
            local_received_at=news_event.local_received_at_utc,
            is_first_announcement=not news_event.is_correction,
        )

    def process_raw_text(
        self,
        headline: str,
        body_text: Optional[str] = None,
        decision_timestamp: Optional[datetime] = None,
        event_id: Optional[str] = None,
        source_name: str = "DIRECT_INGRESS",
        local_received_at: Optional[datetime] = None,
        is_first_announcement: bool = True,
    ) -> FinancialEventRepresentation:
        """Process arbitrary financial headline and body text."""
        start_ns = time.perf_counter_ns()
        ts = decision_timestamp or datetime.now(timezone.utc)
        evt_id = event_id or f"nlp_evt_{int(time.time() * 1000)}"

        # 1. Financial Entity Resolution & Disambiguation
        full_text = f"{headline} {body_text or ''}".strip()
        resolved_entities = self.entity_resolver.resolve_text(full_text)
        primary_entity = resolved_entities[0] if resolved_entities else ResolvedEntity(
            matched_text=headline[:50],
            resolution_state=ResolutionState.UNRESOLVED,
            confidence=0.0,
        )
        instrument_id = primary_entity.entity_id

        # 2. Hierarchical Event Classification
        classification = self.event_classifier.classify_text(headline, body_text)

        # 3. Structured Numerical Extraction
        extraction = self.event_extractor.extract_metrics(headline)

        # 4. Point-in-Time Numerical Surprise Detection
        surprise = self.surprise_engine.compute_surprise(
            metric=extraction.primary_metric,
            instrument_id=instrument_id,
            event_timestamp=ts,
        )

        # 5. Composite Materiality Evaluation
        is_index_constituent = bool(instrument_id in ("RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "LT", "SBIN"))
        materiality = self.materiality_engine.evaluate_materiality(
            classification=classification,
            extraction=extraction,
            surprise=surprise,
            is_first_announcement=is_first_announcement,
            is_index_constituent=is_index_constituent,
        )

        # 6. Causal Historical Event Retrieval
        historical_context = self.historical_retriever.retrieve_comparable_events(
            headline=headline,
            instrument_id=instrument_id,
            event_subtype=classification.primary_subtype,
            materiality_score=materiality.composite_materiality_score,
            decision_timestamp=ts,
            query_event_id=evt_id,
        )

        # 7. Calibrated Composite Confidence Estimation
        # Combine sub-component uncertainties: Entity (0.25), Classification (0.25), Extraction (0.20), Surprise (0.15), Materiality (0.15)
        c_ent = primary_entity.confidence if primary_entity.resolution_state == ResolutionState.RESOLVED else 0.30
        c_cls = classification.confidence
        c_ext = extraction.confidence
        c_sur = 0.90 if surprise.surprise_category != SurpriseCategory.UNAVAILABLE_EXPECTATION else 0.60
        c_mat = materiality.confidence

        composite_conf = (
            0.25 * c_ent
            + 0.25 * c_cls
            + 0.20 * c_ext
            + 0.15 * c_sur
            + 0.15 * c_mat
        )
        composite_conf = round(max(0.10, min(0.98, composite_conf)), 3)

        elapsed_us = max(1, (time.perf_counter_ns() - start_ns) // 1000)

        return FinancialEventRepresentation(
            event_id=evt_id,
            headline=headline,
            body_snippet=body_text[:200] if body_text else None,
            source_name=source_name,
            decision_timestamp=ts,
            local_received_at_utc=local_received_at or ts,
            entity_resolution=primary_entity,
            classification=classification,
            extraction=extraction,
            surprise=surprise,
            materiality=materiality,
            historical_context=historical_context,
            composite_confidence=composite_conf,
            pipeline_version="1.0.0-alpha",
            execution_latency_us=elapsed_us,
        )


_nlp_pipeline: Optional[FinancialNLPPipeline] = None


def get_nlp_pipeline() -> FinancialNLPPipeline:
    """Return shared singleton FinancialNLPPipeline."""
    global _nlp_pipeline
    if _nlp_pipeline is None:
        _nlp_pipeline = FinancialNLPPipeline()
    return _nlp_pipeline
