"""Persistent Local-First Bitemporal Historical Event Store Engine.

Provides durable SQLite WAL storage separating:
1. Canonical event registry (`historical_events`).
2. Append-only bitemporal revision snapshots (`historical_event_revisions`), preserving
   headline, body, entities, instrument mappings, taxonomy, metrics, surprise, materiality,
   and embedding references per revision.
3. Many-to-many current instrument associations (`historical_event_instruments`).
4. Versioned dense semantic vector embeddings per event & revision
   (`historical_event_embeddings`, `historical_event_revision_embeddings`).
5. Strictly separated post-event market outcomes (`historical_event_outcomes`).

All as-of queries (`query_events`, `get_event_as_of`) reconstruct the exact latest
revision satisfying `system_available_at_utc < decision_timestamp`, ensuring future
corrections never leak backward to earlier decision timestamps.
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
    """Hydrated canonical historical event record reconstructed as-of decision timestamp."""

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
    embedding_model_version: Optional[str] = None
    embedding_ref: Optional[str] = None
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
    label_type: str = "REACTION"  # "REACTION" | "EXECUTABLE"
    outcome_start_ts: datetime
    outcome_end_ts: datetime
    return_simple: Optional[float] = None
    return_log: Optional[float] = None
    benchmark_adjusted_return: Optional[float] = None
    benchmark_instrument: Optional[str] = None
    benchmark_adjustment_method: Optional[str] = None
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
    Enforces idempotent ingestion, full bitemporal revision reconstruction, duplicate prevention,
    strict causal availability filtering, and embedding/outcome separation.
    """

    SCHEMA_VERSION = "4.1.0"

    def __init__(self, db_path: Union[str, Path] = "data/alpha_historical_store.db") -> None:
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn_str = str(self.db_path)
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

    @staticmethod
    def _existing_columns(conn: sqlite3.Connection, table_name: str) -> Set[str]:
        cur = conn.execute(f"PRAGMA table_info({table_name});")
        return {row[1] for row in cur.fetchall()}

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
                        embedding_model_version TEXT,
                        embedding_ref TEXT,
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
                        source_article_id TEXT,
                        jurisdiction TEXT,
                        source_feed TEXT,
                        source_url TEXT,
                        headline TEXT NOT NULL,
                        body_snippet TEXT,
                        primary_entity_id TEXT,
                        resolved_entities_json TEXT,
                        instrument_mappings_json TEXT,
                        instrument_ids_json TEXT,
                        event_family TEXT,
                        event_subtype TEXT,
                        extracted_metrics_json TEXT,
                        surprise_json TEXT,
                        materiality_score REAL,
                        materiality_tier TEXT,
                        composite_confidence REAL,
                        sector TEXT,
                        embedding_model_version TEXT,
                        embedding_ref TEXT,
                        event_timestamp_utc TEXT,
                        system_available_at_utc TEXT NOT NULL,
                        processed_at_utc TEXT,
                        recorded_at_utc TEXT NOT NULL,
                        schema_version TEXT,
                        pipeline_version TEXT,
                        is_synthetic INTEGER NOT NULL DEFAULT 0,
                        FOREIGN KEY (event_id) REFERENCES historical_events(event_id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_revisions_bitemporal
                        ON historical_event_revisions(event_id, system_available_at_utc, revision_number);

                    CREATE TABLE IF NOT EXISTS historical_event_embeddings (
                        event_id TEXT NOT NULL,
                        model_version TEXT NOT NULL,
                        embedding_dim INTEGER NOT NULL,
                        vector_blob BLOB NOT NULL,
                        created_at_utc TEXT NOT NULL,
                        PRIMARY KEY (event_id, model_version),
                        FOREIGN KEY (event_id) REFERENCES historical_events(event_id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS historical_event_revision_embeddings (
                        event_id TEXT NOT NULL,
                        revision_number INTEGER NOT NULL,
                        model_version TEXT NOT NULL,
                        embedding_dim INTEGER NOT NULL,
                        vector_blob BLOB NOT NULL,
                        created_at_utc TEXT NOT NULL,
                        PRIMARY KEY (event_id, revision_number, model_version),
                        FOREIGN KEY (event_id) REFERENCES historical_events(event_id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS historical_event_outcomes (
                        event_id TEXT NOT NULL,
                        instrument_id TEXT NOT NULL,
                        horizon_label TEXT NOT NULL,
                        label_type TEXT NOT NULL DEFAULT 'REACTION',
                        outcome_start_ts TEXT NOT NULL,
                        outcome_end_ts TEXT NOT NULL,
                        return_simple REAL,
                        return_log REAL,
                        benchmark_adjusted_return REAL,
                        benchmark_instrument TEXT,
                        benchmark_adjustment_method TEXT,
                        realized_volatility REAL,
                        max_favorable_excursion REAL,
                        max_adverse_excursion REAL,
                        spread_bps_avg REAL,
                        data_quality_status TEXT NOT NULL,
                        market_data_source TEXT NOT NULL,
                        created_at_utc TEXT NOT NULL,
                        PRIMARY KEY (event_id, instrument_id, horizon_label, label_type),
                        FOREIGN KEY (event_id) REFERENCES historical_events(event_id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_outcomes_maturity ON historical_event_outcomes(event_id, horizon_label, outcome_end_ts);
                    """
                )

                # Safe forward migrations for existing Phase 4 SQLite stores
                ev_cols = self._existing_columns(conn, "historical_events")
                if "embedding_model_version" not in ev_cols:
                    conn.execute("ALTER TABLE historical_events ADD COLUMN embedding_model_version TEXT;")
                if "embedding_ref" not in ev_cols:
                    conn.execute("ALTER TABLE historical_events ADD COLUMN embedding_ref TEXT;")

                rev_cols = self._existing_columns(conn, "historical_event_revisions")
                rev_additions = [
                    ("source_article_id", "TEXT"),
                    ("jurisdiction", "TEXT"),
                    ("source_feed", "TEXT"),
                    ("source_url", "TEXT"),
                    ("primary_entity_id", "TEXT"),
                    ("resolved_entities_json", "TEXT"),
                    ("instrument_mappings_json", "TEXT"),
                    ("instrument_ids_json", "TEXT"),
                    ("event_family", "TEXT"),
                    ("event_subtype", "TEXT"),
                    ("extracted_metrics_json", "TEXT"),
                    ("surprise_json", "TEXT"),
                    ("materiality_score", "REAL"),
                    ("materiality_tier", "TEXT"),
                    ("composite_confidence", "REAL"),
                    ("sector", "TEXT"),
                    ("embedding_model_version", "TEXT"),
                    ("embedding_ref", "TEXT"),
                    ("event_timestamp_utc", "TEXT"),
                    ("processed_at_utc", "TEXT"),
                    ("schema_version", "TEXT"),
                    ("pipeline_version", "TEXT"),
                    ("is_synthetic", "INTEGER NOT NULL DEFAULT 0"),
                ]
                for col_name, col_type in rev_additions:
                    if col_name not in rev_cols:
                        conn.execute(f"ALTER TABLE historical_event_revisions ADD COLUMN {col_name} {col_type};")

                out_cols = self._existing_columns(conn, "historical_event_outcomes")
                if "label_type" not in out_cols:
                    conn.executescript(
                        """
                        CREATE TABLE historical_event_outcomes_v41 (
                            event_id TEXT NOT NULL,
                            instrument_id TEXT NOT NULL,
                            horizon_label TEXT NOT NULL,
                            label_type TEXT NOT NULL DEFAULT 'REACTION',
                            outcome_start_ts TEXT NOT NULL,
                            outcome_end_ts TEXT NOT NULL,
                            return_simple REAL,
                            return_log REAL,
                            benchmark_adjusted_return REAL,
                            benchmark_instrument TEXT,
                            benchmark_adjustment_method TEXT,
                            realized_volatility REAL,
                            max_favorable_excursion REAL,
                            max_adverse_excursion REAL,
                            spread_bps_avg REAL,
                            data_quality_status TEXT NOT NULL,
                            market_data_source TEXT NOT NULL,
                            created_at_utc TEXT NOT NULL,
                            PRIMARY KEY (event_id, instrument_id, horizon_label, label_type),
                            FOREIGN KEY (event_id) REFERENCES historical_events(event_id) ON DELETE CASCADE
                        );
                        INSERT INTO historical_event_outcomes_v41 (
                            event_id, instrument_id, horizon_label, label_type,
                            outcome_start_ts, outcome_end_ts,
                            return_simple, return_log, benchmark_adjusted_return, benchmark_instrument,
                            benchmark_adjustment_method, realized_volatility, max_favorable_excursion,
                            max_adverse_excursion, spread_bps_avg, data_quality_status,
                            market_data_source, created_at_utc
                        )
                        SELECT
                            event_id, instrument_id, horizon_label, 'REACTION',
                            outcome_start_ts, outcome_end_ts,
                            return_simple, return_log, benchmark_adjusted_return, benchmark_instrument,
                            NULL, realized_volatility, max_favorable_excursion,
                            max_adverse_excursion, spread_bps_avg, data_quality_status,
                            market_data_source, created_at_utc
                        FROM historical_event_outcomes;
                        DROP TABLE historical_event_outcomes;
                        ALTER TABLE historical_event_outcomes_v41 RENAME TO historical_event_outcomes;
                        CREATE INDEX IF NOT EXISTS idx_outcomes_maturity ON historical_event_outcomes(event_id, horizon_label, outcome_end_ts);
                        """
                    )
                elif "benchmark_adjustment_method" not in out_cols:
                    conn.execute("ALTER TABLE historical_event_outcomes ADD COLUMN benchmark_adjustment_method TEXT;")
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
        embedding_model_version: Optional[str] = None,
        embedding_ref: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Idempotently ingest or append a bitemporal revision of a FinancialEventRepresentation.
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
                rel = (
                    sym.relevance.value
                    if hasattr(sym, "relevance") and hasattr(sym.relevance, "value")
                    else ("PRIMARY" if ent == rep.primary_entity else "SECONDARY")
                )
                if inst and inst not in seen_insts:
                    seen_insts.add(inst)
                    instrument_records.append((inst, rel))

        inst_ids_list = [inst for inst, _ in instrument_records]
        inst_ids_json = json.dumps(inst_ids_list)
        primary_ent_id = rep.primary_entity.entity_id if rep.primary_entity else None
        fam_val = rep.classification.primary_family.value
        sub_val = rep.classification.primary_subtype.value
        mat_score = rep.materiality.composite_materiality_score
        mat_tier = rep.materiality.materiality_tier.value
        syn_int = 1 if is_synthetic else 0

        conn = self._get_connection()
        try:
            with conn:
                cur = conn.execute(
                    "SELECT event_id, content_hash, revision_number, sector FROM historical_events WHERE event_id = ?",
                    (rep.event_id,),
                )
                existing_by_id = cur.fetchone()

                if existing_by_id is None:
                    dup_cur = conn.execute(
                        "SELECT event_id FROM historical_events WHERE content_hash = ? AND jurisdiction = ?",
                        (content_hash, jurisdiction.value),
                    )
                    dup_row = dup_cur.fetchone()
                    if dup_row is not None:
                        return dup_row[0], "SKIPPED_DUPLICATE"

                    conn.execute(
                        """
                        INSERT INTO historical_events (
                            event_id, source_article_id, revision_number, content_hash,
                            jurisdiction, source_feed, source_url, headline, body_snippet,
                            primary_entity_id, resolved_entities_json, instrument_mappings_json,
                            event_family, event_subtype, extracted_metrics_json, surprise_json,
                            materiality_score, materiality_tier, composite_confidence, sector,
                            embedding_model_version, embedding_ref,
                            event_timestamp_utc, system_available_at_utc, processed_at_utc,
                            schema_version, pipeline_version, is_synthetic, indexed_at_utc
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            fam_val,
                            sub_val,
                            metrics_json,
                            surprise_json,
                            mat_score,
                            mat_tier,
                            rep.composite_confidence,
                            sector,
                            embedding_model_version,
                            embedding_ref,
                            event_ts_iso,
                            avail_ts_iso,
                            proc_ts_iso,
                            self.SCHEMA_VERSION,
                            rep.pipeline_version,
                            syn_int,
                            now_iso,
                        ),
                    )

                    for inst_id, tier in instrument_records:
                        conn.execute(
                            "INSERT OR IGNORE INTO historical_event_instruments (event_id, instrument_id, relevance_tier) VALUES (?, ?, ?)",
                            (rep.event_id, inst_id, tier),
                        )

                    conn.execute(
                        """
                        INSERT INTO historical_event_revisions (
                            event_id, revision_number, content_hash, source_article_id,
                            jurisdiction, source_feed, source_url, headline, body_snippet,
                            primary_entity_id, resolved_entities_json, instrument_mappings_json,
                            instrument_ids_json, event_family, event_subtype,
                            extracted_metrics_json, surprise_json, materiality_score,
                            materiality_tier, composite_confidence, sector,
                            embedding_model_version, embedding_ref,
                            event_timestamp_utc, system_available_at_utc, processed_at_utc,
                            recorded_at_utc, schema_version, pipeline_version, is_synthetic
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rep.event_id,
                            1,
                            content_hash,
                            source_article_id,
                            jurisdiction.value,
                            rep.source_name,
                            source_url,
                            rep.headline,
                            rep.body_snippet,
                            primary_ent_id,
                            entities_json,
                            mappings_json,
                            inst_ids_json,
                            fam_val,
                            sub_val,
                            metrics_json,
                            surprise_json,
                            mat_score,
                            mat_tier,
                            rep.composite_confidence,
                            sector,
                            embedding_model_version,
                            embedding_ref,
                            event_ts_iso,
                            avail_ts_iso,
                            proc_ts_iso,
                            now_iso,
                            self.SCHEMA_VERSION,
                            rep.pipeline_version,
                            syn_int,
                        ),
                    )
                    return rep.event_id, "INSERTED"

                else:
                    existing_hash = existing_by_id[1]
                    current_rev = existing_by_id[2]
                    eff_sector = sector if sector is not None else existing_by_id[3]

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
                            sector = ?, embedding_model_version = COALESCE(?, embedding_model_version),
                            embedding_ref = COALESCE(?, embedding_ref),
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
                            fam_val,
                            sub_val,
                            metrics_json,
                            surprise_json,
                            mat_score,
                            mat_tier,
                            rep.composite_confidence,
                            eff_sector,
                            embedding_model_version,
                            embedding_ref,
                            avail_ts_iso,
                            proc_ts_iso,
                            now_iso,
                            rep.event_id,
                        ),
                    )

                    conn.execute("DELETE FROM historical_event_instruments WHERE event_id = ?", (rep.event_id,))
                    for inst_id, tier in instrument_records:
                        conn.execute(
                            "INSERT OR IGNORE INTO historical_event_instruments (event_id, instrument_id, relevance_tier) VALUES (?, ?, ?)",
                            (rep.event_id, inst_id, tier),
                        )

                    conn.execute(
                        """
                        INSERT INTO historical_event_revisions (
                            event_id, revision_number, content_hash, source_article_id,
                            jurisdiction, source_feed, source_url, headline, body_snippet,
                            primary_entity_id, resolved_entities_json, instrument_mappings_json,
                            instrument_ids_json, event_family, event_subtype,
                            extracted_metrics_json, surprise_json, materiality_score,
                            materiality_tier, composite_confidence, sector,
                            embedding_model_version, embedding_ref,
                            event_timestamp_utc, system_available_at_utc, processed_at_utc,
                            recorded_at_utc, schema_version, pipeline_version, is_synthetic
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rep.event_id,
                            new_rev,
                            content_hash,
                            source_article_id,
                            jurisdiction.value,
                            rep.source_name,
                            source_url,
                            rep.headline,
                            rep.body_snippet,
                            primary_ent_id,
                            entities_json,
                            mappings_json,
                            inst_ids_json,
                            fam_val,
                            sub_val,
                            metrics_json,
                            surprise_json,
                            mat_score,
                            mat_tier,
                            rep.composite_confidence,
                            eff_sector,
                            embedding_model_version,
                            embedding_ref,
                            event_ts_iso,
                            avail_ts_iso,
                            proc_ts_iso,
                            now_iso,
                            self.SCHEMA_VERSION,
                            rep.pipeline_version,
                            syn_int,
                        ),
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
            resolved_entities=json.loads(row[10]) if row[10] else [],
            instrument_mappings=json.loads(row[11]) if row[11] else [],
            instrument_ids=instruments,
            event_family=row[12],
            event_subtype=row[13],
            extracted_metrics=json.loads(row[14]) if row[14] else [],
            surprise=json.loads(row[15]) if row[15] else {},
            materiality_score=row[16],
            materiality_tier=row[17],
            composite_confidence=row[18],
            sector=row[19],
            embedding_model_version=row[20],
            embedding_ref=row[21],
            event_timestamp_utc=parse_utc_iso(row[22]),
            system_available_at_utc=parse_utc_iso(row[23]),
            processed_at_utc=parse_utc_iso(row[24]),
            schema_version=row[25],
            pipeline_version=row[26],
            is_synthetic=bool(row[27]),
        )

    def get_event(self, event_id: str) -> Optional[StoredHistoricalEvent]:
        """Retrieve latest stored state for a single event by canonical ID."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT event_id, source_article_id, revision_number, content_hash,
                       jurisdiction, source_feed, source_url, headline, body_snippet,
                       primary_entity_id, resolved_entities_json, instrument_mappings_json,
                       event_family, event_subtype, extracted_metrics_json, surprise_json,
                       materiality_score, materiality_tier, composite_confidence, sector,
                       embedding_model_version, embedding_ref,
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

    def get_event_as_of(
        self,
        event_id: str,
        decision_timestamp: datetime,
    ) -> Optional[StoredHistoricalEvent]:
        """
        Bitemporal reconstruction: return the exact revision of `event_id` that was active at
        `decision_timestamp` (`system_available_at_utc < decision_timestamp`), or None if not yet available.
        """
        decision_iso = to_utc_iso(decision_timestamp)
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT r.event_id,
                       COALESCE(r.source_article_id, e.source_article_id),
                       r.revision_number,
                       r.content_hash,
                       COALESCE(r.jurisdiction, e.jurisdiction),
                       COALESCE(r.source_feed, e.source_feed),
                       COALESCE(r.source_url, e.source_url),
                       r.headline,
                       r.body_snippet,
                       COALESCE(r.primary_entity_id, e.primary_entity_id),
                       COALESCE(r.resolved_entities_json, e.resolved_entities_json),
                       COALESCE(r.instrument_mappings_json, e.instrument_mappings_json),
                       COALESCE(r.event_family, e.event_family),
                       COALESCE(r.event_subtype, e.event_subtype),
                       COALESCE(r.extracted_metrics_json, e.extracted_metrics_json),
                       COALESCE(r.surprise_json, e.surprise_json),
                       COALESCE(r.materiality_score, e.materiality_score),
                       COALESCE(r.materiality_tier, e.materiality_tier),
                       COALESCE(r.composite_confidence, e.composite_confidence),
                       COALESCE(r.sector, e.sector),
                       r.embedding_model_version,
                       r.embedding_ref,
                       COALESCE(r.event_timestamp_utc, e.event_timestamp_utc),
                       r.system_available_at_utc,
                       COALESCE(r.processed_at_utc, e.processed_at_utc),
                       COALESCE(r.schema_version, e.schema_version),
                       COALESCE(r.pipeline_version, e.pipeline_version),
                       COALESCE(r.is_synthetic, e.is_synthetic),
                       r.instrument_ids_json
                FROM historical_event_revisions r
                JOIN historical_events e ON e.event_id = r.event_id
                WHERE r.event_id = ? AND r.system_available_at_utc < ?
                ORDER BY r.revision_number DESC
                LIMIT 1
                """,
                (event_id, decision_iso),
            )
            row = cur.fetchone()
            if not row:
                return None
            inst_json = row[28]
            if inst_json:
                instruments = json.loads(inst_json)
            else:
                inst_cur = conn.execute(
                    "SELECT instrument_id FROM historical_event_instruments WHERE event_id = ?",
                    (event_id,),
                )
                instruments = [r[0] for r in inst_cur.fetchall()]
            return self._row_to_stored_event(row[:28], instruments)
        finally:
            self._close_connection(conn)

    def get_revisions(self, event_id: str) -> List[Dict[str, Any]]:
        """Retrieve complete bitemporal revision audit trail for an event."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT revision_number, content_hash, headline, body_snippet,
                       primary_entity_id, event_family, event_subtype,
                       materiality_score, extracted_metrics_json, surprise_json,
                       embedding_model_version, embedding_ref,
                       system_available_at_utc, processed_at_utc, recorded_at_utc
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
                    "primary_entity_id": r[4],
                    "event_family": r[5],
                    "event_subtype": r[6],
                    "materiality_score": r[7],
                    "extracted_metrics": json.loads(r[8]) if r[8] else [],
                    "surprise": json.loads(r[9]) if r[9] else {},
                    "embedding_model_version": r[10],
                    "embedding_ref": r[11],
                    "system_available_at_utc": r[12],
                    "processed_at_utc": r[13],
                    "recorded_at_utc": r[14],
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
        Bitemporal causal point-in-time query for candidate historical events.
        Reconstructs the EXACT revision of each event that had been observed strictly before
        `decision_timestamp` (`r.system_available_at_utc < decision_timestamp`), preventing
        subsequent revisions from leaking backward or hiding prior versions.
        """
        decision_iso = to_utc_iso(decision_timestamp)
        outer_conditions: List[str] = ["rn = 1"]
        outer_params: List[Any] = []

        if not include_synthetic:
            outer_conditions.append("eff_is_synthetic = 0")

        if jurisdiction is not None:
            outer_conditions.append("eff_jurisdiction = ?")
            outer_params.append(jurisdiction.value)

        if event_subtype is not None:
            outer_conditions.append("eff_event_subtype = ?")
            outer_params.append(event_subtype)

        if start_time is not None:
            outer_conditions.append("eff_avail_ts >= ?")
            outer_params.append(to_utc_iso(start_time))

        if end_time is not None:
            effective_end = min(end_time, decision_timestamp)
            outer_conditions.append("eff_avail_ts <= ?")
            outer_params.append(to_utc_iso(effective_end))

        outer_where = " AND ".join(outer_conditions)

        sql = f"""
            WITH as_of_revs AS (
                SELECT r.event_id,
                       COALESCE(r.source_article_id, e.source_article_id) AS eff_source_article_id,
                       r.revision_number,
                       r.content_hash,
                       COALESCE(r.jurisdiction, e.jurisdiction) AS eff_jurisdiction,
                       COALESCE(r.source_feed, e.source_feed) AS eff_source_feed,
                       COALESCE(r.source_url, e.source_url) AS eff_source_url,
                       r.headline,
                       r.body_snippet,
                       COALESCE(r.primary_entity_id, e.primary_entity_id) AS eff_primary_entity_id,
                       COALESCE(r.resolved_entities_json, e.resolved_entities_json) AS eff_resolved_entities_json,
                       COALESCE(r.instrument_mappings_json, e.instrument_mappings_json) AS eff_instrument_mappings_json,
                       COALESCE(r.event_family, e.event_family) AS eff_event_family,
                       COALESCE(r.event_subtype, e.event_subtype) AS eff_event_subtype,
                       COALESCE(r.extracted_metrics_json, e.extracted_metrics_json) AS eff_extracted_metrics_json,
                       COALESCE(r.surprise_json, e.surprise_json) AS eff_surprise_json,
                       COALESCE(r.materiality_score, e.materiality_score) AS eff_materiality_score,
                       COALESCE(r.materiality_tier, e.materiality_tier) AS eff_materiality_tier,
                       COALESCE(r.composite_confidence, e.composite_confidence) AS eff_composite_confidence,
                       COALESCE(r.sector, e.sector) AS eff_sector,
                       r.embedding_model_version AS eff_emb_model,
                       r.embedding_ref AS eff_emb_ref,
                       COALESCE(r.event_timestamp_utc, e.event_timestamp_utc) AS eff_event_ts,
                       r.system_available_at_utc AS eff_avail_ts,
                       COALESCE(r.processed_at_utc, e.processed_at_utc) AS eff_proc_ts,
                       COALESCE(r.schema_version, e.schema_version) AS eff_schema_ver,
                       COALESCE(r.pipeline_version, e.pipeline_version) AS eff_pipe_ver,
                       COALESCE(r.is_synthetic, e.is_synthetic) AS eff_is_synthetic,
                       r.instrument_ids_json AS eff_inst_ids_json,
                       ROW_NUMBER() OVER (PARTITION BY r.event_id ORDER BY r.revision_number DESC) AS rn
                FROM historical_event_revisions r
                JOIN historical_events e ON e.event_id = r.event_id
                WHERE r.system_available_at_utc < ?
            )
            SELECT event_id, eff_source_article_id, revision_number, content_hash,
                   eff_jurisdiction, eff_source_feed, eff_source_url, headline, body_snippet,
                   eff_primary_entity_id, eff_resolved_entities_json, eff_instrument_mappings_json,
                   eff_event_family, eff_event_subtype, eff_extracted_metrics_json, eff_surprise_json,
                   eff_materiality_score, eff_materiality_tier, eff_composite_confidence, eff_sector,
                   eff_emb_model, eff_emb_ref,
                   eff_event_ts, eff_avail_ts, eff_proc_ts,
                   eff_schema_ver, eff_pipe_ver, eff_is_synthetic,
                   eff_inst_ids_json
            FROM as_of_revs
            WHERE {outer_where}
            ORDER BY eff_avail_ts DESC
        """

        inst_filter_clean = instrument_id.strip().upper() if instrument_id is not None else None
        conn = self._get_connection()
        try:
            cur = conn.execute(sql, [decision_iso] + outer_params)
            results: List[StoredHistoricalEvent] = []
            for row in cur.fetchall():
                eid = row[0]
                chash = row[3]
                if exclude_event_ids and eid in exclude_event_ids:
                    continue
                if exclude_content_hashes and chash in exclude_content_hashes:
                    continue

                inst_json = row[28]
                if inst_json:
                    instruments = json.loads(inst_json)
                else:
                    inst_cur = conn.execute(
                        "SELECT instrument_id FROM historical_event_instruments WHERE event_id = ?",
                        (eid,),
                    )
                    instruments = [r[0] for r in inst_cur.fetchall()]

                # Apply point-in-time instrument filter strictly against the reconstructed revision's instruments
                if inst_filter_clean is not None:
                    prim_upper = (row[9] or "").upper()
                    insts_upper = [x.upper() for x in instruments]
                    if ":" in inst_filter_clean:
                        if inst_filter_clean not in insts_upper:
                            continue
                    else:
                        token_match = (
                            prim_upper == inst_filter_clean
                            or inst_filter_clean in insts_upper
                            or any(f":{inst_filter_clean}:" in x for x in insts_upper)
                        )
                        if not token_match:
                            continue

                results.append(self._row_to_stored_event(row[:28], instruments))
                if len(results) >= limit:
                    break
            return results
        finally:
            self._close_connection(conn)

    # ------------------------------------------------------------------------
    # Separated Embedding Persistence (Float32 IEEE-754 packed binary blobs)
    # ------------------------------------------------------------------------

    def save_embedding(
        self,
        event_id: str,
        model_version: str,
        vector: List[float],
        revision_number: Optional[int] = None,
    ) -> str:
        """Persist a versioned dense semantic vector embedding scoped cleanly to event + revision."""
        dim = len(vector)
        blob = struct.pack(f"{dim}f", *vector)
        now_iso = to_utc_iso(datetime.now(timezone.utc))

        conn = self._get_connection()
        try:
            with conn:
                if revision_number is None:
                    cur = conn.execute(
                        "SELECT revision_number FROM historical_events WHERE event_id = ?",
                        (event_id,),
                    )
                    row = cur.fetchone()
                    rev = int(row[0]) if row else 1
                else:
                    rev = int(revision_number)

                emb_ref = f"emb://{model_version}/{event_id}/rev{rev}"

                conn.execute(
                    """
                    INSERT OR REPLACE INTO historical_event_revision_embeddings (
                        event_id, revision_number, model_version, embedding_dim, vector_blob, created_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (event_id, rev, model_version, dim, blob, now_iso),
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO historical_event_embeddings (
                        event_id, model_version, embedding_dim, vector_blob, created_at_utc
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (event_id, model_version, dim, blob, now_iso),
                )
                conn.execute(
                    """
                    UPDATE historical_event_revisions
                    SET embedding_model_version = ?, embedding_ref = ?
                    WHERE event_id = ? AND revision_number = ?
                    """,
                    (model_version, emb_ref, event_id, rev),
                )
                conn.execute(
                    """
                    UPDATE historical_events
                    SET embedding_model_version = ?, embedding_ref = ?
                    WHERE event_id = ? AND revision_number = ?
                    """,
                    (model_version, emb_ref, event_id, rev),
                )
            return emb_ref
        finally:
            self._close_connection(conn)

    def get_embedding(
        self,
        event_id: str,
        model_version: str,
        revision_number: Optional[int] = None,
    ) -> Optional[List[float]]:
        """Retrieve vector embedding matching exact model version (and revision if requested)."""
        conn = self._get_connection()
        try:
            if revision_number is not None:
                cur = conn.execute(
                    """
                    SELECT embedding_dim, vector_blob
                    FROM historical_event_revision_embeddings
                    WHERE event_id = ? AND revision_number = ? AND model_version = ?
                    """,
                    (event_id, revision_number, model_version),
                )
                row = cur.fetchone()
                if row:
                    dim, blob = row[0], row[1]
                    return list(struct.unpack(f"{dim}f", blob))
                return None

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

    def get_embeddings_batch(
        self,
        event_ids: List[str],
        model_version: str,
        revision_map: Optional[Dict[str, int]] = None,
    ) -> Dict[str, List[float]]:
        """
        Batch-fetch embeddings for candidate event IDs.
        When `revision_map` (`{event_id: revision_number}`) is provided, strictly resolves vectors
        from that specific bitemporal revision snapshot so newer revision vectors cannot leak backward.
        """
        if not event_ids:
            return {}
        conn = self._get_connection()
        try:
            out: Dict[str, List[float]] = {}
            if revision_map:
                placeholders = ",".join("?" for _ in event_ids)
                cur = conn.execute(
                    f"""
                    SELECT event_id, revision_number, embedding_dim, vector_blob
                    FROM historical_event_revision_embeddings
                    WHERE model_version = ? AND event_id IN ({placeholders})
                    """,
                    [model_version] + list(event_ids),
                )
                for eid, rev, dim, blob in cur.fetchall():
                    if revision_map.get(eid) == rev:
                        out[eid] = list(struct.unpack(f"{dim}f", blob))
                return out

            placeholders = ",".join("?" for _ in event_ids)
            sql = f"""
                SELECT event_id, embedding_dim, vector_blob
                FROM historical_event_embeddings
                WHERE model_version = ? AND event_id IN ({placeholders})
            """
            cur = conn.execute(sql, [model_version] + list(event_ids))
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
        """Persist a verified future market response record with explicit label_type and timestamps."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO historical_event_outcomes (
                        event_id, instrument_id, horizon_label, label_type,
                        outcome_start_ts, outcome_end_ts,
                        return_simple, return_log, benchmark_adjusted_return, benchmark_instrument,
                        benchmark_adjustment_method, realized_volatility, max_favorable_excursion,
                        max_adverse_excursion, spread_bps_avg, data_quality_status,
                        market_data_source, created_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        outcome.event_id,
                        outcome.instrument_id.strip().upper(),
                        outcome.horizon_label,
                        outcome.label_type,
                        to_utc_iso(outcome.outcome_start_ts),
                        to_utc_iso(outcome.outcome_end_ts),
                        outcome.return_simple,
                        outcome.return_log,
                        outcome.benchmark_adjusted_return,
                        outcome.benchmark_instrument,
                        outcome.benchmark_adjustment_method,
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
        label_type: Optional[str] = None,
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
        extra_filters: List[str] = []
        if horizon_label is not None:
            extra_filters.append("AND horizon_label = ?")
            params.append(horizon_label)
        if label_type is not None:
            extra_filters.append("AND label_type = ?")
            params.append(label_type)

        sql = f"""
            SELECT event_id, instrument_id, horizon_label, label_type,
                   outcome_start_ts, outcome_end_ts,
                   return_simple, return_log, benchmark_adjusted_return, benchmark_instrument,
                   benchmark_adjustment_method, realized_volatility, max_favorable_excursion,
                   max_adverse_excursion, spread_bps_avg, data_quality_status,
                   market_data_source, created_at_utc
            FROM historical_event_outcomes
            WHERE outcome_end_ts <= ? AND event_id IN ({placeholders}) {' '.join(extra_filters)}
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
                    label_type=row[3],
                    outcome_start_ts=parse_utc_iso(row[4]),
                    outcome_end_ts=parse_utc_iso(row[5]),
                    return_simple=row[6],
                    return_log=row[7],
                    benchmark_adjusted_return=row[8],
                    benchmark_instrument=row[9],
                    benchmark_adjustment_method=row[10],
                    realized_volatility=row[11],
                    max_favorable_excursion=row[12],
                    max_adverse_excursion=row[13],
                    spread_bps_avg=row[14],
                    data_quality_status=row[15],
                    market_data_source=row[16],
                    created_at_utc=parse_utc_iso(row[17]),
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
            total_rev_embeddings = conn.execute("SELECT COUNT(*) FROM historical_event_revision_embeddings").fetchone()[0]
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
                "total_revision_embeddings": total_rev_embeddings,
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
