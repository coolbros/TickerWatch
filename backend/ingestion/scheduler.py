"""Independent connector polling scheduler with concurrency bounding and graceful shutdown."""

import asyncio
import logging
from typing import Dict, List, Optional

from backend.connectors.base import BaseConnector
from backend.ingestion.orchestrator import IngestionOrchestrator

logger = logging.getLogger(__name__)


class IngestionScheduler:
    """
    Manages autonomous, non-blocking polling loops for all active connectors.
    Ensures:
    - Independent connector failure isolation (one failure never halts others).
    - Concurrency bounded via Semaphore.
    - Graceful cancellation and task shutdown.
    """

    def __init__(
        self,
        orchestrator: IngestionOrchestrator,
        max_concurrency: int = 2,
    ) -> None:
        self.orchestrator = orchestrator
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.shutdown_event = asyncio.Event()
        self.tasks: Dict[str, asyncio.Task] = {}

    async def _connector_loop(self, connector: BaseConnector) -> None:
        """Isolated polling loop for an individual connector."""
        connector_id = connector.connector_id
        logger.info(
            "Starting autonomous polling loop for connector",
            extra={"connector_id": connector_id, "interval_seconds": connector.poll_interval_seconds}
        )

        while not self.shutdown_event.is_set():
            try:
                async with self.semaphore:
                    await self.orchestrator.ingest_connector(connector)
            except asyncio.CancelledError:
                logger.info("Connector loop cancelled", extra={"connector_id": connector_id})
                break
            except Exception as exc:
                logger.error(
                    "Unexpected exception in connector polling loop",
                    extra={"connector_id": connector_id, "error": str(exc)}
                )

            # Wait for the configured polling interval, or awake early on shutdown
            try:
                await asyncio.wait_for(
                    self.shutdown_event.wait(),
                    timeout=float(connector.poll_interval_seconds),
                )
                break  # If shutdown_event fired, exit loop
            except asyncio.TimeoutError:
                # Interval elapsed normally, proceed to next cycle
                continue

    def start_connectors(self, connectors: List[BaseConnector]) -> None:
        """Spawn independent background polling tasks for enabled connectors."""
        self.shutdown_event.clear()
        for connector in connectors:
            if not connector.enabled:
                logger.info("Skipping disabled connector", extra={"connector_id": connector.connector_id})
                continue
            if connector.connector_id in self.tasks and not self.tasks[connector.connector_id].done():
                continue

            task = asyncio.create_task(
                self._connector_loop(connector),
                name=f"poll_{connector.connector_id}"
            )
            self.tasks[connector.connector_id] = task

    async def stop(self, timeout_seconds: float = 5.0) -> None:
        """Signal all loops to stop and gracefully wait for ongoing iterations."""
        logger.info("Signalling ingestion scheduler shutdown...")
        self.shutdown_event.set()

        running_tasks = [t for t in self.tasks.values() if not t.done()]
        if running_tasks:
            for t in running_tasks:
                t.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(*running_tasks, return_exceptions=True),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                logger.warning("Some ingestion tasks did not stop within graceful shutdown timeout")

        self.tasks.clear()
        logger.info("Ingestion scheduler stopped cleanly")
