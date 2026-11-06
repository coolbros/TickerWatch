"""Connector registry managing lifecycle, discovery and configuration of source connectors."""

import logging
from pathlib import Path
from typing import Dict, List, Optional
import yaml

from backend.config import Settings, get_settings
from backend.connectors.base import BaseConnector
from backend.connectors.rss import RSSConnector
from backend.connectors.sec import SECConnector

logger = logging.getLogger(__name__)


class ConnectorRegistry:
    """Central registry and factory for all active and configured connectors."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._connectors: Dict[str, BaseConnector] = {}

    def register(self, connector: BaseConnector) -> None:
        """Register a connector instance."""
        self._connectors[connector.connector_id] = connector
        logger.info(
            "Registered connector",
            extra={"connector_id": connector.connector_id, "connector_name": connector.name, "enabled": connector.enabled}
        )

    def get(self, connector_id: str) -> Optional[BaseConnector]:
        """Lookup connector by ID."""
        return self._connectors.get(connector_id)

    def list_all(self) -> List[BaseConnector]:
        """Return all registered connectors."""
        return list(self._connectors.values())

    def list_enabled(self) -> List[BaseConnector]:
        """Return only active, enabled connectors."""
        return [c for c in self._connectors.values() if c.enabled]

    def load_from_config(self) -> None:
        """Discover and instantiate all configured connectors from settings and YAML."""
        # 1. Load RSS Feeds from YAML config
        if self.settings.rss_enabled:
            feeds_path = Path(self.settings.rss_feeds_config)
            if not feeds_path.is_absolute():
                # Resolve relative to working directory or project root
                feeds_path = Path.cwd() / feeds_path

            if feeds_path.exists():
                try:
                    with open(feeds_path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                    feeds = data.get("feeds", [])
                    for feed in feeds:
                        feed_id = feed.get("id")
                        feed_url = feed.get("url")
                        if not feed_id or not feed_url:
                            continue
                        connector = RSSConnector(
                            connector_id=feed_id,
                            feed_url=feed_url,
                            name=feed.get("name"),
                            poll_interval_seconds=feed.get("poll_interval_seconds", self.settings.rss_default_poll_interval_seconds),
                            tickers=feed.get("tickers", []),
                            language=feed.get("language", "en"),
                            enabled=True,
                        )
                        self.register(connector)
                except Exception as ex:
                    logger.error("Failed to load RSS feeds configuration", extra={"error": str(ex), "path": str(feeds_path)})
            else:
                logger.warning("RSS feeds config file not found", extra={"path": str(feeds_path)})

        # 2. Load SEC EDGAR Submissions Connector if enabled
        if self.settings.sec_enabled:
            tracked_dict = self.settings.get_sec_tracked_dict()
            sec_connector = SECConnector(
                connector_id="sec_edgar",
                name="SEC EDGAR Submissions",
                user_agent=self.settings.sec_user_agent,
                tracked_ciks=tracked_dict,
                enabled=self.settings.sec_enabled,
                poll_interval_seconds=self.settings.sec_poll_interval_seconds,
            )
            self.register(sec_connector)
