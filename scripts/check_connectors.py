"""CLI script to probe and print health of all configured connectors."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.config import get_settings
from backend.connectors.registry import ConnectorRegistry
from backend.database.connection import apply_migrations
from backend.database.repository import Repository
from backend.monitoring.health import HealthService


async def main() -> None:
    settings = get_settings()
    apply_migrations(settings.get_sqlite_path())

    repo = Repository(settings.get_sqlite_path())
    registry = ConnectorRegistry(settings)
    registry.load_from_config()

    health_svc = HealthService(registry, repo)
    health_data = await health_svc.get_all_health()

    print("=" * 70)
    print("TICKERWATCH CONNECTOR HEALTH AUDIT")
    print(f"Overall Status: {health_data['status']}")
    print(f"Timestamp:      {health_data['timestamp_utc']}")
    print("=" * 70)

    for c in health_data["connectors"]:
        status_symbol = "[OK]" if c["status"] == "HEALTHY" else f"[{c['status']}]"
        print(f"\n{status_symbol} {c['connector_id']} - {c['name']}")
        print(f"   Enabled:             {c['enabled']}")
        print(f"   Poll Interval:       {c['poll_interval_seconds']}s")
        print(f"   Last Attempted:      {c['last_attempted_at_utc'] or 'Never'}")
        print(f"   Last Successful:     {c['last_successful_at_utc'] or 'Never'}")
        print(f"   Last Observed Item:  {c['last_observed_item_at_utc'] or 'Never'}")
        print(f"   Consecutive Errors:  {c['consecutive_failures']}")
        if c.get("last_error"):
            print(f"   Last Error:          {c['last_error']}")


if __name__ == "__main__":
    asyncio.run(main())
