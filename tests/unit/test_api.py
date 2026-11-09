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
    monkeypatch.setenv("START_SCHEDULER", "false")

    from backend.config import get_settings
    settings = get_settings(reload=True)

    app = create_app(settings=settings, start_scheduler=False)
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


def test_get_dashboard(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "TickerWatch" in response.text
    assert "Data Ingestion Pipelines" in response.text


def test_get_single_connector_health(client, test_app):
    # Register a known test connector
    class DummyConnector(RSSConnector):
        pass

    dummy = DummyConnector(connector_id="dummy_health_conn", feed_url="https://example.com/feed.xml")
    router.registry.register(dummy)

    response = client.get("/connectors/dummy_health_conn/health")
    assert response.status_code == 200
    data = response.json()
    assert data["connector_id"] == "dummy_health_conn"
    assert data["status"] == "HEALTHY"


def test_get_single_connector_health_not_found(client):
    response = client.get("/connectors/nonexistent_connector/health")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_trigger_connector_success(client, test_app):
    class MockTriggerConnector(RSSConnector):
        async def fetch(self, cursor=None):
            from backend.connectors.models import FetchBatch
            return FetchBatch(
                connector_id=self.connector_id,
                items=[],
                next_cursor="cur_trig",
                source_status="NOT_MODIFIED"
            )

    mock_conn = MockTriggerConnector(connector_id="mock_trigger_conn", feed_url="https://example.com/trig.xml")
    router.registry.register(mock_conn)

    response = client.post("/connectors/mock_trigger_conn/trigger")
    assert response.status_code == 200
    data = response.json()
    assert data["connector_id"] == "mock_trigger_conn"
    assert data["status"] == "SUCCESS"


def test_trigger_connector_not_found(client):
    response = client.post("/connectors/nonexistent_connector/trigger")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_lifespan_starts_and_stops_scheduler_cleanly(tmp_path, monkeypatch):
    """Verify that when start_scheduler=True, lifespan starts connectors and shuts down cleanly."""
    db_file = tmp_path / "lifespan_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.setenv("RSS_ENABLED", "false")
    monkeypatch.setenv("SEC_ENABLED", "false")
    monkeypatch.setenv("START_SCHEDULER", "true")

    from backend.config import get_settings
    settings = get_settings(reload=True)

    app = create_app(settings=settings, start_scheduler=True)

    # Use TestClient as context manager to run lifespan startup and shutdown
    with TestClient(app) as test_client:
        res = test_client.get("/health")
        assert res.status_code == 200
        # Scheduler is initialized
        assert router.scheduler is not None

    # After exit from TestClient context, scheduler must have stopped cleanly
    assert router.scheduler.shutdown_event.is_set()


@respx.mock
def test_api_client_makes_zero_external_requests(client):
    """
    Verify that standard API operations with TestClient make ZERO outbound HTTP requests.
    respx.mock without routes will raise an error if any HTTP request is attempted.
    """
    res_health = client.get("/health")
    assert res_health.status_code == 200

    res_connectors = client.get("/connectors")
    assert res_connectors.status_code == 200

    res_articles = client.get("/articles")
    assert res_articles.status_code == 200

    res_stats = client.get("/stats/ingestion")
    assert res_stats.status_code == 200


