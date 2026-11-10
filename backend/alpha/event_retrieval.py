"""Causal Historical Event Retrieval and Contextual Feature Engine."""

from datetime import datetime, timezone
import math
import re
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from backend.alpha.event_classifier import EventSubtype


class HistoricalEventRecord(BaseModel):
    """A historical financial event stored in the causal event store."""

    event_id: str
    instrument_id: str
    headline: str
    event_subtype: str
    timestamp: datetime
    materiality_score: float = 0.5
    surprise_category: str = "UNAVAILABLE_EXPECTATION"
    post_event_return_1d: Optional[float] = None
    post_event_return_5d: Optional[float] = None
    sector: Optional[str] = None


class RetrievedHistoricalMatch(BaseModel):
    """A matched historical event with similarity metrics."""

    event_id: str
    instrument_id: str
    headline: str
    event_subtype: str
    timestamp: datetime
    similarity_score: float = Field(..., ge=0.0, le=1.0)
    materiality_score: float
    post_event_return_1d: Optional[float] = None
    days_prior_to_decision: float


class EventRetrievalContext(BaseModel):
    """Contextual features derived from historical comparable events."""

    query_event_id: Optional[str] = None
    decision_timestamp: datetime
    matches: List[RetrievedHistoricalMatch] = Field(default_factory=list)
    total_comparable_count: int = 0
    mean_historical_return_1d: Optional[float] = None
    recency_days_latest: Optional[float] = None
    top_k: int = 5
    retrieval_method: str = "CAUSAL_TFIDF_HYBRID"


class CausalHistoricalEventRetriever:
    """
    Retrieves comparable historical financial events strictly prior to decision timestamp.
    Guarantees zero look-ahead bias by filtering t_historical < tau_decision before ranking.
    """

    def __init__(self, top_k: int = 5) -> None:
        self.top_k = top_k
        self._events: List[HistoricalEventRecord] = []

    def index_event(self, event: HistoricalEventRecord) -> None:
        """Add a historical event to the retrieval corpus."""
        self._events.append(event)

    def index_events(self, events: List[HistoricalEventRecord]) -> None:
        """Batch index historical events."""
        self._events.extend(events)

    def retrieve_comparable_events(
        self,
        headline: str,
        instrument_id: Optional[str],
        event_subtype: EventSubtype,
        materiality_score: float,
        decision_timestamp: datetime,
        query_event_id: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> EventRetrievalContext:
        """
        Retrieve top-k comparable events strictly prior to decision_timestamp.
        """
        k = top_k or self.top_k

        # 1. Causal Filter: Strictly t_historical < decision_timestamp
        causal_candidates = [
            e for e in self._events
            if e.timestamp < decision_timestamp and (query_event_id is None or e.event_id != query_event_id)
        ]

        if not causal_candidates:
            return EventRetrievalContext(
                query_event_id=query_event_id,
                decision_timestamp=decision_timestamp,
                matches=[],
                total_comparable_count=0,
                mean_historical_return_1d=None,
                recency_days_latest=None,
                top_k=k,
            )

        query_tokens = self._tokenize(headline)
        scored_matches: List[RetrievedHistoricalMatch] = []

        for candidate in causal_candidates:
            sim = self._compute_similarity(
                query_tokens=query_tokens,
                query_instrument=instrument_id,
                query_subtype=event_subtype.value,
                query_materiality=materiality_score,
                candidate=candidate,
            )

            # Calculate time difference in days
            time_delta = decision_timestamp - candidate.timestamp
            days_prior = max(0.0, time_delta.total_seconds() / 86400.0)

            scored_matches.append(
                RetrievedHistoricalMatch(
                    event_id=candidate.event_id,
                    instrument_id=candidate.instrument_id,
                    headline=candidate.headline,
                    event_subtype=candidate.event_subtype,
                    timestamp=candidate.timestamp,
                    similarity_score=round(sim, 3),
                    materiality_score=candidate.materiality_score,
                    post_event_return_1d=candidate.post_event_return_1d,
                    days_prior_to_decision=round(days_prior, 2),
                )
            )

        # Sort descending by similarity score
        scored_matches.sort(key=lambda m: m.similarity_score, reverse=True)
        top_matches = scored_matches[:k]

        # Calculate mean historical return if available
        returns = [m.post_event_return_1d for m in top_matches if m.post_event_return_1d is not None]
        mean_return = round(sum(returns) / len(returns), 4) if returns else None

        # Recency in days of the most recent matched event
        recency = min((m.days_prior_to_decision for m in top_matches), default=None)

        return EventRetrievalContext(
            query_event_id=query_event_id,
            decision_timestamp=decision_timestamp,
            matches=top_matches,
            total_comparable_count=len(causal_candidates),
            mean_historical_return_1d=mean_return,
            recency_days_latest=recency,
            top_k=k,
        )

    def _tokenize(self, text: str) -> Set[str]:
        """Simple alphanumeric tokenizer with stopword filtering."""
        words = re.findall(r"\b[a-zA-Z0-9]{3,}\b", text.lower())
        stopwords = {"the", "and", "for", "with", "from", "that", "this", "were", "been"}
        return set(words) - stopwords

    def _compute_similarity(
        self,
        query_tokens: Set[str],
        query_instrument: Optional[str],
        query_subtype: str,
        query_materiality: float,
        candidate: HistoricalEventRecord,
    ) -> float:
        """
        Hybrid similarity:
        - 0.35 Lexical Jaccard overlap
        - 0.30 Same event subtype match
        - 0.20 Same instrument match
        - 0.15 Materiality score proximity (1 - |m1 - m2|)
        """
        # Lexical Jaccard
        cand_tokens = self._tokenize(candidate.headline)
        union = query_tokens | cand_tokens
        jaccard = (len(query_tokens & cand_tokens) / len(union)) if union else 0.0

        # Subtype match
        subtype_match = 1.0 if candidate.event_subtype == query_subtype else 0.0

        # Instrument match
        inst_match = 1.0 if (query_instrument and candidate.instrument_id == query_instrument) else 0.0

        # Materiality proximity
        mat_prox = max(0.0, 1.0 - abs(query_materiality - candidate.materiality_score))

        score = (0.35 * jaccard) + (0.30 * subtype_match) + (0.20 * inst_match) + (0.15 * mat_prox)
        return min(1.0, max(0.0, score))


_retriever: Optional[CausalHistoricalEventRetriever] = None


def get_event_retriever() -> CausalHistoricalEventRetriever:
    """Return shared singleton CausalHistoricalEventRetriever."""
    global _retriever
    if _retriever is None:
        _retriever = CausalHistoricalEventRetriever()
    return _retriever
