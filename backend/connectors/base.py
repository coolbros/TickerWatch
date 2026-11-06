"""Base connector abstraction for all TickerWatch information sources."""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

from backend.connectors.models import ConnectorHealth, ConnectorStatus, FetchBatch


class BaseConnector(ABC):
    """
    Abstract interface for all information source connectors.
    Every provider (RSS, SEC EDGAR, exchange announcements) must implement this contract.
    """

    def __init__(
        self,
        connector_id: str,
        name: str,
        enabled: bool = True,
        poll_interval_seconds: int = 300,
    ) -> None:
        self.connector_id = connector_id
        self.name = name
        self.enabled = enabled
        self.poll_interval_seconds = poll_interval_seconds

        # State tracking for health reporting
        self.last_attempted_at_utc: Optional[datetime] = None
        self.last_successful_at_utc: Optional[datetime] = None
        self.last_observed_item_at_utc: Optional[datetime] = None
        self.consecutive_failures: int = 0
        self.last_error: Optional[str] = None
        self.status: ConnectorStatus = ConnectorStatus.HEALTHY if enabled else ConnectorStatus.DISABLED
        self.committed_cursor: Optional[str] = None

    @abstractmethod
    async def fetch(self, cursor: Optional[str] = None) -> FetchBatch:
        """
        Fetch a batch of source items starting from the provided cursor.

        Args:
            cursor: Most recently acknowledged cursor, or None if starting fresh.

        Returns:
            FetchBatch containing parsed SourceItems, next_cursor, fetched_at_utc.
        """
        raise NotImplementedError

    @abstractmethod
    async def ack(self, cursor: str) -> None:
        """
        Acknowledge cursor advancement.
        Must ONLY be called after the batch data has been durably committed to persistence.
        """
        raise NotImplementedError

    async def health(self) -> ConnectorHealth:
        """
        Return connector health state.
        Computes STALE condition if a healthy feed has not observed updates in an extended window.
        """
        current_status = self.status
        now = datetime.now(timezone.utc)

        if not self.enabled:
            current_status = ConnectorStatus.DISABLED
        elif self.consecutive_failures >= 3:
            current_status = ConnectorStatus.DEGRADED
        elif self.last_successful_at_utc:
            # If last successful contact was more than 3x poll intervals ago, flag as STALE
            stale_threshold_seconds = max(self.poll_interval_seconds * 3, 900)
            if (now - self.last_successful_at_utc).total_seconds() > stale_threshold_seconds:
                current_status = ConnectorStatus.STALE

        return ConnectorHealth(
            connector_id=self.connector_id,
            name=self.name,
            enabled=self.enabled,
            status=current_status,
            last_attempted_at_utc=self.last_attempted_at_utc,
            last_successful_at_utc=self.last_successful_at_utc,
            last_observed_item_at_utc=self.last_observed_item_at_utc,
            consecutive_failures=self.consecutive_failures,
            last_error=self.last_error,
            poll_interval_seconds=self.poll_interval_seconds,
        )

    def record_attempt(self) -> None:
        """Record that a fetch attempt is starting."""
        self.last_attempted_at_utc = datetime.now(timezone.utc)

    def record_success(self, items_count: int = 0) -> None:
        """Record successful completion of a fetch operation."""
        now = datetime.now(timezone.utc)
        self.last_successful_at_utc = now
        self.consecutive_failures = 0
        self.last_error = None
        self.status = ConnectorStatus.HEALTHY
        if items_count > 0:
            self.last_observed_item_at_utc = now

    def record_failure(self, error_message: str, is_auth: bool = False) -> None:
        """Record a failure for health tracking and backoff determination."""
        self.consecutive_failures += 1
        self.last_error = error_message
        if is_auth:
            self.status = ConnectorStatus.AUTH_ERROR
        elif self.consecutive_failures >= 3:
            self.status = ConnectorStatus.DEGRADED
