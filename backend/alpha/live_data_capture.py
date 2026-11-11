"""Durable Point-in-Time Live Data Capture Store (Phase 6 WP-D).

Extends Phase 2 local-first architecture (SQLite WAL + compressed chunked `.jsonl.gz`
via `PartitionedMarketDataWriter` + `RawPayloadStore`) without external broker/cluster dependencies.

Captures immutably:
- Live news records & append-only article revision snapshots
- Market quotes, trades, snapshots & L2 summaries
- Provider connection transitions & feed-quality incidents (gaps/stale/drops)
- Durable ingestion checkpoints & daily data-quality reports

Supports causal point-in-time reconstruction (`reconstruct_state_as_of(decision_timestamp)`)
without overwriting historical observations or leaking future revisions.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple
from pydantic import BaseModel, Field

from backend.alpha.live_instruments import INSTRUMENT_MASTER_VERSION
from backend.alpha.market_data import FeedIntegrityAnomaly
from backend.alpha.schemas import MarketEvent, NewsEvent
from backend.alpha.storage import PartitionedMarketDataWriter
from backend.alpha.timestamps import get_current_utc, parse_utc_iso, to_utc_iso

CAPTURE_SCHEMA_VERSION = "6.0.0-alpha"


class AsOfCapturedSnapshot(BaseModel):
    """Exact state observable to the system strictly at or before `decision_timestamp`."""

    decision_timestamp_utc: datetime
    news_events: List[NewsEvent] = Field(default_factory=list)
    market_events: List[MarketEvent] = Field(default_factory=list)
    feed_incidents: List[Dict[str, Any]] = Field(default_factory=list)


class CaptureStorageStats(BaseModel):
    """Disk and row-level telemetry for `LiveCaptureStore`."""

    db_size_bytes: int
    wal_size_bytes: int
    total_news_events: int
    total_news_revisions: int
    total_market_snapshots: int
    total_connection_events: int
    total_feed_incidents: int
    total_quality_reports: int
    partitioned_jsonl_gz_files: int
    partitioned_jsonl_gz_bytes: int


class LiveCaptureStore:
    """SQLite WAL + Partitioned `.jsonl.gz` live capture engine with immutable revision history."""

    def __init__(
        self,
        db_path: str | Path = "data/alpha_live_capture.db",
        market_events_dir: str | Path = "data/live_market_events",
        buffer_size: int = 250,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.market_writer = PartitionedMarketDataWriter(
            base_dir=str(market_events_dir),
            buffer_size=buffer_size,
        )
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS live_news_events (
                    event_id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    latest_content_hash TEXT NOT NULL,
                    latest_revision INTEGER NOT NULL,
                    first_received_at_utc TEXT NOT NULL,
                    last_received_at_utc TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    instrument_master_version TEXT NOT NULL,
                    is_synthetic INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS live_news_revisions (
                    event_id TEXT NOT NULL,
                    revision_number INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    headline TEXT NOT NULL,
                    published_at_utc TEXT,
                    published_ts_interpretation TEXT NOT NULL,
                    provider_observed_at_utc TEXT,
                    local_received_at_utc TEXT NOT NULL,
                    local_monotonic_ns INTEGER NOT NULL,
                    ingestion_completed_at_utc TEXT,
                    raw_payload_ref TEXT,
                    schema_version TEXT NOT NULL,
                    instrument_master_version TEXT NOT NULL,
                    is_synthetic INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    recorded_at_utc TEXT NOT NULL,
                    PRIMARY KEY (event_id, revision_number)
                );

                CREATE TRIGGER IF NOT EXISTS trg_no_update_live_news_revisions
                BEFORE UPDATE ON live_news_revisions
                BEGIN
                    SELECT RAISE(ABORT, 'IMMUTABLE_LEDGER: live_news_revisions rows cannot be updated');
                END;

                CREATE TRIGGER IF NOT EXISTS trg_no_delete_live_news_revisions
                BEFORE DELETE ON live_news_revisions
                BEGIN
                    SELECT RAISE(ABORT, 'IMMUTABLE_LEDGER: live_news_revisions rows cannot be deleted');
                END;

                CREATE TABLE IF NOT EXISTS live_market_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    provider TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    segment TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    contract_id TEXT,
                    message_type TEXT NOT NULL,
                    exchange_timestamp_utc TEXT,
                    provider_timestamp_utc TEXT,
                    local_received_at_utc TEXT NOT NULL,
                    local_monotonic_ns INTEGER NOT NULL,
                    sequence_number INTEGER,
                    bid_price REAL,
                    ask_price REAL,
                    last_traded_price REAL,
                    cumulative_volume INTEGER,
                    session_state TEXT,
                    schema_version TEXT NOT NULL,
                    instrument_master_version TEXT NOT NULL,
                    is_synthetic INTEGER NOT NULL DEFAULT 0,
                    event_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_live_mkt_inst_ts
                ON live_market_snapshots(instrument_id, local_received_at_utc);

                CREATE TRIGGER IF NOT EXISTS trg_no_update_live_market_snapshots
                BEFORE UPDATE ON live_market_snapshots
                BEGIN
                    SELECT RAISE(ABORT, 'IMMUTABLE_CAPTURE: live_market_snapshots cannot be modified');
                END;

                CREATE TABLE IF NOT EXISTS live_connection_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    details TEXT NOT NULL,
                    recorded_at_utc TEXT NOT NULL,
                    monotonic_ns INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS live_feed_incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id TEXT NOT NULL,
                    incident_type TEXT NOT NULL,
                    instrument_id TEXT,
                    expected_sequence INTEGER,
                    observed_sequence INTEGER,
                    gap_size INTEGER,
                    details TEXT NOT NULL,
                    recorded_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS live_checkpoints (
                    provider_id TEXT PRIMARY KEY,
                    cursor_json TEXT,
                    sequence_map_json TEXT,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS live_quality_reports (
                    report_id TEXT PRIMARY KEY,
                    generated_at_utc TEXT NOT NULL,
                    metrics_json TEXT NOT NULL
                );
                """
            )
            conn.commit()

    def record_news_event(
        self,
        event: NewsEvent,
        published_ts_interpretation: str = "EXPLICIT_OR_LOCALIZED_UTC",
        is_synthetic: bool = False,
        instrument_master_version: str = INSTRUMENT_MASTER_VERSION,
    ) -> Tuple[str, int]:
        """
        Persist news event preserving full immutable revision history.
        Returns `(status, revision_number)` where status is `INSERTED`, `REVISION_APPENDED`, or `SKIPPED_DUPLICATE`.
        """
        now_iso = to_utc_iso(get_current_utc()) or ""
        recv_iso = to_utc_iso(event.local_received_at_utc) or now_iso
        pub_iso = to_utc_iso(event.published_at_utc)
        prov_iso = to_utc_iso(event.provider_observed_at_utc)
        comp_iso = to_utc_iso(event.ingestion_completed_at_utc)
        syn_int = 1 if is_synthetic else 0
        payload_json = event.model_dump_json()

        with self._connect() as conn:
            row = conn.execute(
                "SELECT latest_content_hash, latest_revision FROM live_news_events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()

            if row is None:
                rev = max(1, int(event.version))
                conn.execute(
                    """
                    INSERT INTO live_news_events (
                        event_id, provider_id, source_name, source_url,
                        latest_content_hash, latest_revision,
                        first_received_at_utc, last_received_at_utc,
                        schema_version, instrument_master_version, is_synthetic
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.provider_id,
                        event.source_name,
                        event.source_url,
                        event.content_hash,
                        rev,
                        recv_iso,
                        recv_iso,
                        CAPTURE_SCHEMA_VERSION,
                        instrument_master_version,
                        syn_int,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO live_news_revisions (
                        event_id, revision_number, content_hash, headline,
                        published_at_utc, published_ts_interpretation,
                        provider_observed_at_utc, local_received_at_utc, local_monotonic_ns,
                        ingestion_completed_at_utc, raw_payload_ref,
                        schema_version, instrument_master_version, is_synthetic,
                        payload_json, recorded_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        rev,
                        event.content_hash,
                        event.headline,
                        pub_iso,
                        published_ts_interpretation,
                        prov_iso,
                        recv_iso,
                        event.local_monotonic_ns,
                        comp_iso,
                        event.raw_payload_ref,
                        CAPTURE_SCHEMA_VERSION,
                        instrument_master_version,
                        syn_int,
                        payload_json,
                        now_iso,
                    ),
                )
                conn.commit()
                return "INSERTED", rev

            existing_hash, current_rev = row[0], int(row[1])
            if existing_hash == event.content_hash:
                return "SKIPPED_DUPLICATE", current_rev

            new_rev = current_rev + 1
            conn.execute(
                """
                UPDATE live_news_events
                SET latest_content_hash = ?, latest_revision = ?, last_received_at_utc = ?
                WHERE event_id = ?
                """,
                (event.content_hash, new_rev, recv_iso, event.event_id),
            )
            conn.execute(
                """
                INSERT INTO live_news_revisions (
                    event_id, revision_number, content_hash, headline,
                    published_at_utc, published_ts_interpretation,
                    provider_observed_at_utc, local_received_at_utc, local_monotonic_ns,
                    ingestion_completed_at_utc, raw_payload_ref,
                    schema_version, instrument_master_version, is_synthetic,
                    payload_json, recorded_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    new_rev,
                    event.content_hash,
                    event.headline,
                    pub_iso,
                    published_ts_interpretation,
                    prov_iso,
                    recv_iso,
                    event.local_monotonic_ns,
                    comp_iso,
                    event.raw_payload_ref,
                    CAPTURE_SCHEMA_VERSION,
                    instrument_master_version,
                    syn_int,
                    payload_json,
                    now_iso,
                ),
            )
            conn.commit()
            return "REVISION_APPENDED", new_rev

    def record_market_event(
        self,
        event: MarketEvent,
        is_synthetic: bool = False,
        instrument_master_version: str = INSTRUMENT_MASTER_VERSION,
        write_to_partitioned_archive: bool = True,
    ) -> bool:
        """Persist market quote/trade/snapshot into SQLite AND optionally `.jsonl.gz` partitions."""
        recv_iso = to_utc_iso(event.local_received_at_utc) or to_utc_iso(get_current_utc()) or ""
        exch_iso = to_utc_iso(event.exchange_timestamp_utc)
        prov_iso = to_utc_iso(event.provider_timestamp_utc)
        syn_int = 1 if is_synthetic else 0

        if write_to_partitioned_archive:
            self.market_writer.append(event)

        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO live_market_snapshots (
                    event_id, provider, venue, segment, instrument_id, contract_id,
                    message_type, exchange_timestamp_utc, provider_timestamp_utc,
                    local_received_at_utc, local_monotonic_ns, sequence_number,
                    bid_price, ask_price, last_traded_price, cumulative_volume, session_state,
                    schema_version, instrument_master_version, is_synthetic, event_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.provider,
                    event.venue,
                    event.segment,
                    event.instrument_id,
                    event.contract_id,
                    event.message_type.value,
                    exch_iso,
                    prov_iso,
                    recv_iso,
                    event.local_monotonic_ns,
                    event.sequence_number,
                    float(event.bid_price) if event.bid_price is not None else None,
                    float(event.ask_price) if event.ask_price is not None else None,
                    float(event.last_traded_price) if event.last_traded_price is not None else None,
                    event.cumulative_volume,
                    event.session_state,
                    CAPTURE_SCHEMA_VERSION,
                    instrument_master_version,
                    syn_int,
                    event.model_dump_json(),
                ),
            )
            conn.commit()
            return cur.rowcount > 0

    def flush_market_archive(self) -> List[Path]:
        return self.market_writer.flush_all()

    def record_connection_event(self, provider_id: str, state: str, details: str) -> None:
        now_iso = to_utc_iso(get_current_utc()) or ""
        mono = 0
        from backend.alpha.timestamps import get_monotonic_ns

        mono = get_monotonic_ns()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO live_connection_events (provider_id, state, details, recorded_at_utc, monotonic_ns) VALUES (?, ?, ?, ?, ?)",
                (provider_id, state, details, now_iso, mono),
            )
            conn.commit()

    def record_feed_incident(
        self,
        provider_id: str,
        incident_type: str,
        details: str,
        instrument_id: Optional[str] = None,
        expected_sequence: Optional[int] = None,
        observed_sequence: Optional[int] = None,
        gap_size: Optional[int] = None,
    ) -> None:
        now_iso = to_utc_iso(get_current_utc()) or ""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO live_feed_incidents (
                    provider_id, incident_type, instrument_id,
                    expected_sequence, observed_sequence, gap_size, details, recorded_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider_id,
                    incident_type,
                    instrument_id,
                    expected_sequence,
                    observed_sequence,
                    gap_size,
                    details,
                    now_iso,
                ),
            )
            conn.commit()

    def record_anomaly(self, provider_id: str, anomaly: FeedIntegrityAnomaly) -> None:
        self.record_feed_incident(
            provider_id=provider_id,
            incident_type=anomaly.anomaly_type,
            instrument_id=anomaly.instrument_id,
            expected_sequence=anomaly.expected_sequence,
            observed_sequence=anomaly.observed_sequence,
            gap_size=anomaly.gap_size,
            details=anomaly.details,
        )

    def save_checkpoint(
        self,
        provider_id: str,
        cursor_json: Optional[str] = None,
        sequence_map: Optional[Dict[str, int]] = None,
    ) -> None:
        now_iso = to_utc_iso(get_current_utc()) or ""
        seq_str = json.dumps(sequence_map, sort_keys=True) if sequence_map is not None else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO live_checkpoints (provider_id, cursor_json, sequence_map_json, updated_at_utc)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                    cursor_json = COALESCE(excluded.cursor_json, live_checkpoints.cursor_json),
                    sequence_map_json = COALESCE(excluded.sequence_map_json, live_checkpoints.sequence_map_json),
                    updated_at_utc = excluded.updated_at_utc
                """,
                (provider_id, cursor_json, seq_str, now_iso),
            )
            conn.commit()

    def load_checkpoint(self, provider_id: str) -> Tuple[Optional[str], Dict[str, int]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT cursor_json, sequence_map_json FROM live_checkpoints WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
        if not row:
            return None, {}
        seq_map = json.loads(row[1]) if row[1] else {}
        return row[0], {str(k): int(v) for k, v in seq_map.items()}

    def save_quality_report(self, report_id: str, metrics: Dict[str, Any]) -> None:
        now_iso = to_utc_iso(get_current_utc()) or ""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO live_quality_reports (report_id, generated_at_utc, metrics_json) VALUES (?, ?, ?)",
                (report_id, now_iso, json.dumps(metrics, sort_keys=True, default=str)),
            )
            conn.commit()

    def reconstruct_state_as_of(
        self,
        decision_timestamp: datetime,
        instrument_id: Optional[str] = None,
        include_synthetic: bool = False,
    ) -> AsOfCapturedSnapshot:
        """
        Reconstruct exact causal news revisions and market observations available at `<= decision_timestamp`.
        Never returns revisions or ticks arriving after `decision_timestamp`.
        """
        cutoff_iso = to_utc_iso(decision_timestamp) or ""
        syn_clause = "" if include_synthetic else " AND is_synthetic = 0"

        with self._connect() as conn:
            # Latest revision observed <= decision_timestamp per event_id
            rev_rows = conn.execute(
                f"""
                WITH ranked AS (
                    SELECT payload_json,
                           ROW_NUMBER() OVER (
                               PARTITION BY event_id ORDER BY revision_number DESC
                           ) AS rn
                    FROM live_news_revisions
                    WHERE local_received_at_utc <= ?{syn_clause}
                )
                SELECT payload_json FROM ranked WHERE rn = 1
                """,
                (cutoff_iso,),
            ).fetchall()

            mkt_sql = f"""
                SELECT event_json FROM live_market_snapshots
                WHERE local_received_at_utc <= ?{syn_clause}
            """
            mkt_params: List[Any] = [cutoff_iso]
            if instrument_id is not None:
                mkt_sql += " AND instrument_id = ?"
                mkt_params.append(instrument_id)
            mkt_sql += " ORDER BY local_received_at_utc ASC, id ASC"
            mkt_rows = conn.execute(mkt_sql, mkt_params).fetchall()

            inc_rows = conn.execute(
                "SELECT provider_id, incident_type, instrument_id, details, recorded_at_utc FROM live_feed_incidents WHERE recorded_at_utc <= ? ORDER BY id ASC",
                (cutoff_iso,),
            ).fetchall()

        news_out = [NewsEvent.model_validate_json(r[0]) for r in rev_rows]
        news_out.sort(key=lambda e: e.local_received_at_utc)
        mkt_out = [MarketEvent.model_validate_json(r[0]) for r in mkt_rows]
        incidents = [
            {
                "provider_id": r[0],
                "incident_type": r[1],
                "instrument_id": r[2],
                "details": r[3],
                "recorded_at_utc": r[4],
            }
            for r in inc_rows
        ]
        return AsOfCapturedSnapshot(
            decision_timestamp_utc=decision_timestamp,
            news_events=news_out,
            market_events=mkt_out,
            feed_incidents=incidents,
        )

    def get_news_revisions(self, event_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT revision_number, content_hash, headline, published_at_utc,
                       published_ts_interpretation, local_received_at_utc, recorded_at_utc
                FROM live_news_revisions
                WHERE event_id = ?
                ORDER BY revision_number ASC
                """,
                (event_id,),
            ).fetchall()
        return [
            {
                "revision_number": r[0],
                "content_hash": r[1],
                "headline": r[2],
                "published_at_utc": r[3],
                "published_ts_interpretation": r[4],
                "local_received_at_utc": r[5],
                "recorded_at_utc": r[6],
            }
            for r in rows
        ]

    def get_storage_stats(self) -> CaptureStorageStats:
        wal_path = Path(str(self.db_path) + "-wal")
        db_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
        wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0

        gz_files = list(self.market_writer.base_dir.rglob("*.jsonl.gz")) if self.market_writer.base_dir.exists() else []
        gz_bytes = sum(f.stat().st_size for f in gz_files)

        with self._connect() as conn:
            n_news = conn.execute("SELECT COUNT(*) FROM live_news_events").fetchone()[0]
            n_revs = conn.execute("SELECT COUNT(*) FROM live_news_revisions").fetchone()[0]
            n_mkt = conn.execute("SELECT COUNT(*) FROM live_market_snapshots").fetchone()[0]
            n_conn = conn.execute("SELECT COUNT(*) FROM live_connection_events").fetchone()[0]
            n_inc = conn.execute("SELECT COUNT(*) FROM live_feed_incidents").fetchone()[0]
            n_qrep = conn.execute("SELECT COUNT(*) FROM live_quality_reports").fetchone()[0]

        return CaptureStorageStats(
            db_size_bytes=db_bytes,
            wal_size_bytes=wal_bytes,
            total_news_events=int(n_news),
            total_news_revisions=int(n_revs),
            total_market_snapshots=int(n_mkt),
            total_connection_events=int(n_conn),
            total_feed_incidents=int(n_inc),
            total_quality_reports=int(n_qrep),
            partitioned_jsonl_gz_files=len(gz_files),
            partitioned_jsonl_gz_bytes=gz_bytes,
        )
