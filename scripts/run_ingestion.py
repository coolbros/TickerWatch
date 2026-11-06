"""CLI script to run an immediate ingestion pass across active connectors."""

import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.config import get_settings, setup_logging
from backend.connectors.registry import ConnectorRegistry
from backend.database.connection import apply_migrations
from backend.database.repository import Repository
from backend.ingestion.orchestrator import IngestionOrchestrator


async def main() -> None:
    parser = argparse.ArgumentParser(description="TickerWatch Ingestion Runner")
    parser.add_argument("--connector", help="Specific connector ID to run (runs all if omitted)")
    args = parser.parse_args()

    settings = get_settings()
    setup_logging(settings.log_level)

    apply_migrations(settings.get_sqlite_path())

    repo = Repository(settings.get_sqlite_path())
    registry = ConnectorRegistry(settings)
    registry.load_from_config()

    orchestrator = IngestionOrchestrator(repository=repo)

    if args.connector:
        target = registry.get(args.connector)
        if not target:
            print(f"Error: Connector '{args.connector}' not found.")
            sys.exit(1)
        connectors = [target]
    else:
        connectors = registry.list_enabled()

    print(f"Starting ingestion pass for {len(connectors)} connector(s)...")
    for conn in connectors:
        print(f"\n--- Ingesting: {conn.connector_id} ({conn.name}) ---")
        result = await orchestrator.ingest_connector(conn)
        print(f"Status: {result.get('status')}")
        print(f"Fetched: {result.get('fetched')}, Inserted: {result.get('inserted')}, Skipped: {result.get('skipped')}")
        if result.get("error"):
            print(f"Error: {result.get('error')}")
        print(f"Duration: {round(result.get('duration_ms', 0), 2)} ms")


if __name__ == "__main__":
    asyncio.run(main())
