"""Repository layer for atomic database transactions, idempotency and persistence."""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.connectors.models import SourceItem
from backend.database.connection import get_connection
from backend.database.models import (
    ArticleRow,
    ConnectorStateRow,
    DeadLetterRow,
    IngestionAttemptRow,
)


class Repository:
    """Encapsulates all database persistence, idempotency and transaction boundaries."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        return get_connection(self.db_path)

    def get_connector_cursor(self, connector_id: str) -> Optional[str]:
        """Fetch the durable, committed cursor for a connector."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT cursor FROM connector_state WHERE connector_id = ?;",
                (connector_id,)
            )
            row = cursor.fetchone()
            return row["cursor"] if row and row["cursor"] is not None else None

    def get_connector_state(self, connector_id: str) -> Optional[ConnectorStateRow]:
        """Retrieve the persisted state for a connector."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT connector_id, cursor, last_successful_fetch, last_attempted_fetch,
                          last_observed_item, consecutive_failures, status, last_error, updated_at_utc
                   FROM connector_state WHERE connector_id = ?;""",
                (connector_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return ConnectorStateRow(
                connector_id=row["connector_id"],
                cursor=row["cursor"],
                last_successful_fetch=row["last_successful_fetch"],
                last_attempted_fetch=row["last_attempted_fetch"],
                last_observed_item=row["last_observed_item"],
                consecutive_failures=row["consecutive_failures"],
                status=row["status"],
                last_error=row["last_error"],
                updated_at_utc=row["updated_at_utc"],
            )

    def get_all_connector_states(self) -> List[ConnectorStateRow]:
        """Retrieve all connector state rows."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT connector_id, cursor, last_successful_fetch, last_attempted_fetch,
                          last_observed_item, consecutive_failures, status, last_error, updated_at_utc
                   FROM connector_state ORDER BY connector_id ASC;"""
            )
            rows = cursor.fetchall()
            return [
                ConnectorStateRow(
                    connector_id=r["connector_id"],
                    cursor=r["cursor"],
                    last_successful_fetch=r["last_successful_fetch"],
                    last_attempted_fetch=r["last_attempted_fetch"],
                    last_observed_item=r["last_observed_item"],
                    consecutive_failures=r["consecutive_failures"],
                    status=r["status"],
                    last_error=r["last_error"],
                    updated_at_utc=r["updated_at_utc"],
                )
                for r in rows
            ]

    def record_attempt_start(self, connector_id: str) -> None:
        """Record the start of a fetch attempt in connector state."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            with conn:
                conn.execute(
                    """INSERT INTO connector_state (connector_id, last_attempted_fetch, updated_at_utc)
                       VALUES (?, ?, ?)
                       ON CONFLICT(connector_id) DO UPDATE SET
                           last_attempted_fetch = excluded.last_attempted_fetch,
                           updated_at_utc = excluded.updated_at_utc;""",
                    (connector_id, now_str, now_str)
                )

    def record_failure(self, connector_id: str, error_message: str, is_auth: bool = False) -> None:
        """Update failure count and status on connector error."""
        now_str = datetime.now(timezone.utc).isoformat()
        status = "AUTH_ERROR" if is_auth else "DEGRADED"
        with self._get_conn() as conn:
            with conn:
                conn.execute(
                    """INSERT INTO connector_state (connector_id, consecutive_failures, status, last_error, updated_at_utc)
                       VALUES (?, 1, ?, ?, ?)
                       ON CONFLICT(connector_id) DO UPDATE SET
                           consecutive_failures = connector_state.consecutive_failures + 1,
                           status = CASE WHEN excluded.status = 'AUTH_ERROR' THEN 'AUTH_ERROR'
                                         WHEN connector_state.consecutive_failures + 1 >= 3 THEN 'DEGRADED'
                                         ELSE connector_state.status END,
                           last_error = excluded.last_error,
                           updated_at_utc = excluded.updated_at_utc;""",
                    (connector_id, status, error_message, now_str)
                )

    def save_batch_atomic(
        self,
        connector_id: str,
        items: List[SourceItem],
        new_cursor: Optional[str],
    ) -> Tuple[int, int]:
        """
        DURABLE TRANSACTION GUARANTEE:
        Atomically persist all valid articles and advance connector cursor.
        Guarantees:
        1. If persistence crashes or fails, cursor advancement is not committed.
        2. Idempotency constraint (connector_id, source_item_id) suppresses duplicates safely.
        3. FTS5 search index is kept synchronized via database triggers.

        Returns:
            Tuple of (inserted_count, skipped_count).
        """
        inserted_count = 0
        skipped_count = 0
        now_str = datetime.now(timezone.utc).isoformat()
        last_item_time = None

        conn = self._get_conn()
        try:
            # Begin explicit immediate transaction to prevent concurrent write contention
            conn.execute("BEGIN IMMEDIATE;")

            for item in items:
                pub_utc_str = item.published_at_utc.isoformat() if item.published_at_utc else None
                obs_utc_str = item.observed_at_utc.isoformat()
                upd_utc_str = item.updated_at_utc.isoformat() if item.updated_at_utc else None
                metadata_str = json.dumps(item.raw_metadata, ensure_ascii=False)
                tickers_str = json.dumps(item.tickers)

                if pub_utc_str:
                    last_item_time = pub_utc_str
                elif not last_item_time:
                    last_item_time = obs_utc_str

                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO articles (
                           connector_id, source_item_id, publisher, source_type, title, url,
                           published_at_utc, published_at_original, observed_at_utc, updated_at_utc,
                           content_hash, snippet, raw_metadata, language, tickers, created_at_utc
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(connector_id, source_item_id) DO NOTHING;""",
                    (
                        connector_id,
                        item.source_item_id,
                        item.publisher,
                        item.source_type,
                        item.title,
                        item.url,
                        pub_utc_str,
                        item.published_at_original,
                        obs_utc_str,
                        upd_utc_str,
                        item.content_hash,
                        item.content_text,
                        metadata_str,
                        item.language,
                        tickers_str,
                        now_str,
                    )
                )

                if cursor.rowcount > 0:
                    inserted_count += 1
                else:
                    skipped_count += 1

            # Advance cursor atomically in the same transaction
            conn.execute(
                """INSERT INTO connector_state (
                       connector_id, cursor, last_successful_fetch, last_observed_item,
                       consecutive_failures, status, last_error, updated_at_utc
                   ) VALUES (?, ?, ?, ?, 0, 'HEALTHY', NULL, ?)
                   ON CONFLICT(connector_id) DO UPDATE SET
                       cursor = COALESCE(excluded.cursor, connector_state.cursor),
                       last_successful_fetch = excluded.last_successful_fetch,
                       last_observed_item = COALESCE(excluded.last_observed_item, connector_state.last_observed_item),
                       consecutive_failures = 0,
                       status = 'HEALTHY',
                       last_error = NULL,
                       updated_at_utc = excluded.updated_at_utc;""",
                (connector_id, new_cursor, now_str, last_item_time, now_str)
            )

            conn.commit()
            return inserted_count, skipped_count

        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def record_ingestion_attempt(
        self,
        connector_id: str,
        start_time_utc: datetime,
        completion_time_utc: datetime,
        number_fetched: int,
        number_inserted: int,
        number_skipped: int,
        number_failed: int,
        retry_count: int,
        error_details: Optional[str] = None,
        duration_ms: float = 0.0,
    ) -> None:
        """Log diagnostic ingestion telemetry for health audits."""
        with self._get_conn() as conn:
            with conn:
                conn.execute(
                    """INSERT INTO ingestion_attempts (
                           connector_id, start_time_utc, completion_time_utc, number_fetched,
                           number_inserted, number_skipped, number_failed, retry_count,
                           error_details, duration_ms
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                    (
                        connector_id,
                        start_time_utc.isoformat(),
                        completion_time_utc.isoformat(),
                        number_fetched,
                        number_inserted,
                        number_skipped,
                        number_failed,
                        retry_count,
                        error_details,
                        duration_ms,
                    )
                )

    def record_dead_letter(
        self,
        connector_id: str,
        source_item_id: Optional[str],
        error_category: str,
        error_message: str,
        raw_payload: Optional[str] = None,
        retry_count: int = 0,
    ) -> None:
        """Quarantine unprocessable items with bounded payload storage (max 64KB)."""
        now_str = datetime.now(timezone.utc).isoformat()
        bounded_payload = raw_payload[:65536] if raw_payload else None

        with self._get_conn() as conn:
            with conn:
                conn.execute(
                    """INSERT INTO dead_letter_items (
                           connector_id, source_item_id, error_category, error_message,
                           raw_payload, retry_count, first_failure_at_utc, last_failure_at_utc, retry_status
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'NEW');""",
                    (
                        connector_id,
                        source_item_id,
                        error_category,
                        error_message,
                        bounded_payload,
                        retry_count,
                        now_str,
                        now_str,
                    )
                )

    def get_articles(
        self,
        limit: int = 50,
        offset: int = 0,
        ticker: Optional[str] = None,
        connector_id: Optional[str] = None,
    ) -> List[ArticleRow]:
        """Fetch paginated articles with optional ticker or connector filtering."""
        query = "SELECT * FROM articles WHERE 1=1"
        params: List[Any] = []

        if connector_id:
            query += " AND connector_id = ?"
            params.append(connector_id)

        if ticker:
            # Tickers stored as JSON array string, e.g. ["AAPL", "MSFT"]
            query += " AND tickers LIKE ?"
            params.append(f'%"{ticker.upper()}"%')

        query += " ORDER BY observed_at_utc DESC LIMIT ? OFFSET ?;"
        params.extend([limit, offset])

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [
                ArticleRow(
                    id=r["id"],
                    connector_id=r["connector_id"],
                    source_item_id=r["source_item_id"],
                    publisher=r["publisher"],
                    source_type=r["source_type"],
                    title=r["title"],
                    url=r["url"],
                    published_at_utc=r["published_at_utc"],
                    published_at_original=r["published_at_original"],
                    observed_at_utc=r["observed_at_utc"],
                    updated_at_utc=r["updated_at_utc"],
                    content_hash=r["content_hash"],
                    snippet=r["snippet"],
                    raw_metadata=r["raw_metadata"],
                    language=r["language"],
                    tickers=r["tickers"],
                    created_at_utc=r["created_at_utc"],
                )
                for r in rows
            ]

    def get_article_by_id(self, article_id: int) -> Optional[ArticleRow]:
        """Fetch a single article by ID."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM articles WHERE id = ?;", (article_id,))
            r = cursor.fetchone()
            if not r:
                return None
            return ArticleRow(
                id=r["id"],
                connector_id=r["connector_id"],
                source_item_id=r["source_item_id"],
                publisher=r["publisher"],
                source_type=r["source_type"],
                title=r["title"],
                url=r["url"],
                published_at_utc=r["published_at_utc"],
                published_at_original=r["published_at_original"],
                observed_at_utc=r["observed_at_utc"],
                updated_at_utc=r["updated_at_utc"],
                content_hash=r["content_hash"],
                snippet=r["snippet"],
                raw_metadata=r["raw_metadata"],
                language=r["language"],
                tickers=r["tickers"],
                created_at_utc=r["created_at_utc"],
            )

    def get_ingestion_stats(self) -> Dict[str, Any]:
        """Aggregate high-level ingestion performance statistics."""
        with self._get_conn() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM articles;")
            total_articles = cursor.fetchone()[0]

            cursor.execute("SELECT connector_id, COUNT(*) FROM articles GROUP BY connector_id;")
            articles_by_connector = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute("""
                SELECT COUNT(*),
                       COALESCE(SUM(number_fetched), 0),
                       COALESCE(SUM(number_inserted), 0),
                       COALESCE(SUM(number_skipped), 0),
                       COALESCE(SUM(number_failed), 0)
                FROM ingestion_attempts;
            """)
            stats_row = cursor.fetchone()

            cursor.execute("SELECT COUNT(*) FROM dead_letter_items;")
            dead_letter_count = cursor.fetchone()[0]

            return {
                "total_articles": total_articles,
                "articles_by_connector": articles_by_connector,
                "total_ingestion_attempts": stats_row[0],
                "total_fetched": stats_row[1],
                "total_inserted": stats_row[2],
                "total_skipped": stats_row[3],
                "total_failed": stats_row[4],
                "dead_letter_count": dead_letter_count,
            }
