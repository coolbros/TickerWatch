"""Unit tests for FastAPI endpoints using TestClient."""

from datetime import datetime, timezone
from pathlib import Path
from fastapi.testclient import TestClient
import httpx
import pytest
import respx

from backend.api.routes import router
from backend.connectors.models import SourceItem
from backend.connectors.registry import ConnectorRegistry
from backend.connectors.rss import RSSConnector
from backend.database.connection import apply_migrations
from backend.database.repository import Repository
from backend.ingestion.orchestrator import IngestionOrchestrator
from backend.main import create_app

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def test_app(tmp_path, monkeypatch):
    db_file = tmp_path / "api_test.db"
    db_path = str(db_file)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("SEC_ENABLED", "false")  # keep minimal for unit test

    from backend.config import get_settings
    get_settings(reload=True)

    app = create_app()
    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


def test_get_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "connectors" in data


def test_get_connectors(client):
    response = client.get("/connectors")
    assert response.status_code == 200
    data = response.json()
    assert "connectors" in data


def test_get_ingestion_stats(client):
    response = client.get("/stats/ingestion")
    assert response.status_code == 200
    data = response.json()
    assert "total_articles" in data
    assert "total_fetched" in data


def test_get_articles_empty(client):
    response = client.get("/articles")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 0


def test_get_articles_populated(client, test_app):
    # Insert an article directly into the app's repo
    repo = router.repo
    item = SourceItem(
        connector_id="test_api_conn",
        source_item_id="api-item-1",
        publisher="API News",
        source_type="rss",
        title="Breaking API Headline",
        url="https://api.example.com/item1",
        published_at_utc=datetime.now(timezone.utc),
        observed_at_utc=datetime.now(timezone.utc),
        content_hash="apihash123",
        tickers=["AAPL"],
    )
    repo.save_batch_atomic("test_api_conn", [item], "cur1")

    # Fetch list
    res = client.get("/articles?ticker=AAPL")
    assert res.status_code == 200
    articles = res.json()
    assert len(articles) == 1
    art_id = articles[0]["id"]
    assert articles[0]["title"] == "Breaking API Headline"
    assert articles[0]["tickers"] == ["AAPL"]

    # Fetch individual
    res_single = client.get(f"/articles/{art_id}")
    assert res_single.status_code == 200
    assert res_single.json()["id"] == art_id


def test_get_article_not_found(client):
    response = client.get("/articles/999999")
    assert response.status_code == 404
