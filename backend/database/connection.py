"""SQLite database connection management and migration runner."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from backend.config import get_settings


def configure_connection(conn: sqlite3.Connection) -> None:
    """Apply required PRAGMAs for high-performance, restart-safe SQLite operation."""
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.execute("PRAGMA busy_timeout=10000;")
    cursor.close()


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Create and configure a raw SQLite connection."""
    target_path = db_path or get_settings().get_sqlite_path()
    # Ensure parent directory exists
    db_file = Path(target_path)
    if db_file.parent and not db_file.parent.exists():
        db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(target_path, check_same_thread=False)
    configure_connection(conn)
    return conn


@contextmanager
def get_db(db_path: str | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager providing a managed SQLite connection."""
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def apply_migrations(db_path: str | None = None) -> None:
    """
    Apply versioned migrations in numeric sequence.
    Guarantees schema reproducibility across environments.
    """
    target_path = db_path or get_settings().get_sqlite_path()
    conn = get_connection(target_path)
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    filename TEXT NOT NULL,
                    applied_at_utc TEXT NOT NULL
                );
            """)

            cursor = conn.cursor()
            cursor.execute("SELECT version FROM schema_migrations ORDER BY version ASC;")
            applied = {row[0] for row in cursor.fetchall()}

            migrations_dir = Path(__file__).parent / "migrations"
            sql_files = sorted(migrations_dir.glob("*.sql"))

            for f in sql_files:
                # Expect filenames like 001_initial_schema.sql
                version_str = f.name.split("_", 1)[0]
                if not version_str.isdigit():
                    continue
                version = int(version_str)
                if version not in applied:
                    sql_content = f.read_text(encoding="utf-8")
                    conn.executescript(sql_content)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, filename, applied_at_utc) VALUES (?, ?, datetime('now', 'utc'));",
                        (version, f.name)
                    )
    finally:
        conn.close()
