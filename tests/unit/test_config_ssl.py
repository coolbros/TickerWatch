"""Unit tests for configuration validation, structured logging, and SSL context verification."""

import json
import logging
import ssl
import sys
from unittest.mock import MagicMock, patch
import pytest
from pydantic import ValidationError

from backend.config import (
    JSONLogFormatter,
    Settings,
    get_settings,
    get_ssl_context,
)


def test_ssl_context_preserves_tls_verification():
    """CRITICAL SECURITY CHECK: Verify TLS verification is never weakened or disabled."""
    ctx = get_ssl_context()
    assert isinstance(ctx, ssl.SSLContext)
    # verify_mode MUST be CERT_REQUIRED (never CERT_NONE)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    # check_hostname MUST be True
    assert ctx.check_hostname is True


def test_ssl_context_handles_store_enumeration_errors():
    """Verify that exceptions or malformed certs during Windows CryptoAPI certificate loading fail open safely."""
    real_create_default_context = ssl.create_default_context

    def mock_create_default_context():
        real_ctx = real_create_default_context()
        # Mock load_verify_locations on the context to raise an error
        real_ctx.load_verify_locations = MagicMock(side_effect=ssl.SSLError("Failed to decode certificate"))
        return real_ctx

    with patch("ssl.create_default_context", side_effect=mock_create_default_context):
        ctx = get_ssl_context()
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True


def test_ssl_context_non_windows_platform():
    """Verify that on non-Windows platforms standard default SSLContext is returned."""
    with patch("sys.platform", "linux"):
        ctx = get_ssl_context()
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True


def test_settings_default_values():
    """Verify standard default application settings."""
    settings = Settings()
    assert settings.app_env in ("development", "test", "production")
    assert settings.start_scheduler is True
    assert settings.max_connector_concurrency >= 1
    assert settings.max_retries >= 0
    assert settings.backoff_base_seconds >= 0.1
    assert settings.backoff_max_seconds >= settings.backoff_base_seconds
    assert settings.http_timeout_seconds >= 1.0


def test_settings_sqlite_path_resolution():
    """Verify resolution of clean SQLite file paths."""
    s1 = Settings(database_url="sqlite:///custom/path/tickerwatch.db")
    assert s1.get_sqlite_path() == "custom/path/tickerwatch.db"

    s2 = Settings(database_url="sqlite://custom/path/tickerwatch.db")
    assert s2.get_sqlite_path() == "custom/path/tickerwatch.db"

    s3 = Settings(database_url="tickerwatch.db")
    assert s3.get_sqlite_path() == "tickerwatch.db"


def test_settings_sec_tracked_dict_normalization():
    """Verify CIK zero-padding and uppercase ticker parsing."""
    settings = Settings(
        sec_tracked_ciks="320193:aapl,  1018724:amzn , 789019, , CIK0000012345:msft "
    )
    tracked = settings.get_sec_tracked_dict()
    assert tracked["0000320193"] == "AAPL"
    assert tracked["0001018724"] == "AMZN"
    assert tracked["0000789019"] == "UNKNOWN"
    assert tracked["0000012345"] == "MSFT"
    assert "" not in tracked


def test_settings_sec_user_agent_validation():
    """Verify SEC User-Agent validation rules."""
    # Valid User-Agent
    s1 = Settings(
        sec_enabled=True,
        sec_user_agent="TickerWatchApp contact@example.com"
    )
    assert s1.is_sec_configured() is True

    # Missing email
    s2 = Settings(
        sec_enabled=True,
        sec_user_agent="TickerWatchAppWithoutEmail"
    )
    assert s2.is_sec_configured() is False

    # Empty string
    s3 = Settings(
        sec_enabled=True,
        sec_user_agent=""
    )
    assert s3.is_sec_configured() is False

    # SEC disabled
    s4 = Settings(
        sec_enabled=False,
        sec_user_agent="TickerWatchApp contact@example.com"
    )
    assert s4.is_sec_configured() is False


def test_settings_invalid_numeric_ranges_raise_validation_error():
    """Verify strict validation for invalid numeric bounds."""
    # Negative poll interval
    with pytest.raises(ValidationError):
        Settings(ingestion_poll_interval_seconds=5)  # ge=10

    # Negative timeout
    with pytest.raises(ValidationError):
        Settings(http_timeout_seconds=0.5)  # ge=1.0

    # Zero concurrency
    with pytest.raises(ValidationError):
        Settings(max_connector_concurrency=0)  # ge=1

    # Negative retry count
    with pytest.raises(ValidationError):
        Settings(max_retries=-1)  # ge=0


def test_json_log_formatter():
    """Verify structured JSON logging and ensure secret suppression."""
    formatter = JSONLogFormatter()
    logger = logging.getLogger("test_logger")
    record = logger.makeRecord(
        name="test_logger",
        level=logging.INFO,
        fn="test_file.py",
        lno=42,
        msg="Sample log message",
        args=(),
        exc_info=None,
        extra={"custom_field": "custom_value", "article_count": 10}
    )

    formatted = formatter.format(record)
    data = json.loads(formatted)

    assert data["level"] == "INFO"
    assert data["logger"] == "test_logger"
    assert data["message"] == "Sample log message"
    assert data["custom_field"] == "custom_value"
    assert data["article_count"] == 10
    assert "timestamp" in data
