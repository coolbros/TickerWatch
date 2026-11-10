"""Unified Financial NLP Pipeline and Representation Schema (v1.1.0)."""

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
from backend.alpha.instruments import InstrumentMappingRecord
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


class TimestampProvenance(BaseModel):
    published_at_utc: Optional[datetime] = None
    provider_observed_at_utc: Optional[datetime] = None
    local_received_at_utc: datetime
    ingestion_completed_at_utc: Optional[datetime] = None
    processed_at_utc: datetime
    decision_timestamp: datetime


class FinancialEventRepresentation(BaseModel):
    """
    Canonical, versioned financial event representation (v1.1.0).
    Transforms unstructured news/disclosures into structured, quantified, multi-entity,
    instrument-linked events for downstream temporal alignment and event-conditioned predictive modeling.
    """

    event_id: str
    headline: str
    body_snippet: Optional[str] = None
    source_name: str

    # Causal Timestamp Discipline (A1 Remediation)
    # Operative actionable timestamp is strictly local_received_at_utc
    decision_timestamp: datetime = Field(..., description="Actionable system receipt timestamp (local_received_at_utc)")
    published_at_utc: Optional[datetime] = Field(None, description="Reported publication time in UTC")
    provider_observed_at_utc: Optional[datetime] = Field(None, description="Provider observation time in UTC")
    local_received_at_utc: datetime = Field(..., description="Local network socket ingress time in UTC")
    ingestion_completed_at_utc: Optional[datetime] = None
    processed_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Multi-Entity Resolution (A4 Remediation)
    primary_entity: ResolvedEntity
    secondary_entities: List[ResolvedEntity] = Field(default_factory=list)
    all_entities: List[ResolvedEntity] = Field(default_factory=list)
    all_instrument_mappings: List[InstrumentMappingRecord] = Field(default_factory=list)
    entity_resolution: ResolvedEntity  # Backwards compatibility alias for primary_entity

    # NLP Sub-components
    classification: EventClassificationResult
    extraction: FinancialExtractionResult
    surprise: NumericalSurpriseResult
    materiality: MaterialityAssessment
    historical_context: EventRetrievalContext

    # Calibrated uncertainty and scoring
    composite_confidence: float = Field(..., ge=0.0, le=1.0)

    # Provenance and telemetry
    pipeline_version: str = "1.1.0-alpha"
    execution_latency_us: int = Field(default=0, description="End-to-end pipeline execution time in microseconds")

    @property
    def timestamp_provenance(self) -> TimestampProvenance:
        """Structured timestamp provenance for audit and replay reproducibility."""
        return TimestampProvenance(
            published_at_utc=self.published_at_utc,
            provider_observed_at_utc=self.provider_observed_at_utc,
            local_received_at_utc=self.local_received_at_utc,
            ingestion_completed_at_utc=self.ingestion_completed_at_utc,
            processed_at_utc=self.processed_at_utc,
            decision_timestamp=self.decision_timestamp,
        )

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
        """
        Process canonical Phase 2 NewsEvent through the full financial NLP pipeline.
        Enforces A1: The actionable decision timestamp is strictly local_received_at_utc.
        """
        decision_ts = news_event.local_received_at_utc
        return self.process_raw_text(
            headline=news_event.headline,
            body_text=news_event.body_text,
            decision_timestamp=decision_ts,
            published_at=news_event.published_at_utc,
            provider_observed_at=news_event.provider_observed_at_utc,
            local_received_at=news_event.local_received_at_utc,
            ingestion_completed_at=news_event.ingestion_completed_at_utc,
            event_id=news_event.event_id,
            source_name=news_event.source_name,
            is_first_announcement=not news_event.is_correction,
            existing_mappings=news_event.instrument_mappings,
        )

    def process_raw_text(
        self,
        headline: str,
        body_text: Optional[str] = None,
        decision_timestamp: Optional[datetime] = None,
        published_at: Optional[datetime] = None,
        provider_observed_at: Optional[datetime] = None,
        local_received_at: Optional[datetime] = None,
        ingestion_completed_at: Optional[datetime] = None,
        event_id: Optional[str] = None,
        source_name: str = "DIRECT_INGRESS",
        is_first_announcement: bool = True,
        existing_mappings: Optional[List[InstrumentMappingRecord]] = None,
    ) -> FinancialEventRepresentation:
        """Process arbitrary financial headline and body text."""
        start_ns = time.perf_counter_ns()
        # A1 Remediation: Decision timestamp defaults to local_received_at or current UTC
        recv_ts = local_received_at or decision_timestamp or datetime.now(timezone.utc)
        decision_ts = recv_ts
        evt_id = event_id or f"nlp_evt_{int(time.time() * 1000)}"

        # 1. Multi-Entity Financial Resolution & Disambiguation (A4 Remediation)
        full_text = f"{headline} {body_text or ''}".strip()
        resolved_entities = self.entity_resolver.resolve_text(full_text)
        primary_entity = resolved_entities[0] if resolved_entities else ResolvedEntity(
            matched_text=headline[:50],
            resolution_state=ResolutionState.UNRESOLVED,
            confidence=0.0,
        )
        secondary_entities = resolved_entities[1:] if len(resolved_entities) > 1 else []

        # Combine instrument mappings across all resolved entities plus existing mappings
        combined_mappings: List[InstrumentMappingRecord] = []
        seen_inst_keys = set()

        if existing_mappings:
            for em in existing_mappings:
                key = (em.instrument_id, em.relevance.value)
                if key not in seen_inst_keys:
                    combined_mappings.append(em)
                    seen_inst_keys.add(key)

        for ent in resolved_entities:
            for im in ent.instruments:
                key = (im.instrument_id, im.relevance.value)
                if key not in seen_inst_keys:
                    combined_mappings.append(im)
                    seen_inst_keys.add(key)

        instrument_id = primary_entity.parent_entity_id or primary_entity.entity_id

        # 2. Hierarchical Event Classification
        classification = self.event_classifier.classify_text(headline, body_text)

        # 3. Full-Document Structured Numerical Extraction (A3 Remediation)
        extraction = self.event_extractor.extract_metrics(headline, body_text=body_text)

        # 4. Point-in-Time Numerical Surprise Detection (A5 Remediation)
        surprise = self.surprise_engine.compute_surprise(
            metric=extraction.primary_metric,
            instrument_id=instrument_id,
            event_timestamp=decision_ts,
        )

        # 5. Composite Materiality Evaluation
        index_names = {
            "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "LT", "SBIN",
            "BHARTIARTL", "ITC", "KOTAKBANK", "TATAMOTORS", "AAPL", "AMZN", "MSFT", "GOOGL", "META"
        }
        is_index_constituent = bool(instrument_id in index_names)
        materiality = self.materiality_engine.evaluate_materiality(
            classification=classification,
            extraction=extraction,
            surprise=surprise,
            is_first_announcement=is_first_announcement,
            is_index_constituent=is_index_constituent,
        )

        # 6. Causal Historical Event Retrieval with Outcome Maturity Protection (A2 Remediation)
        historical_context = self.historical_retriever.retrieve_comparable_events(
            headline=headline,
            instrument_id=instrument_id,
            event_subtype=classification.primary_subtype,
            materiality_score=materiality.composite_materiality_score,
            decision_timestamp=decision_ts,
            query_event_id=evt_id,
        )

        # 7. Calibrated Composite Confidence Estimation
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
            decision_timestamp=decision_ts,
            published_at_utc=published_at,
            provider_observed_at_utc=provider_observed_at,
            local_received_at_utc=recv_ts,
            ingestion_completed_at_utc=ingestion_completed_at,
            primary_entity=primary_entity,
            secondary_entities=secondary_entities,
            all_entities=resolved_entities,
            all_instrument_mappings=combined_mappings,
            entity_resolution=primary_entity,  # Backwards compatibility
            classification=classification,
            extraction=extraction,
            surprise=surprise,
            materiality=materiality,
            historical_context=historical_context,
            composite_confidence=composite_conf,
            pipeline_version="1.1.0-alpha",
            execution_latency_us=elapsed_us,
        )


_nlp_pipeline: Optional[FinancialNLPPipeline] = None


def get_nlp_pipeline() -> FinancialNLPPipeline:
    """Return shared singleton FinancialNLPPipeline."""
    global _nlp_pipeline
    if _nlp_pipeline is None:
        _nlp_pipeline = FinancialNLPPipeline()
    return _nlp_pipeline
