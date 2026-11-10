"""Persistent Local-First Historical Event Store Engine.

Provides durable SQLite WAL storage separating:
1. Event metadata & structured NLP representations (`historical_events`).
2. Many-to-many instrument associations (`historical_event_instruments`).
3. Append-only revision history (`historical_event_revisions`).
4. Versioned dense semantic vector embeddings (`historical_event_embeddings`).
5. Strictly separated post-event market outcomes (`historical_event_outcomes`).

All queries enforce causal availability at `decision_timestamp` and outcome maturity
provenance (`outcome_end_ts <= decision_timestamp`).
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple, Union
from pydantic import BaseModel, Field

from backend.alpha.historical_import import MarketJurisdiction
from backend.alpha.timestamps import parse_utc_iso, to_utc_iso

if TYPE_CHECKING:
    from backend.alpha.nlp_pipeline import FinancialEventRepresentation


class StoredHistoricalEvent(BaseModel):
    """Hydrated canonical historical event record retrieved from persistent storage."""

    event_id: str
    source_article_id: Optional[str] = None
    revision_number: int = 1
    content_hash: str
    jurisdiction: MarketJurisdiction
    source_feed: str
    source_url: Optional[str] = None
    headline: str
    body_snippet: Optional[str] = None
    primary_entity_id: Optional[str] = None
    resolved_entities: List[Dict[str, Any]] = Field(default_factory=list)
    instrument_mappings: List[Dict[str, Any]] = Field(default_factory=list)
    instrument_ids: List[str] = Field(default_factory=list)
    event_family: str
    event_subtype: str
    extracted_metrics: List[Dict[str, Any]] = Field(default_factory=list)
    surprise: Dict[str, Any] = Field(default_factory=dict)
    materiality_score: float
    materiality_tier: str
    composite_confidence: float
    sector: Optional[str] = None
    event_timestamp_utc: datetime
    system_available_at_utc: datetime
    processed_at_utc: datetime
    schema_version: str
    pipeline_version: str
    is_synthetic: bool = False


class StoredOutcomeRecord(BaseModel):
    """Strictly separated future market response record with provenance and maturity timestamp."""

    event_id: str
    instrument_id: str
    horizon_label: str  # e.g., "1m", "5m", "30m", "1h", "1d", "5d"
    outcome_start_ts: datetime
    outcome_end_ts: datetime
    return_simple: Optional[float] = None
    return_log: Optional[float] = None
    benchmark_adjusted_return: Optional[float] = None
    benchmark_instrument: Optional[str] = None
    realized_volatility: Optional[float] = None
    max_favorable_excursion: Optional[float] = None
    max_adverse_excursion: Optional[float] = None
    spread_bps_avg: Optional[float] = None
    data_quality_status: str = "VERIFIED"
    market_data_source: str = "LOCAL_BARS"
    created_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PersistentHistoricalEventStore:
    """
    SQLite WAL-backed local-first store for TickerWatch Alpha historical intelligence.
    Enforces idempotent ingestion, revision tracking, duplicate prevention,
    strict causal availability filtering, and embedding/outcome separation.
    """

    SCHEMA_VERSION = "4.0.0"

    def __init__(self, db_path: Union[str, Path] = "data/alpha_historical_store.db") -> None:
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn_str = str(self.db_path)
        # Keep persistent connection only for :memory: DBs; otherwise open per-transaction
        self._memory_conn: Optional[sqlite3.Connection] = None
        if self._conn_str == ":memory:":
            self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        if self._memory_conn is not None:
            return self._memory_conn
        conn = sqlite3.connect(self._conn_str, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=10000;")
        return conn

    def _close_connection(self, conn: sqlite3.Connection) -> None:
        if self._memory_conn is None:
            conn.close()

    def _init_schema(self) -> None:
        conn = self._get_connection()
        try:
            with conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS historical_events (
                        event_id TEXT PRIMARY KEY,
                        source_article_id TEXT,
                        revision_number INTEGER NOT NULL DEFAULT 1,
                        content_hash TEXT NOT NULL,
                        jurisdiction TEXT NOT NULL,
                        source_feed TEXT NOT NULL,
                        source_url TEXT,
                        headline TEXT NOT NULL,
                        body_snippet TEXT,
                        primary_entity_id TEXT,
                        resolved_entities_json TEXT NOT NULL,
                        instrument_mappings_json TEXT NOT NULL,
                        event_family TEXT NOT NULL,
                        event_subtype TEXT NOT NULL,
                        extracted_metrics_json TEXT NOT NULL,
                        surprise_json TEXT NOT NULL,
                        materiality_score REAL NOT NULL,
                        materiality_tier TEXT NOT NULL,
                        composite_confidence REAL NOT NULL,
                        sector TEXT,
                        event_timestamp_utc TEXT NOT NULL,
                        system_available_at_utc TEXT NOT NULL,
                        processed_at_utc TEXT NOT NULL,
                        schema_version TEXT NOT NULL,
                        pipeline_version TEXT NOT NULL,
                        is_synthetic INTEGER NOT NULL DEFAULT 0,
                        indexed_at_utc TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_hist_avail_ts ON historical_events(system_available_at_utc);
                    CREATE INDEX IF NOT EXISTS idx_hist_subtype ON historical_events(event_subtype);
                    CREATE INDEX IF NOT EXISTS idx_hist_jurisdiction ON historical_events(jurisdiction);
                    CREATE INDEX IF NOT EXISTS idx_hist_content_hash ON historical_events(content_hash);
                    CREATE INDEX IF NOT EXISTS idx_hist_primary_ent ON historical_events(primary_entity_id);

                    CREATE TABLE IF NOT EXISTS historical_event_instruments (
                        event_id TEXT NOT NULL,
                        instrument_id TEXT NOT NULL,
                        relevance_tier TEXT NOT NULL DEFAULT 'DIRECT',
                        PRIMARY KEY (event_id, instrument_id),
                        FOREIGN KEY (event_id) REFERENCES historical_events(event_id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_hist_inst_lookup ON historical_event_instruments(instrument_id);

                    CREATE TABLE IF NOT EXISTS historical_event_revisions (
                        revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT NOT NULL,
                        revision_number INTEGER NOT NULL,
                        content_hash TEXT NOT NULL,
                        headline TEXT NOT NULL,
                        body_snippet TEXT,
                        system_available_at_utc TEXT NOT NULL,
                        recorded_at_utc TEXT NOT NULL,
                        FOREIGN KEY (event_id) REFERENCES historical_events(event_id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS historical_event_embeddings (
                        event_id TEXT NOT NULL,
                        model_version TEXT NOT NULL,
                        embedding_dim INTEGER NOT NULL,
                        vector_blob BLOB NOT NULL,
                        created_at_utc TEXT NOT NULL,
                        PRIMARY KEY (event_id, model_version),
                        FOREIGN KEY (event_id) REFERENCES historical_events(event_id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS historical_event_outcomes (
                        event_id TEXT NOT NULL,
                        instrument_id TEXT NOT NULL,
                        horizon_label TEXT NOT NULL,
                        outcome_start_ts TEXT NOT NULL,
                        outcome_end_ts TEXT NOT NULL,
                        return_simple REAL,
                        return_log REAL,
                        benchmark_adjusted_return REAL,
                        benchmark_instrument TEXT,
                        realized_volatility REAL,
                        max_favorable_excursion REAL,
                        max_adverse_excursion REAL,
                        spread_bps_avg REAL,
                        data_quality_status TEXT NOT NULL,
                        market_data_source TEXT NOT NULL,
                        created_at_utc TEXT NOT NULL,
                        PRIMARY KEY (event_id, instrument_id, horizon_label),
                        FOREIGN KEY (event_id) REFERENCES historical_events(event_id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_outcomes_maturity ON historical_event_outcomes(event_id, horizon_label, outcome_end_ts);
                    """
                )
        finally:
            self._close_connection(conn)

    @staticmethod
    def _compute_representation_hash(rep: FinancialEventRepresentation) -> str:
        payload = f"{rep.headline.strip().lower()}|{(rep.body_snippet or '').strip().lower()}|{rep.classification.primary_subtype.value}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def upsert_event(
        self,
        rep: FinancialEventRepresentation,
        jurisdiction: MarketJurisdiction = MarketJurisdiction.IN,
        source_article_id: Optional[str] = None,
        sector: Optional[str] = None,
        source_url: Optional[str] = None,
        is_synthetic: bool = False,
    ) -> Tuple[str, str]:
        """
        Idempotently ingest or revise a FinancialEventRepresentation.
        Returns (event_id, status) where status is 'INSERTED', 'SKIPPED_DUPLICATE', or 'UPDATED_REVISION'.
        """
        content_hash = self._compute_representation_hash(rep)
        now_iso = to_utc_iso(datetime.now(timezone.utc))
        event_ts_iso = to_utc_iso(rep.published_at_utc or rep.decision_timestamp)
        avail_ts_iso = to_utc_iso(rep.local_received_at_utc)
        proc_ts_iso = to_utc_iso(rep.processed_at_utc)

        entities_json = json.dumps([e.model_dump(mode="json") for e in rep.all_entities])
        mappings_json = json.dumps([m.model_dump(mode="json") for m in rep.all_instrument_mappings])
        metrics_json = json.dumps([m.model_dump(mode="json") for m in rep.extraction.metrics])
        surprise_json = json.dumps(rep.surprise.model_dump(mode="json"))

        # Collect distinct mapped instrument IDs
        instrument_records: List[Tuple[str, str]] = []
        seen_insts: Set[str] = set()
        for m in rep.all_instrument_mappings:
            inst = (m.instrument_id if hasattr(m, "instrument_id") else str(m)).strip().upper()
            rel = m.relevance.value if hasattr(m, "relevance") and hasattr(m.relevance, "value") else "DIRECT"
            if inst and inst not in seen_insts:
                seen_insts.add(inst)
                instrument_records.append((inst, rel))
        for ent in rep.all_entities:
            for sym in ent.instruments:
                inst = (sym.instrument_id if hasattr(sym, "instrument_id") else str(sym)).strip().upper()
                rel = sym.relevance.value if hasattr(sym, "relevance") and hasattr(sym.relevance, "value") else ("PRIMARY" if ent == rep.primary_entity else "SECONDARY")
                if inst and inst not in seen_insts:
                    seen_insts.add(inst)
                    instrument_records.append((inst, rel))

        primary_ent_id = rep.primary_entity.entity_id if rep.primary_entity else None

        conn = self._get_connection()
        try:
            with conn:
                # 1. Check if identical content_hash already exists under another event_id (duplicate detection)
                cur = conn.execute(
                    "SELECT event_id, content_hash, revision_number FROM historical_events WHERE event_id = ?",
                    (rep.event_id,),
                )
                existing_by_id = cur.fetchone()

                if existing_by_id is None:
                    # Check duplicate by content hash within the same jurisdiction and timestamp window
                    dup_cur = conn.execute(
                        "SELECT event_id FROM historical_events WHERE content_hash = ? AND jurisdiction = ?",
                        (content_hash, jurisdiction.value),
                    )
                    dup_row = dup_cur.fetchone()
                    if dup_row is not None:
                        return dup_row[0], "SKIPPED_DUPLICATE"

                    # Insert new event
                    conn.execute(
                        """
                        INSERT INTO historical_events (
                            event_id, source_article_id, revision_number, content_hash,
                            jurisdiction, source_feed, source_url, headline, body_snippet,
                            primary_entity_id, resolved_entities_json, instrument_mappings_json,
                            event_family, event_subtype, extracted_metrics_json, surprise_json,
                            materiality_score, materiality_tier, composite_confidence, sector,
                            event_timestamp_utc, system_available_at_utc, processed_at_utc,
                            schema_version, pipeline_version, is_synthetic, indexed_at_utc
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rep.event_id,
                            source_article_id,
                            1,
                            content_hash,
                            jurisdiction.value,
                            rep.source_name,
                            source_url,
                            rep.headline,
                            rep.body_snippet,
                            primary_ent_id,
                            entities_json,
                            mappings_json,
                            rep.classification.primary_family.value,
                            rep.classification.primary_subtype.value,
                            metrics_json,
                            surprise_json,
                            rep.materiality.composite_materiality_score,
                            rep.materiality.materiality_tier.value,
                            rep.composite_confidence,
                            sector,
                            event_ts_iso,
                            avail_ts_iso,
                            proc_ts_iso,
                            self.SCHEMA_VERSION,
                            rep.pipeline_version,
                            1 if is_synthetic else 0,
                            now_iso,
                        ),
                    )

                    # Insert instrument join links
                    for inst_id, tier in instrument_records:
                        conn.execute(
                            "INSERT OR IGNORE INTO historical_event_instruments (event_id, instrument_id, relevance_tier) VALUES (?, ?, ?)",
                            (rep.event_id, inst_id, tier),
                        )

                    # Record initial revision log
                    conn.execute(
                        """
                        INSERT INTO historical_event_revisions (
                            event_id, revision_number, content_hash, headline, body_snippet,
                            system_available_at_utc, recorded_at_utc
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (rep.event_id, 1, content_hash, rep.headline, rep.body_snippet, avail_ts_iso, now_iso),
                    )

                    return rep.event_id, "INSERTED"

                else:
                    # Event ID already exists; check if content changed (revision update)
                    existing_hash = existing_by_id[1]
                    current_rev = existing_by_id[2]

                    if existing_hash == content_hash:
                        return rep.event_id, "SKIPPED_DUPLICATE"

                    new_rev = current_rev + 1
                    conn.execute(
                        """
                        UPDATE historical_events SET
                            revision_number = ?, content_hash = ?, headline = ?, body_snippet = ?,
                            primary_entity_id = ?, resolved_entities_json = ?, instrument_mappings_json = ?,
                            event_family = ?, event_subtype = ?, extracted_metrics_json = ?, surprise_json = ?,
                            materiality_score = ?, materiality_tier = ?, composite_confidence = ?,
                            system_available_at_utc = ?, processed_at_utc = ?, indexed_at_utc = ?
                        WHERE event_id = ?
                        """,
                        (
                            new_rev,
                            content_hash,
                            rep.headline,
                            rep.body_snippet,
                            primary_ent_id,
                            entities_json,
                            mappings_json,
                            rep.classification.primary_family.value,
                            rep.classification.primary_subtype.value,
                            metrics_json,
                            surprise_json,
                            rep.materiality.composite_materiality_score,
                            rep.materiality.materiality_tier.value,
                            rep.composite_confidence,
                            avail_ts_iso,
                            proc_ts_iso,
                            now_iso,
                            rep.event_id,
                        ),
                    )

                    # Refresh instrument links
                    conn.execute("DELETE FROM historical_event_instruments WHERE event_id = ?", (rep.event_id,))
                    for inst_id, tier in instrument_records:
                        conn.execute(
                            "INSERT OR IGNORE INTO historical_event_instruments (event_id, instrument_id, relevance_tier) VALUES (?, ?, ?)",
                            (rep.event_id, inst_id, tier),
                        )

                    # Append revision snapshot
                    conn.execute(
                        """
                        INSERT INTO historical_event_revisions (
                            event_id, revision_number, content_hash, headline, body_snippet,
                            system_available_at_utc, recorded_at_utc
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (rep.event_id, new_rev, content_hash, rep.headline, rep.body_snippet, avail_ts_iso, now_iso),
                    )

                    return rep.event_id, "UPDATED_REVISION"
        finally:
            self._close_connection(conn)

    def _row_to_stored_event(self, row: Tuple[Any, ...], instruments: List[str]) -> StoredHistoricalEvent:
        return StoredHistoricalEvent(
            event_id=row[0],
            source_article_id=row[1],
            revision_number=row[2],
            content_hash=row[3],
            jurisdiction=MarketJurisdiction(row[4]),
            source_feed=row[5],
            source_url=row[6],
            headline=row[7],
            body_snippet=row[8],
            primary_entity_id=row[9],
            resolved_entities=json.loads(row[10]),
            instrument_mappings=json.loads(row[11]),
            instrument_ids=instruments,
            event_family=row[12],
            event_subtype=row[13],
            extracted_metrics=json.loads(row[14]),
            surprise=json.loads(row[15]),
            materiality_score=row[16],
            materiality_tier=row[17],
            composite_confidence=row[18],
            sector=row[19],
            event_timestamp_utc=parse_utc_iso(row[20]),
            system_available_at_utc=parse_utc_iso(row[21]),
            processed_at_utc=parse_utc_iso(row[22]),
            schema_version=row[23],
            pipeline_version=row[24],
            is_synthetic=bool(row[25]),
        )

    def get_event(self, event_id: str) -> Optional[StoredHistoricalEvent]:
        """Retrieve a single event by canonical ID."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT event_id, source_article_id, revision_number, content_hash,
                       jurisdiction, source_feed, source_url, headline, body_snippet,
                       primary_entity_id, resolved_entities_json, instrument_mappings_json,
                       event_family, event_subtype, extracted_metrics_json, surprise_json,
                       materiality_score, materiality_tier, composite_confidence, sector,
                       event_timestamp_utc, system_available_at_utc, processed_at_utc,
                       schema_version, pipeline_version, is_synthetic
                FROM historical_events WHERE event_id = ?
                """,
                (event_id,),
            )
            row = cur.fetchone()
            if not row:
                return None

            inst_cur = conn.execute(
                "SELECT instrument_id FROM historical_event_instruments WHERE event_id = ?",
                (event_id,),
            )
            instruments = [r[0] for r in inst_cur.fetchall()]
            return self._row_to_stored_event(row, instruments)
        finally:
            self._close_connection(conn)

    def get_revisions(self, event_id: str) -> List[Dict[str, Any]]:
        """Retrieve complete revision audit trail for an event."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT revision_number, content_hash, headline, body_snippet,
                       system_available_at_utc, recorded_at_utc
                FROM historical_event_revisions
                WHERE event_id = ?
                ORDER BY revision_number ASC
                """,
                (event_id,),
            )
            return [
                {
                    "revision_number": r[0],
                    "content_hash": r[1],
                    "headline": r[2],
                    "body_snippet": r[3],
                    "system_available_at_utc": r[4],
                    "recorded_at_utc": r[5],
                }
                for r in cur.fetchall()
            ]
        finally:
            self._close_connection(conn)

    def query_events(
        self,
        decision_timestamp: datetime,
        instrument_id: Optional[str] = None,
        event_subtype: Optional[str] = None,
        jurisdiction: Optional[MarketJurisdiction] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        exclude_event_ids: Optional[Set[str]] = None,
        exclude_content_hashes: Optional[Set[str]] = None,
        include_synthetic: bool = False,
        limit: int = 500,
    ) -> List[StoredHistoricalEvent]:
        """
        Causal point-in-time query for candidate historical events.
        Guarantees: system_available_at_utc < decision_timestamp strictly enforced.
        """
        decision_iso = to_utc_iso(decision_timestamp)
        conditions = ["e.system_available_at_utc < ?"]
        params: List[Any] = [decision_iso]

        if not include_synthetic:
            conditions.append("e.is_synthetic = 0")

        if jurisdiction is not None:
            conditions.append("e.jurisdiction = ?")
            params.append(jurisdiction.value)

        if event_subtype is not None:
            conditions.append("e.event_subtype = ?")
            params.append(event_subtype)

        if start_time is not None:
            conditions.append("e.system_available_at_utc >= ?")
            params.append(to_utc_iso(start_time))

        if end_time is not None:
            # Cap effective end_time at decision_timestamp to maintain causality
            effective_end = min(end_time, decision_timestamp)
            conditions.append("e.system_available_at_utc <= ?")
            params.append(to_utc_iso(effective_end))

        if instrument_id is not None:
            inst_clean = instrument_id.strip().upper()
            if ":" in inst_clean:
                conditions.append(
                    "EXISTS (SELECT 1 FROM historical_event_instruments i WHERE i.event_id = e.event_id AND i.instrument_id = ?)"
                )
                params.append(inst_clean)
            else:
                conditions.append(
                    "(e.primary_entity_id = ? OR EXISTS (SELECT 1 FROM historical_event_instruments i WHERE i.event_id = e.event_id AND (i.instrument_id = ? OR i.instrument_id LIKE ?)))"
                )
                params.extend([inst_clean, inst_clean, f"%:{inst_clean}:%"])

        where_sql = " AND ".join(conditions)
        sql = f"""
            SELECT e.event_id, e.source_article_id, e.revision_number, e.content_hash,
                   e.jurisdiction, e.source_feed, e.source_url, e.headline, e.body_snippet,
                   e.primary_entity_id, e.resolved_entities_json, e.instrument_mappings_json,
                   e.event_family, e.event_subtype, e.extracted_metrics_json, e.surprise_json,
                   e.materiality_score, e.materiality_tier, e.composite_confidence, e.sector,
                   e.event_timestamp_utc, e.system_available_at_utc, e.processed_at_utc,
                   e.schema_version, e.pipeline_version, e.is_synthetic
            FROM historical_events e
            WHERE {where_sql}
            ORDER BY e.system_available_at_utc DESC
            LIMIT ?
        """
        params.append(limit)

        conn = self._get_connection()
        try:
            cur = conn.execute(sql, params)
            rows = cur.fetchall()

            results: List[StoredHistoricalEvent] = []
            for row in rows:
                eid = row[0]
                chash = row[3]
                if exclude_event_ids and eid in exclude_event_ids:
                    continue
                if exclude_content_hashes and chash in exclude_content_hashes:
                    continue

                inst_cur = conn.execute(
                    "SELECT instrument_id FROM historical_event_instruments WHERE event_id = ?",
                    (eid,),
                )
                instruments = [r[0] for r in inst_cur.fetchall()]
                results.append(self._row_to_stored_event(row, instruments))
            return results
        finally:
            self._close_connection(conn)

    # ------------------------------------------------------------------------
    # Separated Embedding Persistence (Float32 IEEE-754 packed binary blobs)
    # ------------------------------------------------------------------------

    def save_embedding(self, event_id: str, model_version: str, vector: List[float]) -> None:
        """Persist a versioned dense semantic vector embedding."""
        dim = len(vector)
        blob = struct.pack(f"{dim}f", *vector)
        now_iso = to_utc_iso(datetime.now(timezone.utc))

        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO historical_event_embeddings (
                        event_id, model_version, embedding_dim, vector_blob, created_at_utc
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (event_id, model_version, dim, blob, now_iso),
                )
        finally:
            self._close_connection(conn)

    def get_embedding(self, event_id: str, model_version: str) -> Optional[List[float]]:
        """Retrieve vector embedding matching the exact model version."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "SELECT embedding_dim, vector_blob FROM historical_event_embeddings WHERE event_id = ? AND model_version = ?",
                (event_id, model_version),
            )
            row = cur.fetchone()
            if not row:
                return None
            dim, blob = row[0], row[1]
            return list(struct.unpack(f"{dim}f", blob))
        finally:
            self._close_connection(conn)

    def get_embeddings_batch(self, event_ids: List[str], model_version: str) -> Dict[str, List[float]]:
        """Batch-fetch embeddings for a list of candidate event IDs."""
        if not event_ids:
            return {}
        placeholders = ",".join("?" for _ in event_ids)
        sql = f"""
            SELECT event_id, embedding_dim, vector_blob
            FROM historical_event_embeddings
            WHERE model_version = ? AND event_id IN ({placeholders})
        """
        conn = self._get_connection()
        try:
            cur = conn.execute(sql, [model_version] + list(event_ids))
            out: Dict[str, List[float]] = {}
            for row in cur.fetchall():
                eid, dim, blob = row[0], row[1], row[2]
                out[eid] = list(struct.unpack(f"{dim}f", blob))
            return out
        finally:
            self._close_connection(conn)

    # ------------------------------------------------------------------------
    # Separated Future Market Outcomes (Causal Maturity Enforced)
    # ------------------------------------------------------------------------

    def save_outcome(self, outcome: StoredOutcomeRecord) -> None:
        """Persist a verified future market response record with explicit timestamps."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO historical_event_outcomes (
                        event_id, instrument_id, horizon_label,
                        outcome_start_ts, outcome_end_ts,
                        return_simple, return_log, benchmark_adjusted_return, benchmark_instrument,
                        realized_volatility, max_favorable_excursion, max_adverse_excursion,
                        spread_bps_avg, data_quality_status, market_data_source, created_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        outcome.event_id,
                        outcome.instrument_id.strip().upper(),
                        outcome.horizon_label,
                        to_utc_iso(outcome.outcome_start_ts),
                        to_utc_iso(outcome.outcome_end_ts),
                        outcome.return_simple,
                        outcome.return_log,
                        outcome.benchmark_adjusted_return,
                        outcome.benchmark_instrument,
                        outcome.realized_volatility,
                        outcome.max_favorable_excursion,
                        outcome.max_adverse_excursion,
                        outcome.spread_bps_avg,
                        outcome.data_quality_status,
                        outcome.market_data_source,
                        to_utc_iso(outcome.created_at_utc),
                    ),
                )
        finally:
            self._close_connection(conn)

    def get_matured_outcomes(
        self,
        event_ids: List[str],
        decision_timestamp: datetime,
        horizon_label: Optional[str] = None,
    ) -> Dict[str, List[StoredOutcomeRecord]]:
        """
        Retrieve ONLY outcomes that had completely matured by `decision_timestamp`.
        Strictly enforces `outcome_end_ts <= decision_timestamp` at SQL boundary.
        """
        if not event_ids:
            return {}
        decision_iso = to_utc_iso(decision_timestamp)
        placeholders = ",".join("?" for _ in event_ids)
        params: List[Any] = [decision_iso] + list(event_ids)
        horizon_filter = ""
        if horizon_label is not None:
            horizon_filter = "AND horizon_label = ?"
            params.append(horizon_label)

        sql = f"""
            SELECT event_id, instrument_id, horizon_label,
                   outcome_start_ts, outcome_end_ts,
                   return_simple, return_log, benchmark_adjusted_return, benchmark_instrument,
                   realized_volatility, max_favorable_excursion, max_adverse_excursion,
                   spread_bps_avg, data_quality_status, market_data_source, created_at_utc
            FROM historical_event_outcomes
            WHERE outcome_end_ts <= ? AND event_id IN ({placeholders}) {horizon_filter}
        """

        conn = self._get_connection()
        try:
            cur = conn.execute(sql, params)
            grouped: Dict[str, List[StoredOutcomeRecord]] = {}
            for row in cur.fetchall():
                rec = StoredOutcomeRecord(
                    event_id=row[0],
                    instrument_id=row[1],
                    horizon_label=row[2],
                    outcome_start_ts=parse_utc_iso(row[3]),
                    outcome_end_ts=parse_utc_iso(row[4]),
                    return_simple=row[5],
                    return_log=row[6],
                    benchmark_adjusted_return=row[7],
                    benchmark_instrument=row[8],
                    realized_volatility=row[9],
                    max_favorable_excursion=row[10],
                    max_adverse_excursion=row[11],
                    spread_bps_avg=row[12],
                    data_quality_status=row[13],
                    market_data_source=row[14],
                    created_at_utc=parse_utc_iso(row[15]),
                )
                grouped.setdefault(rec.event_id, []).append(rec)
            return grouped
        finally:
            self._close_connection(conn)

    # ------------------------------------------------------------------------
    # Diagnostics & Storage Telemetry
    # ------------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Compute store coverage statistics and disk footprint."""
        conn = self._get_connection()
        try:
            total_events = conn.execute("SELECT COUNT(*) FROM historical_events").fetchone()[0]
            total_revisions = conn.execute("SELECT COUNT(*) FROM historical_event_revisions").fetchone()[0]
            total_embeddings = conn.execute("SELECT COUNT(*) FROM historical_event_embeddings").fetchone()[0]
            total_outcomes = conn.execute("SELECT COUNT(*) FROM historical_event_outcomes").fetchone()[0]

            jur_rows = conn.execute(
                "SELECT jurisdiction, COUNT(*) FROM historical_events GROUP BY jurisdiction"
            ).fetchall()
            by_jurisdiction = {r[0]: r[1] for r in jur_rows}

            model_rows = conn.execute(
                "SELECT model_version, COUNT(*) FROM historical_event_embeddings GROUP BY model_version"
            ).fetchall()
            embeddings_by_model = {r[0]: r[1] for r in model_rows}

            disk_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0

            return {
                "schema_version": self.SCHEMA_VERSION,
                "total_events": total_events,
                "total_revisions": total_revisions,
                "total_embeddings": total_embeddings,
                "total_outcomes": total_outcomes,
                "by_jurisdiction": by_jurisdiction,
                "embeddings_by_model": embeddings_by_model,
                "storage_bytes": disk_bytes,
            }
        finally:
            self._close_connection(conn)

    def verify_integrity(self) -> bool:
        """Run SQLite PRAGMA integrity_check."""
        conn = self._get_connection()
        try:
            res = conn.execute("PRAGMA integrity_check;").fetchone()
            return res is not None and res[0] == "ok"
        finally:
            self._close_connection(conn)
