"""Causal Historical Event Retrieval, BM25 Lexical Baseline, Dense Vector Search & Hybrid Ranking.

Implements modular, multi-dimensional causal retrieval over historical financial events.
Compares:
1. Lexical Jaccard Overlap (`JACCARD`)
2. Causal Okapi BM25 Lexical Baseline (`BM25`)
3. Exact Dense Semantic Cosine Similarity (`DENSE_SEMANTIC`) via versioned CPU ONNX/Concept embeddings
4. Modular Hybrid Ranking (`HYBRID_MODULAR`) combining semantic, lexical, subtype, entity/instrument,
   numerical magnitude, surprise alignment, materiality proximity, and temporal decay.

Strictly enforces:
- Causal availability at `decision_timestamp` (`system_available_at_utc < decision_timestamp`).
- Outcome horizon maturity (`outcome_end_ts <= decision_timestamp`).
- Exclusion of duplicates, content hash collisions, and revisions of the query event.
- Embedding model version isolation.
"""

from datetime import datetime, timezone, timedelta
from enum import Enum
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple
import numpy as np
from pydantic import BaseModel, Field

from backend.alpha.event_classifier import EventSubtype
from backend.alpha.event_embeddings import (
    BaseEventEncoder,
    EventEmbeddingEngine,
    batch_cosine_similarity,
    format_event_for_embedding,
    get_embedding_engine,
)
from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.historical_store import (
    PersistentHistoricalEventStore,
    StoredHistoricalEvent,
    StoredOutcomeRecord,
)


class RetrievalMode(str, Enum):
    """Explicit retrieval scoring mode for benchmarking and ablations."""

    JACCARD = "JACCARD"
    BM25 = "BM25"
    DENSE_SEMANTIC = "DENSE_SEMANTIC"
    HYBRID_MODULAR = "HYBRID_MODULAR"


class RetrievalScoreBreakdown(BaseModel):
    """Transparent, decomposable multi-dimensional similarity components."""

    combined_score: float = Field(..., ge=0.0, le=1.0)
    lexical_jaccard: float = Field(0.0, ge=0.0, le=1.0)
    lexical_bm25_norm: float = Field(0.0, ge=0.0, le=1.0)
    semantic_cosine: float = Field(0.0, ge=0.0, le=1.0)
    event_subtype_match: float = Field(0.0, ge=0.0, le=1.0)
    instrument_match: float = Field(0.0, ge=0.0, le=1.0)
    numerical_similarity: float = Field(0.5, ge=0.0, le=1.0)
    surprise_alignment: float = Field(0.5, ge=0.0, le=1.0)
    materiality_proximity: float = Field(0.0, ge=0.0, le=1.0)
    temporal_proximity: float = Field(0.5, ge=0.0, le=1.0)
    sector_match: float = Field(0.0, ge=0.0, le=1.0)
    regime_match: float = Field(0.5, ge=0.0, le=1.0)


class HistoricalEventRecord(BaseModel):
    """A historical financial event record (Phase 3 backwards-compatible schema)."""

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
    body_snippet: Optional[str] = None
    event_family: Optional[str] = None
    primary_monetary_value: Optional[float] = None
    source_feed: str = "HISTORICAL_STORE"
    jurisdiction: MarketJurisdiction = MarketJurisdiction.IN


class RetrievedHistoricalMatch(BaseModel):
    """A matched historical event exposing transparent modular similarity and matured outcomes."""

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

    # Phase 4 Transparent Breakdown & Provenance Fields
    score_breakdown: Optional[RetrievalScoreBreakdown] = None
    lexical_similarity: float = 0.0
    semantic_similarity: float = 0.0
    event_subtype_match: float = 0.0
    instrument_match: float = 0.0
    numerical_similarity: float = 0.5
    materiality_proximity: float = 0.0
    matching_entities: List[str] = Field(default_factory=list)
    source_provenance: Optional[str] = None
    system_available_at_utc: Optional[datetime] = None
    available_outcome_horizons: List[str] = Field(default_factory=list)
    matured_outcomes: Dict[str, StoredOutcomeRecord] = Field(default_factory=dict)


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
    retrieval_method: str = "CAUSAL_HYBRID_MODULAR"
    embedding_model_version: Optional[str] = None


def tokenize_financial_text(text: str) -> List[str]:
    """Alphanumeric financial tokenizer removing noisy generic English stopwords."""
    words = re.findall(r"\b[a-zA-Z0-9]{2,}\b", text.lower())
    stopwords = {
        "the", "and", "for", "with", "from", "that", "this", "were", "been",
        "are", "was", "has", "have", "had", "into", "will", "its", "of", "in",
        "on", "at", "to", "by", "as", "an", "or",
    }
    return [w for w in words if w not in stopwords]


class CausalBM25Scorer:
    """
    Okapi BM25 scorer computed strictly over causally available historical candidates.
    No corpus-wide future statistics (IDF leakage) are ever used!
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b

    def score_candidates(self, query_tokens: List[str], doc_token_lists: List[List[str]]) -> List[float]:
        n_docs = len(doc_token_lists)
        if n_docs == 0 or not query_tokens:
            return [0.0] * n_docs

        avg_dl = sum(len(d) for d in doc_token_lists) / max(1, n_docs)
        if avg_dl < 1e-9:
            return [0.0] * n_docs

        # Compute document frequency within strictly causal candidates
        q_set = set(query_tokens)
        df: Dict[str, int] = {t: 0 for t in q_set}
        for d_tokens in doc_token_lists:
            d_set = set(d_tokens)
            for t in q_set:
                if t in d_set:
                    df[t] += 1

        raw_scores: List[float] = []
        for d_tokens in doc_token_lists:
            dl = len(d_tokens)
            tf_counts: Dict[str, int] = {}
            for t in d_tokens:
                tf_counts[t] = tf_counts.get(t, 0) + 1

            score = 0.0
            for t in query_tokens:
                if t not in tf_counts:
                    continue
                n_qi = df.get(t, 0)
                idf = math.log((n_docs - n_qi + 0.5) / (n_qi + 0.5) + 1.0)
                tf = tf_counts[t]
                denom = tf + self.k1 * (1.0 - self.b + self.b * (dl / avg_dl))
                score += idf * ((tf * (self.k1 + 1.0)) / max(1e-9, denom))
            raw_scores.append(score)

        # Normalize to [0.0, 1.0] via saturation scaling or max normalization
        max_s = max(raw_scores) if raw_scores else 0.0
        if max_s <= 1e-9:
            return [0.0] * n_docs
        return [min(1.0, max(0.0, s / max_s)) for s in raw_scores]


class CausalHistoricalEventRetriever:
    """
    Backwards-compatible Phase 3 + Phase 4 In-Memory / Light SQLite Historical Retriever.
    Now supports exact dense embeddings, BM25, Jaccard, and Modular Hybrid Ranking.
    """

    def __init__(
        self,
        top_k: int = 5,
        db_path: Optional[str] = None,
        embedding_engine: Optional[EventEmbeddingEngine] = None,
    ) -> None:
        self.top_k = top_k
        self.db_path = db_path
        self._events: List[HistoricalEventRecord] = []
        self._vectors: Dict[Tuple[str, str], np.ndarray] = {}
        self._bm25 = CausalBM25Scorer()
        self.embedding_engine = embedding_engine

        if self.db_path:
            self._init_db()
            self.load_from_db()

    def _init_db(self) -> None:
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
        if not any(e.event_id == event.event_id for e in self._events):
            self._events.append(event)
            if self.db_path:
                self.save_event_to_db(event)

    def index_events(self, events: List[HistoricalEventRecord]) -> None:
        for e in events:
            self.index_event(e)

    def _get_candidate_vectors(
        self, candidates: List[HistoricalEventRecord], encoder: BaseEventEncoder
    ) -> np.ndarray:
        """Fetch or encode unit vectors for candidate events scoped to encoder.model_version."""
        mver = encoder.model_version
        missing_idx: List[int] = []
        missing_texts: List[str] = []
        result_vecs: List[Optional[np.ndarray]] = [None] * len(candidates)

        for idx, c in enumerate(candidates):
            key = (c.event_id, mver)
            if key in self._vectors:
                result_vecs[idx] = self._vectors[key]
            else:
                missing_idx.append(idx)
                missing_texts.append(
                    format_event_for_embedding(
                        headline=c.headline,
                        body_snippet=c.body_snippet,
                        event_subtype=c.event_subtype,
                        primary_entity=c.instrument_id,
                    )
                )

        if missing_texts:
            encoded_mat = encoder.encode_batch(missing_texts)
            for list_pos, orig_idx in enumerate(missing_idx):
                v = encoded_mat[list_pos]
                c = candidates[orig_idx]
                self._vectors[(c.event_id, mver)] = v
                result_vecs[orig_idx] = v

        return np.vstack(result_vecs)

    def retrieve_comparable_events(
        self,
        headline: str,
        instrument_id: Optional[str],
        event_subtype: EventSubtype,
        materiality_score: float,
        decision_timestamp: datetime,
        query_event_id: Optional[str] = None,
        top_k: Optional[int] = None,
        mode: RetrievalMode = RetrievalMode.HYBRID_MODULAR,
        min_similarity: float = 0.0,
        instrument_filter: Optional[str] = None,
        subtype_filter: Optional[EventSubtype] = None,
    ) -> EventRetrievalContext:
        """
        Retrieve top-k comparable events strictly prior to decision_timestamp.
        """
        k = top_k or self.top_k
        subtype_val = event_subtype.value if isinstance(event_subtype, EventSubtype) else str(event_subtype)

        # 1. Causal Pre-Filter: Strictly t_historical < decision_timestamp + deduplication
        causal_candidates: List[HistoricalEventRecord] = []
        for e in self._events:
            if e.timestamp >= decision_timestamp:
                continue
            if query_event_id is not None and e.event_id == query_event_id:
                continue
            if instrument_filter and e.instrument_id.upper() != instrument_filter.upper():
                continue
            if subtype_filter and e.event_subtype != subtype_filter.value:
                continue
            causal_candidates.append(e)

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
                retrieval_method=f"CAUSAL_{mode.value}",
                embedding_model_version=(self.embedding_engine.active_model_version if self.embedding_engine else None),
            )

        query_tokens_list = tokenize_financial_text(headline)
        query_tokens_set = set(query_tokens_list)
        cand_token_lists = [tokenize_financial_text(c.headline) for c in causal_candidates]

        # Compute Causal BM25 baseline scores
        bm25_scores = self._bm25.score_candidates(query_tokens_list, cand_token_lists)

        # Compute Exact Dense Semantic Cosine similarities (if semantic or hybrid)
        sem_scores = np.zeros(len(causal_candidates), dtype=np.float32)
        active_model_ver: Optional[str] = None
        if mode in (RetrievalMode.DENSE_SEMANTIC, RetrievalMode.HYBRID_MODULAR):
            engine = self.embedding_engine or get_embedding_engine()
            encoder = engine.active_encoder
            active_model_ver = encoder.model_version
            q_text = format_event_for_embedding(
                headline=headline,
                event_subtype=subtype_val,
                primary_entity=instrument_id,
            )
            q_vec = encoder.encode_single(q_text)
            c_mat = self._get_candidate_vectors(causal_candidates, encoder)
            # Cosine sim in [-1, 1] -> rescale to [0, 1] for compatibility
            raw_cos = batch_cosine_similarity(q_vec, c_mat)
            sem_scores = np.clip((raw_cos + 1.0) / 2.0, 0.0, 1.0) if mode == RetrievalMode.HYBRID_MODULAR else np.clip(raw_cos, 0.0, 1.0)

        scored_matches: List[RetrievedHistoricalMatch] = []
        for idx, candidate in enumerate(causal_candidates):
            cand_set = set(cand_token_lists[idx])
            union = query_tokens_set | cand_set
            jaccard = (len(query_tokens_set & cand_set) / len(union)) if union else 0.0
            bm25_s = bm25_scores[idx]
            sem_s = float(sem_scores[idx])

            subtype_match = 1.0 if candidate.event_subtype == subtype_val else 0.0
            inst_match = 1.0 if (instrument_id and candidate.instrument_id.upper() == instrument_id.upper()) else 0.0
            mat_prox = max(0.0, 1.0 - abs(materiality_score - candidate.materiality_score))

            time_delta = decision_timestamp - candidate.timestamp
            days_prior = max(0.0, time_delta.total_seconds() / 86400.0)
            # Exponential recency decay (~1 year half-life: exp(-0.002 * days))
            temp_prox = math.exp(-0.002 * days_prior)

            if mode == RetrievalMode.JACCARD:
                combined = jaccard
            elif mode == RetrievalMode.BM25:
                combined = bm25_s
            elif mode == RetrievalMode.DENSE_SEMANTIC:
                combined = sem_s
            else:
                # HYBRID_MODULAR: Semantic + Lexical + Structured Economics
                combined = (
                    0.30 * sem_s
                    + 0.15 * bm25_s
                    + 0.05 * jaccard
                    + 0.20 * subtype_match
                    + 0.15 * inst_match
                    + 0.10 * mat_prox
                    + 0.05 * temp_prox
                )
            combined = min(1.0, max(0.0, combined))
            if combined < min_similarity:
                continue

            breakdown = RetrievalScoreBreakdown(
                combined_score=round(combined, 4),
                lexical_jaccard=round(jaccard, 4),
                lexical_bm25_norm=round(bm25_s, 4),
                semantic_cosine=round(sem_s, 4),
                event_subtype_match=subtype_match,
                instrument_match=inst_match,
                materiality_proximity=round(mat_prox, 4),
                temporal_proximity=round(temp_prox, 4),
            )

            is_1d_matured = (candidate.timestamp + timedelta(days=1)) <= decision_timestamp
            matured_return_1d = candidate.post_event_return_1d if is_1d_matured else None

            is_5d_matured = (candidate.timestamp + timedelta(days=5)) <= decision_timestamp
            matured_return_5d = candidate.post_event_return_5d if is_5d_matured else None

            horizons: List[str] = []
            if is_1d_matured and matured_return_1d is not None:
                horizons.append("1d")
            if is_5d_matured and matured_return_5d is not None:
                horizons.append("5d")

            matching_ents = [candidate.instrument_id] if inst_match > 0 else []

            scored_matches.append(
                RetrievedHistoricalMatch(
                    event_id=candidate.event_id,
                    instrument_id=candidate.instrument_id,
                    headline=candidate.headline,
                    event_subtype=candidate.event_subtype,
                    timestamp=candidate.timestamp,
                    similarity_score=round(combined, 4),
                    materiality_score=candidate.materiality_score,
                    post_event_return_1d=matured_return_1d,
                    post_event_return_5d=matured_return_5d,
                    is_1d_outcome_matured=is_1d_matured,
                    is_5d_outcome_matured=is_5d_matured,
                    days_prior_to_decision=round(days_prior, 2),
                    score_breakdown=breakdown,
                    lexical_similarity=round(max(bm25_s, jaccard), 4),
                    semantic_similarity=round(sem_s, 4),
                    event_subtype_match=subtype_match,
                    instrument_match=inst_match,
                    materiality_proximity=round(mat_prox, 4),
                    matching_entities=matching_ents,
                    source_provenance=candidate.source_feed,
                    system_available_at_utc=candidate.timestamp,
                    available_outcome_horizons=horizons,
                )
            )

        scored_matches.sort(key=lambda m: m.similarity_score, reverse=True)
        top_matches = scored_matches[:k]

        matured_1d_returns = [m.post_event_return_1d for m in top_matches if m.post_event_return_1d is not None]
        mean_1d = round(sum(matured_1d_returns) / len(matured_1d_returns), 4) if matured_1d_returns else None

        matured_5d_returns = [m.post_event_return_5d for m in top_matches if m.post_event_return_5d is not None]
        mean_5d = round(sum(matured_5d_returns) / len(matured_5d_returns), 4) if matured_5d_returns else None

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
            retrieval_method=f"CAUSAL_{mode.value}",
            embedding_model_version=active_model_ver,
        )

    def _tokenize(self, text: str) -> Set[str]:
        return set(tokenize_financial_text(text))

    def _compute_similarity(
        self,
        query_tokens: Set[str],
        query_instrument: Optional[str],
        query_subtype: str,
        query_materiality: float,
        candidate: HistoricalEventRecord,
    ) -> float:
        cand_tokens = self._tokenize(candidate.headline)
        union = query_tokens | cand_tokens
        jaccard = (len(query_tokens & cand_tokens) / len(union)) if union else 0.0
        subtype_match = 1.0 if candidate.event_subtype == query_subtype else 0.0
        inst_match = 1.0 if (query_instrument and candidate.instrument_id == query_instrument) else 0.0
        mat_prox = max(0.0, 1.0 - abs(query_materiality - candidate.materiality_score))
        score = (0.35 * jaccard) + (0.30 * subtype_match) + (0.20 * inst_match) + (0.15 * mat_prox)
        return min(1.0, max(0.0, score))


class PersistentVectorRetriever:
    """
    Production Phase 4 Semantic Vector & Modular Hybrid Retriever operating directly
    over `PersistentHistoricalEventStore`, supporting:
    - Jurisdiction separation (`IN` vs `US`).
    - Exact cosine similarity on versioned embeddings cached in SQLite BLOBs.
    - Numerical magnitude similarity and surprise direction alignment.
    - Reporting period matching and temporal decay.
    - Causal outcome fetching with `outcome_end_ts <= decision_timestamp`.
    """

    def __init__(
        self,
        store: PersistentHistoricalEventStore,
        embedding_engine: Optional[EventEmbeddingEngine] = None,
    ) -> None:
        self.store = store
        self.embedding_engine = embedding_engine or get_embedding_engine()
        self._bm25 = CausalBM25Scorer()

    def ensure_event_embedded(self, event: StoredHistoricalEvent, model_version: Optional[str] = None) -> List[float]:
        """Compute and persist embedding if not already indexed for this model_version."""
        encoder = self.embedding_engine.active_encoder
        if model_version and model_version != encoder.model_version:
            if model_version == self.embedding_engine.fallback_encoder.model_version:
                encoder = self.embedding_engine.fallback_encoder

        target_ver = encoder.model_version
        existing = self.store.get_embedding(event.event_id, target_ver)
        if existing is not None:
            return existing

        text = format_event_for_embedding(
            headline=event.headline,
            body_snippet=event.body_snippet,
            event_subtype=event.event_subtype,
            primary_entity=event.primary_entity_id,
        )
        vec = encoder.encode_single(text)
        self.store.save_embedding(event.event_id, target_ver, vec)
        return vec

    @staticmethod
    def _extract_primary_magnitude(metrics: List[Dict[str, Any]]) -> Optional[float]:
        for m in metrics:
            val = m.get("normalized_value")
            if isinstance(val, (int, float)) and abs(val) > 0:
                return float(abs(val))
        return None

    @staticmethod
    def _compute_numerical_similarity(mag_a: Optional[float], mag_b: Optional[float]) -> float:
        if mag_a is None or mag_b is None:
            return 0.5  # neutral when unquantified
        la = math.log10(max(1.0, abs(mag_a)))
        lb = math.log10(max(1.0, abs(mag_b)))
        # 3 orders of magnitude apart = 0.0 similarity
        return max(0.0, 1.0 - abs(la - lb) / 3.0)

    def retrieve(
        self,
        query_headline: str,
        decision_timestamp: datetime,
        query_event_id: Optional[str] = None,
        query_content_hash: Optional[str] = None,
        query_body_snippet: Optional[str] = None,
        query_subtype: Optional[str] = None,
        query_family: Optional[str] = None,
        query_entity_ids: Optional[List[str]] = None,
        query_instrument_ids: Optional[List[str]] = None,
        query_materiality: float = 0.5,
        query_primary_magnitude: Optional[float] = None,
        query_surprise_category: Optional[str] = None,
        query_sector: Optional[str] = None,
        jurisdiction: Optional[MarketJurisdiction] = None,
        instrument_filter: Optional[str] = None,
        subtype_filter: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        top_k: int = 10,
        min_similarity: float = 0.0,
        mode: RetrievalMode = RetrievalMode.HYBRID_MODULAR,
        include_synthetic: bool = False,
        market_regime_volatility: Optional[str] = None,
    ) -> EventRetrievalContext:
        """
        Execute multi-stage causal retrieval over persisted historical events.
        """
        exclude_ids = {query_event_id} if query_event_id else set()
        exclude_hashes = {query_content_hash} if query_content_hash else set()

        # 1. SQL-level Causal & Metadata Pre-filter
        candidates = self.store.query_events(
            decision_timestamp=decision_timestamp,
            instrument_id=instrument_filter,
            event_subtype=subtype_filter,
            jurisdiction=jurisdiction,
            start_time=start_time,
            end_time=end_time,
            exclude_event_ids=exclude_ids,
            exclude_content_hashes=exclude_hashes,
            include_synthetic=include_synthetic,
            limit=500,
        )

        encoder = self.embedding_engine.active_encoder
        active_ver = encoder.model_version

        if not candidates:
            return EventRetrievalContext(
                query_event_id=query_event_id,
                decision_timestamp=decision_timestamp,
                matches=[],
                total_comparable_count=0,
                top_k=top_k,
                retrieval_method=f"PERSISTENT_{mode.value}",
                embedding_model_version=active_ver,
            )

        # 2. Fetch or lazily compute embeddings for candidates strictly scoped by model_version
        cand_ids = [c.event_id for c in candidates]
        cached_embs = self.store.get_embeddings_batch(cand_ids, model_version=active_ver)

        missing_cands: List[StoredHistoricalEvent] = []
        missing_texts: List[str] = []
        for c in candidates:
            if c.event_id not in cached_embs:
                missing_cands.append(c)
                missing_texts.append(
                    format_event_for_embedding(
                        headline=c.headline,
                        body_snippet=c.body_snippet,
                        event_subtype=c.event_subtype,
                        primary_entity=c.primary_entity_id,
                    )
                )

        if missing_texts:
            new_mat = encoder.encode_batch(missing_texts)
            for idx, c in enumerate(missing_cands):
                vec = new_mat[idx].tolist()
                cached_embs[c.event_id] = vec
                self.store.save_embedding(c.event_id, active_ver, vec)

        # Build matrix [N, D] aligned with candidates
        emb_matrix = np.array([cached_embs[c.event_id] for c in candidates], dtype=np.float32)

        # Encode query text
        q_text = format_event_for_embedding(
            headline=query_headline,
            body_snippet=query_body_snippet,
            event_subtype=query_subtype,
            primary_entity=(query_entity_ids[0] if query_entity_ids else None),
        )
        q_vec = encoder.encode_single(q_text)
        raw_cos = batch_cosine_similarity(q_vec, emb_matrix)
        sem_scores = np.clip((raw_cos + 1.0) / 2.0, 0.0, 1.0) if mode == RetrievalMode.HYBRID_MODULAR else np.clip(raw_cos, 0.0, 1.0)

        # 3. Lexical Tokenization & Causal BM25
        q_tokens = tokenize_financial_text(f"{query_headline} {query_body_snippet or ''}")
        q_token_set = set(q_tokens)
        cand_token_lists = [tokenize_financial_text(f"{c.headline} {c.body_snippet or ''}") for c in candidates]
        bm25_scores = self._bm25.score_candidates(q_tokens, cand_token_lists)

        # 4. Fetch Strictly Matured Outcomes for candidates
        matured_outcomes_map = self.store.get_matured_outcomes(cand_ids, decision_timestamp=decision_timestamp)

        q_inst_set = {i.upper() for i in (query_instrument_ids or [])}
        q_ent_set = {e.upper() for e in (query_entity_ids or [])}

        scored_matches: List[RetrievedHistoricalMatch] = []
        for idx, c in enumerate(candidates):
            c_tok_set = set(cand_token_lists[idx])
            union = q_token_set | c_tok_set
            jaccard = (len(q_token_set & c_tok_set) / len(union)) if union else 0.0
            bm25_s = bm25_scores[idx]
            sem_s = float(sem_scores[idx])

            # Subtype / family match
            if query_subtype and c.event_subtype == query_subtype:
                subtype_match = 1.0
            elif query_family and c.event_family == query_family:
                subtype_match = 0.5
            else:
                subtype_match = 0.0

            # Multi-entity & Instrument overlap
            c_inst_set = {i.upper() for i in c.instrument_ids}
            shared_insts = sorted(q_inst_set & c_inst_set)
            c_ent_ids = {e.get("entity_id", "").upper() for e in c.resolved_entities if e.get("entity_id")}
            if c.primary_entity_id:
                c_ent_ids.add(c.primary_entity_id.upper())
            shared_ents = sorted(q_ent_set & c_ent_ids)

            if shared_insts or shared_ents:
                inst_match = 1.0
            elif query_sector and c.sector and query_sector.upper() == c.sector.upper():
                inst_match = 0.4
            else:
                inst_match = 0.0

            sector_match = 1.0 if (query_sector and c.sector and query_sector.upper() == c.sector.upper()) else 0.0

            # Numerical similarity
            c_mag = self._extract_primary_magnitude(c.extracted_metrics)
            num_sim = self._compute_numerical_similarity(query_primary_magnitude, c_mag)

            # Surprise direction match
            c_surp_cat = c.surprise.get("surprise_category", "UNAVAILABLE_EXPECTATION")
            if query_surprise_category and query_surprise_category != "UNAVAILABLE_EXPECTATION" and c_surp_cat != "UNAVAILABLE_EXPECTATION":
                surprise_align = 1.0 if query_surprise_category == c_surp_cat else 0.1
            else:
                surprise_align = 0.5

            # Materiality proximity
            mat_prox = max(0.0, 1.0 - abs(query_materiality - c.materiality_score))

            # Temporal proximity (exponential decay, ~365 day scale)
            days_prior = max(0.0, (decision_timestamp - c.system_available_at_utc).total_seconds() / 86400.0)
            temp_prox = math.exp(-0.002 * days_prior)

            if mode == RetrievalMode.JACCARD:
                combined = jaccard
            elif mode == RetrievalMode.BM25:
                combined = bm25_s
            elif mode == RetrievalMode.DENSE_SEMANTIC:
                combined = sem_s
            else:
                combined = (
                    0.28 * sem_s
                    + 0.12 * bm25_s
                    + 0.05 * jaccard
                    + 0.18 * subtype_match
                    + 0.14 * inst_match
                    + 0.08 * num_sim
                    + 0.05 * surprise_align
                    + 0.06 * mat_prox
                    + 0.04 * temp_prox
                )
            combined = min(1.0, max(0.0, combined))
            if combined < min_similarity:
                continue

            breakdown = RetrievalScoreBreakdown(
                combined_score=round(combined, 4),
                lexical_jaccard=round(jaccard, 4),
                lexical_bm25_norm=round(bm25_s, 4),
                semantic_cosine=round(sem_s, 4),
                event_subtype_match=subtype_match,
                instrument_match=inst_match,
                numerical_similarity=round(num_sim, 4),
                surprise_alignment=round(surprise_align, 4),
                materiality_proximity=round(mat_prox, 4),
                temporal_proximity=round(temp_prox, 4),
                sector_match=sector_match,
            )

            # Inspect matured outcomes
            cand_outcomes = matured_outcomes_map.get(c.event_id, [])
            outcomes_by_h: Dict[str, StoredOutcomeRecord] = {o.horizon_label: o for o in cand_outcomes}
            ret_1d = outcomes_by_h["1d"].return_simple if "1d" in outcomes_by_h else None
            ret_5d = outcomes_by_h["5d"].return_simple if "5d" in outcomes_by_h else None

            scored_matches.append(
                RetrievedHistoricalMatch(
                    event_id=c.event_id,
                    instrument_id=(c.instrument_ids[0] if c.instrument_ids else (c.primary_entity_id or "UNKNOWN")),
                    headline=c.headline,
                    event_subtype=c.event_subtype,
                    timestamp=c.system_available_at_utc,
                    similarity_score=round(combined, 4),
                    materiality_score=c.materiality_score,
                    post_event_return_1d=ret_1d,
                    post_event_return_5d=ret_5d,
                    is_1d_outcome_matured=("1d" in outcomes_by_h),
                    is_5d_outcome_matured=("5d" in outcomes_by_h),
                    days_prior_to_decision=round(days_prior, 2),
                    score_breakdown=breakdown,
                    lexical_similarity=round(max(bm25_s, jaccard), 4),
                    semantic_similarity=round(sem_s, 4),
                    event_subtype_match=subtype_match,
                    instrument_match=inst_match,
                    numerical_similarity=round(num_sim, 4),
                    materiality_proximity=round(mat_prox, 4),
                    matching_entities=shared_ents or shared_insts,
                    source_provenance=c.source_feed,
                    system_available_at_utc=c.system_available_at_utc,
                    available_outcome_horizons=sorted(outcomes_by_h.keys()),
                    matured_outcomes=outcomes_by_h,
                )
            )

        scored_matches.sort(key=lambda m: m.similarity_score, reverse=True)
        top_matches = scored_matches[:top_k]

        m1 = [m.post_event_return_1d for m in top_matches if m.post_event_return_1d is not None]
        m5 = [m.post_event_return_5d for m in top_matches if m.post_event_return_5d is not None]
        mean_1d = round(sum(m1) / len(m1), 4) if m1 else None
        mean_5d = round(sum(m5) / len(m5), 4) if m5 else None
        recency = min((m.days_prior_to_decision for m in top_matches), default=None)

        return EventRetrievalContext(
            query_event_id=query_event_id,
            decision_timestamp=decision_timestamp,
            matches=top_matches,
            total_comparable_count=len(candidates),
            matured_1d_count=len(m1),
            matured_5d_count=len(m5),
            mean_historical_return_1d=mean_1d,
            mean_historical_return_5d=mean_5d,
            recency_days_latest=recency,
            top_k=top_k,
            retrieval_method=f"PERSISTENT_{mode.value}",
            embedding_model_version=active_ver,
        )


_retriever: Optional[CausalHistoricalEventRetriever] = None


def get_event_retriever(db_path: Optional[str] = None) -> CausalHistoricalEventRetriever:
    """Return shared singleton CausalHistoricalEventRetriever."""
    global _retriever
    if _retriever is None:
        _retriever = CausalHistoricalEventRetriever(db_path=db_path)
    return _retriever
