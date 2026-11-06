"""CLI script to inspect persisted articles, connector cursors and ingestion stats."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.config import get_settings
from backend.database.connection import apply_migrations
from backend.database.repository import Repository


def main() -> None:
    settings = get_settings()
    db_path = settings.get_sqlite_path()
    apply_migrations(db_path)

    repo = Repository(db_path)
    stats = repo.get_ingestion_stats()

    print("=" * 60)
    print("TICKERWATCH DATABASE INSPECTION")
    print(f"Database File: {db_path}")
    print("=" * 60)
    print(f"Total Articles:             {stats['total_articles']}")
    print(f"Total Ingestion Attempts:   {stats['total_ingestion_attempts']}")
    print(f"Total Items Fetched:        {stats['total_fetched']}")
    print(f"Total Items Inserted:       {stats['total_inserted']}")
    print(f"Total Items Skipped:        {stats['total_skipped']}")
    print(f"Total Items Failed:         {stats['total_failed']}")
    print(f"Dead Letter Queue Count:    {stats['dead_letter_count']}")
    print("\nArticles per Connector:")
    for cid, count in stats.get("articles_by_connector", {}).items():
        print(f"  - {cid}: {count}")

    print("\n" + "-" * 60)
    print("CONNECTOR STATES & CURSORS")
    print("-" * 60)
    states = repo.get_all_connector_states()
    if not states:
        print("  (No connector state recorded yet)")
    for s in states:
        print(f"Connector:            {s.connector_id}")
        print(f"  Status:             {s.status}")
        print(f"  Cursor:             {s.cursor[:80] + '...' if s.cursor and len(s.cursor) > 80 else s.cursor}")
        print(f"  Last Success:       {s.last_successful_fetch}")
        print(f"  Consecutive Errors: {s.consecutive_failures}")
        if s.last_error:
            print(f"  Last Error:         {s.last_error}")
        print()

    print("-" * 60)
    print("RECENT 5 ARTICLES")
    print("-" * 60)
    articles = repo.get_articles(limit=5)
    if not articles:
        print("  (No articles stored in database yet)")
    for a in articles:
        print(f"[{a.id}] {a.title}")
        print(f"     Source:    {a.connector_id} | Type: {a.source_type} | Published: {a.published_at_utc}")
        print(f"     URL:       {a.url}")
        print(f"     Tickers:   {a.tickers}")
        print()


if __name__ == "__main__":
    main()
