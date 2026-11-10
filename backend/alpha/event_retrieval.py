"""Causal Historical Event Retrieval and Contextual Feature Engine with Outcome Maturity Protection."""

from datetime import datetime, timezone, timedelta
import json
import math
from pathlib import Path
import re
import sqlite3
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
    """A matched historical event with similarity metrics and matured outcome returns."""

    event_id: str
    instrument_id: str
    headline: str
    event_subtype: str
    timestamp: datetime
    similarity_score: float = Field(..., ge=0.0, le=1.0)
    materiality_score: float
    post_event_return_1d: Optional[float] = None
    post_event_return_5d: Optional[float] = None
    is_1d_outcome_matured: bool = False
    is_5d_outcome_matured: bool = False
    days_prior_to_decision: float


class EventRetrievalContext(BaseModel):
    """Contextual features derived from historical comparable events."""

    query_event_id: Optional[str] = None
    decision_timestamp: datetime
    matches: List[RetrievedHistoricalMatch] = Field(default_factory=list)
    total_comparable_count: int = 0
    matured_1d_count: int = 0
    matured_5d_count: int = 0
    mean_historical_return_1d: Optional[float] = None
    mean_historical_return_5d: Optional[float] = None
    recency_days_latest: Optional[float] = None
    top_k: int = 5
    retrieval_method: str = "CAUSAL_JACCARD_HYBRID"


class CausalHistoricalEventRetriever:
    """
    Retrieves comparable historical financial events strictly prior to decision timestamp.
    Guarantees zero look-ahead bias by:
    1. Pre-filtering t_historical < tau_decision before ranking.
    2. Enforcing outcome maturity: post_event_return_1d is only populated if t_historical + 1d <= tau_decision.
    3. Enforcing outcome maturity: post_event_return_5d is only populated if t_historical + 5d <= tau_decision.
    Supports persistent disk-backed storage (SQLite WAL) for recovery across process restarts.
    """

    def __init__(self, top_k: int = 5, db_path: Optional[str] = None) -> None:
        self.top_k = top_k
        self.db_path = db_path
        self._events: List[HistoricalEventRecord] = []
        if self.db_path:
            self._init_db()
            self.load_from_db()

    def _init_db(self) -> None:
        """Initialize SQLite persistence schema."""
        if not self.db_path:
            return
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS historical_event_records (
                    event_id TEXT PRIMARY KEY,
                    instrument_id TEXT NOT NULL,
                    headline TEXT NOT NULL,
                    event_subtype TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    materiality_score REAL NOT NULL,
                    surprise_category TEXT NOT NULL,
                    post_event_return_1d REAL,
                    post_event_return_5d REAL,
                    sector TEXT
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_ts ON historical_event_records(timestamp);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_inst ON historical_event_records(instrument_id);")

    def load_from_db(self) -> int:
        """Load historical events from persistent SQLite database."""
        if not self.db_path or not Path(self.db_path).exists():
            return 0
        loaded = 0
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM historical_event_records ORDER BY timestamp ASC;")
            for row in cursor.fetchall():
                rec = HistoricalEventRecord(
                    event_id=row["event_id"],
                    instrument_id=row["instrument_id"],
                    headline=row["headline"],
                    event_subtype=row["event_subtype"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    materiality_score=row["materiality_score"],
                    surprise_category=row["surprise_category"],
                    post_event_return_1d=row["post_event_return_1d"],
                    post_event_return_5d=row["post_event_return_5d"],
                    sector=row["sector"],
                )
                if not any(e.event_id == rec.event_id for e in self._events):
                    self._events.append(rec)
                    loaded += 1
        return loaded

    def save_event_to_db(self, event: HistoricalEventRecord) -> None:
        """Persist a single event record to SQLite idempotently."""
        if not self.db_path:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO historical_event_records (
                    event_id, instrument_id, headline, event_subtype, timestamp,
                    materiality_score, surprise_category, post_event_return_1d, post_event_return_5d, sector
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    materiality_score=excluded.materiality_score,
                    surprise_category=excluded.surprise_category,
                    post_event_return_1d=excluded.post_event_return_1d,
                    post_event_return_5d=excluded.post_event_return_5d;
                """,
                (
                    event.event_id,
                    event.instrument_id,
                    event.headline,
                    event.event_subtype,
                    event.timestamp.isoformat(),
                    event.materiality_score,
                    event.surprise_category,
                    event.post_event_return_1d,
                    event.post_event_return_5d,
                    event.sector,
                ),
            )

    def index_event(self, event: HistoricalEventRecord) -> None:
        """Add a historical event to the retrieval corpus with optional persistence."""
        if not any(e.event_id == event.event_id for e in self._events):
            self._events.append(event)
            if self.db_path:
                self.save_event_to_db(event)

    def index_events(self, events: List[HistoricalEventRecord]) -> None:
        """Batch index historical events."""
        for e in events:
            self.index_event(e)

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
        Rigidly enforces outcome maturity: future returns that have not fully matured
        by decision_timestamp are strictly excluded from results and statistical features.
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
                matured_1d_count=0,
                matured_5d_count=0,
                mean_historical_return_1d=None,
                mean_historical_return_5d=None,
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

            # Strict Outcome Maturity Protection
            # An event's 1-day return is only matured if t_candidate + 1 day <= decision_timestamp
            is_1d_matured = (candidate.timestamp + timedelta(days=1)) <= decision_timestamp
            matured_return_1d = candidate.post_event_return_1d if is_1d_matured else None

            # An event's 5-day return is only matured if t_candidate + 5 days <= decision_timestamp
            is_5d_matured = (candidate.timestamp + timedelta(days=5)) <= decision_timestamp
            matured_return_5d = candidate.post_event_return_5d if is_5d_matured else None

            scored_matches.append(
                RetrievedHistoricalMatch(
                    event_id=candidate.event_id,
                    instrument_id=candidate.instrument_id,
                    headline=candidate.headline,
                    event_subtype=candidate.event_subtype,
                    timestamp=candidate.timestamp,
                    similarity_score=round(sim, 3),
                    materiality_score=candidate.materiality_score,
                    post_event_return_1d=matured_return_1d,
                    post_event_return_5d=matured_return_5d,
                    is_1d_outcome_matured=is_1d_matured,
                    is_5d_outcome_matured=is_5d_matured,
                    days_prior_to_decision=round(days_prior, 2),
                )
            )

        # Sort descending by similarity score
        scored_matches.sort(key=lambda m: m.similarity_score, reverse=True)
        top_matches = scored_matches[:k]

        # Calculate mean historical return ONLY over fully matured outcomes
        matured_1d_returns = [m.post_event_return_1d for m in top_matches if m.post_event_return_1d is not None]
        mean_1d = round(sum(matured_1d_returns) / len(matured_1d_returns), 4) if matured_1d_returns else None

        matured_5d_returns = [m.post_event_return_5d for m in top_matches if m.post_event_return_5d is not None]
        mean_5d = round(sum(matured_5d_returns) / len(matured_5d_returns), 4) if matured_5d_returns else None

        # Recency in days of the most recent matched event
        recency = min((m.days_prior_to_decision for m in top_matches), default=None)

        return EventRetrievalContext(
            query_event_id=query_event_id,
            decision_timestamp=decision_timestamp,
            matches=top_matches,
            total_comparable_count=len(causal_candidates),
            matured_1d_count=len(matured_1d_returns),
            matured_5d_count=len(matured_5d_returns),
            mean_historical_return_1d=mean_1d,
            mean_historical_return_5d=mean_5d,
            recency_days_latest=recency,
            top_k=k,
            retrieval_method="CAUSAL_JACCARD_HYBRID",
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
        cand_tokens = self._tokenize(candidate.headline)
        union = query_tokens | cand_tokens
        jaccard = (len(query_tokens & cand_tokens) / len(union)) if union else 0.0

        subtype_match = 1.0 if candidate.event_subtype == query_subtype else 0.0
        inst_match = 1.0 if (query_instrument and candidate.instrument_id == query_instrument) else 0.0
        mat_prox = max(0.0, 1.0 - abs(query_materiality - candidate.materiality_score))

        score = (0.35 * jaccard) + (0.30 * subtype_match) + (0.20 * inst_match) + (0.15 * mat_prox)
        return min(1.0, max(0.0, score))


_retriever: Optional[CausalHistoricalEventRetriever] = None


def get_event_retriever(db_path: Optional[str] = None) -> CausalHistoricalEventRetriever:
    """Return shared singleton CausalHistoricalEventRetriever."""
    global _retriever
    if _retriever is None:
        _retriever = CausalHistoricalEventRetriever(db_path=db_path)
    return _retriever
