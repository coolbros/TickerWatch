"""Ingestion orchestrator coordinating fetch, validation, atomic persistence and cursor ack."""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from backend.connectors.base import BaseConnector
from backend.connectors.models import FetchBatch
from backend.database.repository import Repository
from backend.ingestion.retry import RetryPolicy, execute_with_retry

logger = logging.getLogger(__name__)


class IngestionOrchestrator:
    """
    Coordinates end-to-end ingestion lifecycle for connectors.
    Guarantees:
    - Never advances persisted cursor until items are committed to SQLite.
    - Cancellation safety during database transaction commits.
    - Comprehensive diagnostics telemetry recorded per run.
    """

    def __init__(
        self,
        repository: Repository,
        retry_policy: Optional[RetryPolicy] = None,
    ) -> None:
        self.repo = repository
        self.retry_policy = retry_policy or RetryPolicy()

    async def ingest_connector(self, connector: BaseConnector) -> Dict[str, Any]:
        """
        Execute a single ingestion cycle for a connector with full transaction safety.

        Returns:
            Dictionary containing metrics for the ingestion cycle.
        """
        connector_id = connector.connector_id
        start_time = datetime.now(timezone.utc)
        t0 = time.monotonic()

        self.repo.record_attempt_start(connector_id)

        # Step 1: Read the last durably committed cursor from database
        cursor = self.repo.get_connector_cursor(connector_id)

        try:
            # Step 2: Fetch batch with retry policy
            async def _fetch():
                return await connector.fetch(cursor)

            batch: FetchBatch = await execute_with_retry(
                _fetch,
                policy=self.retry_policy,
                operation_name=f"fetch_{connector_id}",
            )

            # Step 5 & 6 & 7 & 8: Durably persist items, advance DB cursor, and ack connector.
            # Entire operation is shielded from task cancellation to protect transactional integrity.
            async def _persist_and_ack() -> tuple[int, int]:
                ins, skp = await asyncio.to_thread(
                    self.repo.save_batch_atomic,
                    connector_id,
                    batch.items,
                    batch.next_cursor,
                )
                if batch.next_cursor:
                    await connector.ack(batch.next_cursor)
                return ins, skp

            inserted, skipped = await asyncio.shield(_persist_and_ack())

            duration_ms = (time.monotonic() - t0) * 1000.0
            completion_time = datetime.now(timezone.utc)

            # Record telemetry diagnostics
            self.repo.record_ingestion_attempt(
                connector_id=connector_id,
                start_time_utc=start_time,
                completion_time_utc=completion_time,
                number_fetched=len(batch.items),
                number_inserted=inserted,
                number_skipped=skipped,
                number_failed=0,
                retry_count=0,
                duration_ms=duration_ms,
            )

            logger.info(
                "Ingestion completed successfully",
                extra={
                    "connector_id": connector_id,
                    "fetched": len(batch.items),
                    "inserted": inserted,
                    "skipped": skipped,
                    "duration_ms": round(duration_ms, 2),
                }
            )

            return {
                "connector_id": connector_id,
                "status": "SUCCESS",
                "fetched": len(batch.items),
                "inserted": inserted,
                "skipped": skipped,
                "failed": 0,
                "duration_ms": duration_ms,
            }

        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000.0
            completion_time = datetime.now(timezone.utc)
            error_str = str(exc)

            # Mark connector failure state
            is_auth = "auth" in error_str.lower() or "user-agent" in error_str.lower()
            self.repo.record_failure(connector_id, error_str, is_auth=is_auth)

            self.repo.record_ingestion_attempt(
                connector_id=connector_id,
                start_time_utc=start_time,
                completion_time_utc=completion_time,
                number_fetched=0,
                number_inserted=0,
                number_skipped=0,
                number_failed=1,
                retry_count=self.retry_policy.max_retries,
                error_details=error_str,
                duration_ms=duration_ms,
            )

            logger.error(
                "Ingestion cycle failed for connector",
                extra={
                    "connector_id": connector_id,
                    "error": error_str,
                    "duration_ms": round(duration_ms, 2),
                }
            )

            return {
                "connector_id": connector_id,
                "status": "FAILED",
                "fetched": 0,
                "inserted": 0,
                "skipped": 0,
                "failed": 1,
                "error": error_str,
                "duration_ms": duration_ms,
            }
