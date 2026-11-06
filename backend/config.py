"""Configuration and structured logging for TickerWatch."""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class JSONLogFormatter(logging.Formatter):
    """Structured JSON log formatter for production observability."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include exception info if present
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        # Include custom extra fields if provided
        for key, val in record.__dict__.items():
            if key not in {
                "args", "asctime", "created", "exc_info", "exc_text",
                "filename", "funcName", "levelname", "levelno", "lineno",
                "module", "msecs", "message", "msg", "name", "pathname",
                "process", "processName", "relativeCreated", "stack_info",
                "thread", "threadName"
            } and not key.startswith("_"):
                try:
                    # Verify serializability
                    json.dumps(val)
                    log_obj[key] = val
                except (TypeError, ValueError):
                    log_obj[key] = str(val)

        return json.dumps(log_obj, ensure_ascii=False)


class Settings(BaseSettings):
    """Application settings with strict validation and sensible defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # General App Environment
    app_env: str = Field(default="development", description="Environment: development, test, production")
    log_level: str = Field(default="INFO", description="Log level: DEBUG, INFO, WARNING, ERROR")

    # Database
    database_url: str = Field(
        default="sqlite:///tickerwatch.db",
        description="Database URL or SQLite path"
    )

    # Orchestrator & Polling
    ingestion_poll_interval_seconds: int = Field(default=300, ge=10)
    http_timeout_seconds: float = Field(default=20.0, ge=1.0)
    max_connector_concurrency: int = Field(default=2, ge=1)

    # Retry and Resilience
    max_retries: int = Field(default=5, ge=0)
    backoff_base_seconds: float = Field(default=2.0, ge=0.1)
    backoff_max_seconds: float = Field(default=60.0, ge=1.0)

    # Article Retention and Policy
    article_retention_days: int = Field(default=180, ge=1)
    store_full_content: bool = Field(default=False)

    # Generic RSS Connector
    rss_enabled: bool = Field(default=True)
    rss_feeds_config: str = Field(default="config/feeds.yaml")
    rss_default_poll_interval_seconds: int = Field(default=300, ge=10)

    # SEC Connector
    sec_enabled: bool = Field(default=True)
    sec_user_agent: str = Field(
        default="",
        description="Required by SEC EDGAR: e.g. TickerWatch/0.1 admin@example.com"
    )
    sec_tracked_ciks: str = Field(
        default="0000320193:AAPL,0001018724:AMZN,0000789019:MSFT",
        description="Comma-separated CIK:TICKER pairs"
    )
    sec_poll_interval_seconds: int = Field(default=600, ge=30)

    # Indian Market Feeds (NSE / BSE)
    nse_rss_enabled: bool = Field(default=False)
    nse_poll_interval_seconds: int = Field(default=300, ge=10)

    def get_sqlite_path(self) -> str:
        """Resolve clean local path for SQLite database."""
        url = self.database_url
        if url.startswith("sqlite:///"):
            return url[len("sqlite:///"):]
        if url.startswith("sqlite://"):
            return url[len("sqlite://"):]
        return url

    def get_sec_tracked_dict(self) -> Dict[str, str]:
        """Parse comma-separated CIK:TICKER into a dictionary."""
        tracked = {}
        for item in self.sec_tracked_ciks.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" in item:
                cik, ticker = item.split(":", 1)
                # Pad CIK to 10 digits as required by SEC EDGAR
                tracked[cik.strip().zfill(10)] = ticker.strip().upper()
            else:
                tracked[item.zfill(10)] = "UNKNOWN"
        return tracked

    @field_validator("sec_user_agent")
    @classmethod
    def validate_sec_user_agent(cls, v: str) -> str:
        # We don't fail immediately at parse if SEC is disabled, but store stripped string
        return v.strip()

    def is_sec_configured(self) -> bool:
        """Check if SEC connector has the required valid User-Agent with contact address."""
        if not self.sec_enabled:
            return False
        ua = self.sec_user_agent.strip()
        # SEC requires User-Agent format: 'Sample Company Name AdminContact@<sample company domain>.com'
        return bool(ua and "@" in ua and len(ua.split()) >= 2)


_settings: Settings | None = None


def get_settings(reload: bool = False) -> Settings:
    """Singleton getter for application settings."""
    global _settings
    if _settings is None or reload:
        _settings = Settings()
    return _settings


def setup_logging(level: str | None = None) -> None:
    """Configure root logger with structured JSON formatting."""
    log_lvl = level or get_settings().log_level.upper()
    numeric_level = getattr(logging, log_lvl, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Remove existing handlers to avoid duplicates
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(numeric_level)
    handler.setFormatter(JSONLogFormatter())
    root_logger.addHandler(handler)
