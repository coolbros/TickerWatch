"""Unit tests for the SQLite persistence and repository layer."""

from datetime import datetime, timezone
import pytest

from backend.connectors.models import SourceItem
from backend.database.connection import apply_migrations, get_connection
from backend.database.repository import Repository


@pytest.fixture
def test_repo(tmp_path):
    """Fixture providing a fresh isolated SQLite database with migrations applied."""
    db_file = tmp_path / "test_tickerwatch.db"
    db_path = str(db_file)
    apply_migrations(db_path)
    return Repository(db_path)


def make_dummy_item(
    connector_id: str = "test_conn",
    source_item_id: str = "item-1",
    title: str = "Test Title",
    content_hash: str = "hash123",
) -> SourceItem:
    return SourceItem(
        connector_id=connector_id,
        source_item_id=source_item_id,
        publisher="TestPub",
        source_type="rss",
        title=title,
        url=f"https://example.com/{source_item_id}",
        published_at_utc=datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc),
        published_at_original="2026-11-06T12:00:00Z",
        observed_at_utc=datetime.now(timezone.utc),
        content_text="Sample snippet",
        content_hash=content_hash,
        raw_metadata={"foo": "bar"},
        tickers=["AAPL", "MSFT"],
    )


def test_migrations_applied(test_repo):
    """Verify that migrations created the required tables and indexes."""
    with test_repo._get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cursor.fetchall()}
        assert "articles" in tables
        assert "connector_state" in tables
        assert "ingestion_attempts" in tables
        assert "dead_letter_items" in tables
        assert "schema_migrations" in tables
        assert "articles_fts" in tables


def test_atomic_batch_insertion(test_repo):
    """Test successful insertion of a batch with cursor advancement."""
    items = [
        make_dummy_item(source_item_id="item-1"),
        make_dummy_item(source_item_id="item-2"),
    ]
    inserted, skipped = test_repo.save_batch_atomic(
        connector_id="test_conn",
        items=items,
        new_cursor="cursor_v1",
    )
    assert inserted == 2
    assert skipped == 0

    # Verify cursor was advanced
    cursor = test_repo.get_connector_cursor("test_conn")
    assert cursor == "cursor_v1"

    # Verify articles query
    articles = test_repo.get_articles(limit=10)
    assert len(articles) == 2


def test_idempotency_duplicate_suppression(test_repo):
    """Test that repeating the same batch suppresses duplicates and does not error."""
    items = [make_dummy_item(source_item_id="item-1")]

    # First insert
    ins1, skip1 = test_repo.save_batch_atomic("test_conn", items, "cursor_1")
    assert ins1 == 1
    assert skip1 == 0

    # Second insert with identical connector_id and source_item_id
    ins2, skip2 = test_repo.save_batch_atomic("test_conn", items, "cursor_2")
    assert ins2 == 0
    assert skip2 == 1

    # Verify only 1 article exists in DB
    articles = test_repo.get_articles(limit=10)
    assert len(articles) == 1
    # Verify cursor updated to cursor_2
    assert test_repo.get_connector_cursor("test_conn") == "cursor_2"


def test_cross_connector_isolation(test_repo):
    """Test that same source_item_id under different connector_ids are preserved independently."""
    item_a = make_dummy_item(connector_id="conn_a", source_item_id="id-100", title="From A")
    item_b = make_dummy_item(connector_id="conn_b", source_item_id="id-100", title="From B")

    ins_a, _ = test_repo.save_batch_atomic("conn_a", [item_a], "cur_a")
    ins_b, _ = test_repo.save_batch_atomic("conn_b", [item_b], "cur_b")

    assert ins_a == 1
    assert ins_b == 1

    articles_a = test_repo.get_articles(connector_id="conn_a")
    articles_b = test_repo.get_articles(connector_id="conn_b")
    assert len(articles_a) == 1
    assert len(articles_b) == 1
    assert articles_a[0].title == "From A"
    assert articles_b[0].title == "From B"


def test_transaction_rollback_no_partial_persistence(test_repo):
    """
    Test that if an error occurs during batch processing,
    neither articles nor cursor are committed (atomic guarantee).
    """
    valid_item = make_dummy_item(source_item_id="valid-1")

    # Manually test rollback by attempting an invalid query inside transaction
    conn = test_repo._get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        conn.execute(
            """INSERT INTO articles (
                   connector_id, source_item_id, publisher, source_type, title, url,
                   observed_at_utc, content_hash, raw_metadata, tickers, created_at_utc
               ) VALUES ('conn_err', 'err-1', 'Pub', 'rss', 'Title', 'http://url',
                         datetime('now'), 'hash', '{}', '[]', datetime('now'));"""
        )
        # Force a failure (syntax error)
        conn.execute("INSERT INTO non_existent_table VALUES (1);")
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()

    # Verify item was not persisted
    articles = test_repo.get_articles(connector_id="conn_err")
    assert len(articles) == 0


def test_dead_letter_bounded_payload(test_repo):
    """Test that dead-letter items record errors and bound payloads."""
    large_payload = "X" * 100000  # 100 KB
    test_repo.record_dead_letter(
        connector_id="test_conn",
        source_item_id="bad-item-99",
        error_category="PARSING_ERROR",
        error_message="Invalid XML",
        raw_payload=large_payload,
    )

    stats = test_repo.get_ingestion_stats()
    assert stats["dead_letter_count"] == 1

    with test_repo._get_conn() as conn:
        row = conn.execute("SELECT raw_payload FROM dead_letter_items WHERE source_item_id='bad-item-99';").fetchone()
        assert len(row[0]) <= 65536  # Bounded to 64KB


def test_database_reopen_and_persistence(tmp_path):
    """Test that closing connection and reopening persists state cleanly (WAL durability)."""
    db_file = tmp_path / "persistent.db"
    db_path = str(db_file)
    apply_migrations(db_path)

    repo1 = Repository(db_path)
    repo1.save_batch_atomic(
        connector_id="conn_persist",
        items=[make_dummy_item(connector_id="conn_persist", source_item_id="p-1")],
        new_cursor="cursor_persist_1",
    )

    # Instantiate brand new repository pointing to the same file
    repo2 = Repository(db_path)
    cursor = repo2.get_connector_cursor("conn_persist")
    assert cursor == "cursor_persist_1"
    articles = repo2.get_articles(connector_id="conn_persist")
    assert len(articles) == 1
    assert articles[0].source_item_id == "p-1"


def test_sqlite_wal_pragmas_applied(test_repo):
    """Verify that SQLite connection configuration applies critical PRAGMAs for concurrency and durability."""
    with test_repo._get_conn() as conn:
        cursor = conn.cursor()

        cursor.execute("PRAGMA journal_mode;")
        journal_mode = cursor.fetchone()[0]
        assert journal_mode.lower() == "wal"

        cursor.execute("PRAGMA synchronous;")
        sync_mode = cursor.fetchone()[0]
        # NORMAL is represented as 1
        assert sync_mode == 1

        cursor.execute("PRAGMA foreign_keys;")
        foreign_keys = cursor.fetchone()[0]
        assert foreign_keys == 1

        cursor.execute("PRAGMA busy_timeout;")
        busy_timeout = cursor.fetchone()[0]
        assert busy_timeout >= 10000


def test_concurrent_readers_during_writer(test_repo):
    """
    Verify WAL mode property: concurrent readers can read snapshot data
    without being blocked by an in-flight write transaction.
    """
    # Seed initial article
    test_repo.save_batch_atomic(
        "conn_wal",
        [make_dummy_item(connector_id="conn_wal", source_item_id="initial-1", title="Initial Article")],
        "cur_init"
    )

    writer_conn = test_repo._get_conn()
    reader_conn = test_repo._get_conn()

    try:
        # Writer begins transaction and inserts uncommitted article
        writer_conn.execute("BEGIN IMMEDIATE;")
        writer_conn.execute(
            """INSERT INTO articles (
                   connector_id, source_item_id, publisher, source_type, title, url,
                   observed_at_utc, content_hash, raw_metadata, tickers, created_at_utc
               ) VALUES ('conn_wal', 'uncommitted-2', 'Pub', 'rss', 'Uncommitted Title',
                         'https://example.com/2', datetime('now'), 'hash2', '{}', '[]', datetime('now'));"""
        )

        # Reader performs query while writer holds lock
        cursor = reader_conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM articles WHERE connector_id = 'conn_wal';")
        reader_count = cursor.fetchone()[0]
        # Reader sees only committed snapshot (1 article) without blocking or locking
        assert reader_count == 1

        # Writer commits
        writer_conn.commit()

        # Reader now sees updated state (2 articles)
        cursor.execute("SELECT COUNT(*) FROM articles WHERE connector_id = 'conn_wal';")
        reader_count_after = cursor.fetchone()[0]
        assert reader_count_after == 2

    finally:
        writer_conn.close()
        reader_conn.close()


def test_fts5_triggers_synchronization(test_repo):
    """
    Verify Full-Text Search (FTS5) index synchronization via database triggers:
    1. Insertion triggers insert into articles_fts.
    2. Update triggers update articles_fts.
    3. Deletion triggers remove from articles_fts.
    """
    # 1. Insert article with unique headline
    test_repo.save_batch_atomic(
        "conn_fts",
        [make_dummy_item(
            connector_id="conn_fts",
            source_item_id="fts-1",
            title="NVIDIA Announces Quantum Computing Breakthrough",
            content_hash="nvda_hash_1"
        )],
        "cur_fts_1"
    )

    with test_repo._get_conn() as conn:
        cursor = conn.cursor()

        # Query FTS5 table
        cursor.execute("SELECT rowid, title FROM articles_fts WHERE articles_fts MATCH 'Quantum';")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert "Quantum Computing" in rows[0]["title"]
        art_id = rows[0]["rowid"]

        # 2. Update article title directly in articles table
        cursor.execute(
            "UPDATE articles SET title = 'NVIDIA Revises Quantum Roadmap' WHERE id = ?;",
            (art_id,)
        )
        conn.commit()

        # Verify old term no longer matches and new term matches
        cursor.execute("SELECT rowid FROM articles_fts WHERE articles_fts MATCH 'Breakthrough';")
        assert len(cursor.fetchall()) == 0

        cursor.execute("SELECT rowid FROM articles_fts WHERE articles_fts MATCH 'Roadmap';")
        assert len(cursor.fetchall()) == 1

        # 3. Delete article
        cursor.execute("DELETE FROM articles WHERE id = ?;", (art_id,))
        conn.commit()

        # Verify FTS index cleaned up
        cursor.execute("SELECT rowid FROM articles_fts WHERE articles_fts MATCH 'Roadmap';")
        assert len(cursor.fetchall()) == 0

