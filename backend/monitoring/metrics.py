"""Metrics collection and execution timers for ingestion diagnostics."""

import time
from contextlib import contextmanager
from typing import Any, Dict, Generator


class IngestionMetrics:
    """Helper for timing discrete stages of the ingestion pipeline."""

    def __init__(self) -> None:
        self.timings_ms: Dict[str, float] = {}

    @contextmanager
    def measure(self, stage_name: str) -> Generator[None, None, None]:
        t0 = time.monotonic()
        try:
            yield
        finally:
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            self.timings_ms[stage_name] = round(elapsed_ms, 2)

    def summary(self) -> Dict[str, Any]:
        return dict(self.timings_ms)
