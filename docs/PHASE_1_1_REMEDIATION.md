# TickerWatch — Phase 1.1: Engineering Remediation Report

**Project:** TickerWatch  
**Phase:** 1.1 (Independent Engineering Audit & Hardening)  
**Status:** All Remediations Implemented & Verified  
**Date:** November 2026  

---

## 1. Overview

During the independent engineering audit of Phase 1, five specific defects and resilience gaps were identified across rate limiting, cursor synchronization, health monitoring, task cancellation, and TLS verification.

Each defect was reproduced, remediated in the codebase, and verified with a dedicated automated regression test.

---

## 2. Remediation Details

### Finding 1: Shared Process-Wide SEC Rate Limiter

- **Severity:** High
- **Category:** API Compliance & Rate Limiting
- **Affected Files:**
  - `backend/connectors/sec.py`
- **Root Cause:**
  `SECConnector` previously tracked its last request timestamp in an instance variable (`self._last_request_monotonic`). If multiple `SECConnector` instances were initialized (e.g. across different workers, CIK partitions, or scheduled intervals), requests were not paced against a global ceiling. This created a direct risk of exceeding the official SEC EDGAR limit of 10 requests/second.
- **Remediation:**
  Introduced `SECRateLimiter` implementing a process-wide token bucket with an async lock. The limiter enforces a minimum inter-request delay (default `0.12s`, capping requests at ~8.3 req/s) shared across all connector instances and CIK lookups.
  ```python
  class SECRateLimiter:
      _instance: Optional["SECRateLimiter"] = None

      def __init__(self, min_interval_seconds: float = 0.12) -> None:
          self.min_interval_seconds = min_interval_seconds
          self._last_request_monotonic = 0.0
          self._lock: Optional[asyncio.Lock] = None

      @classmethod
      def get_shared_limiter(cls, min_interval_seconds: float = 0.12) -> "SECRateLimiter":
          if cls._instance is None:
              cls._instance = cls(min_interval_seconds=min_interval_seconds)
          return cls._instance

      async def acquire(self) -> None:
          async with self._get_lock():
              now = time.monotonic()
              elapsed = now - self._last_request_monotonic
              if elapsed < self.min_interval_seconds:
                  await asyncio.sleep(self.min_interval_seconds - elapsed)
              self._last_request_monotonic = time.monotonic()
  ```
- **Regression Test:**
  `tests/integration/test_audit_regressions.py::test_shared_sec_rate_limiter_across_instances`

---

### Finding 2: Conflation of Transport Poll Success and Content Freshness on HTTP 304

- **Severity:** Medium
- **Category:** Observability & Health Monitoring
- **Affected Files:**
  - `backend/database/migrations/002_add_persistence_timestamp.sql`
  - `backend/database/models.py`
  - `backend/database/repository.py`
  - `backend/connectors/models.py`
  - `backend/connectors/base.py`
  - `backend/monitoring/health.py`
- **Root Cause:**
  When a feed returned HTTP 304 (Not Modified), or when an active feed produced no new articles during a quiet market period, the system did not cleanly distinguish between:
  1. Successful transport polling (HTTP 200 or 304).
  2. Observation of newly published items.
  3. Database insertion of new records.
  Consequently, long periods without news could falsely trigger `ConnectorStatus.DEGRADED` or `STALE` even though the feed was operating normally.
- **Remediation:**
  - Added migration `002_add_persistence_timestamp.sql` adding `last_successful_persistence` to `connector_state`.
  - Updated `Repository.save_batch_atomic()` to only update `last_successful_persistence` when `inserted_count > 0`.
  - Added `is_source_idle: bool` to `ConnectorHealth`. If transport poll succeeds but no new articles exist, the connector remains `ConnectorStatus.HEALTHY` with `is_source_idle=True`.
- **Regression Tests:**
  - `tests/integration/test_audit_regressions.py::test_304_updates_successful_poll_without_advancing_observed_item`
  - `tests/integration/test_audit_regressions.py::test_persistence_timestamp_distinction`

---

### Finding 3: Premature Task Cancellation in Scheduler Shutdown

- **Severity:** Medium
- **Category:** Concurrency & Graceful Shutdown
- **Affected Files:**
  - `backend/ingestion/scheduler.py`
- **Root Cause:**
  `IngestionScheduler.stop()` immediately called `.cancel()` on all running `asyncio.Task` instances. This abruptly terminated in-flight connector fetch cycles without giving them an opportunity to complete naturally or gracefully release resources.
- **Remediation:**
  Implemented a two-stage graceful shutdown in `IngestionScheduler.stop(timeout_seconds=5.0)`:
  1. Signals `_running = False` and awaits running tasks via `asyncio.wait(..., timeout=timeout_seconds)`.
  2. Only tasks that do not finish within the timeout are cancelled.
  ```python
  async def stop(self, timeout_seconds: float = 5.0) -> None:
      self._running = False
      pending_tasks = [t for t in self.tasks if not t.done()]
      if pending_tasks:
          done, still_pending = await asyncio.wait(
              pending_tasks,
              timeout=timeout_seconds,
              return_when=asyncio.ALL_COMPLETED,
          )
          for task in still_pending:
              task.cancel()
              try:
                  await task
              except (asyncio.CancelledError, Exception):
                  pass
      self.tasks.clear()
  ```
- **Regression Test:**
  `tests/integration/test_audit_regressions.py::test_scheduler_graceful_shutdown_natural_completion`

---

### Finding 4: In-Memory Connector Cursor Ack Outside Cancellation Shield

- **Severity:** High
- **Category:** Durability & Cursor Integrity
- **Affected Files:**
  - `backend/ingestion/orchestrator.py`
- **Root Cause:**
  In `IngestionOrchestrator.ingest_connector()`, `save_batch_atomic()` was wrapped with `asyncio.shield()`, but `connector.ack(batch)` was called outside the shield. If an async task cancellation occurred after database commit but before `connector.ack()`, the SQLite database held the new cursor while the connector's in-memory state held the old cursor.
- **Remediation:**
  Unified the database persistence and connector cursor acknowledgement into a single shielded helper coroutine `_persist_and_ack()`:
  ```python
  async def _persist_and_ack() -> Dict[str, Any]:
      save_result = await self.repository.save_batch_atomic(
          connector_id=connector.connector_id,
          items=batch.items,
          new_cursor=batch.new_cursor,
          status=connector.status.value,
          observed_item_time=batch.latest_published_at_utc,
      )
      connector.ack(batch)
      return save_result

  result = await asyncio.shield(_persist_and_ack())
  ```
- **Regression Test:**
  `tests/integration/test_audit_regressions.py::test_atomic_persistence_and_ack_under_cancellation`

---

### Finding 5: Windows CryptoAPI Root Store Resolution for TLS Verification

- **Severity:** Medium
- **Category:** Security & Environment Compatibility
- **Affected Files:**
  - `backend/config.py`
  - `backend/connectors/rss.py`
  - `backend/connectors/sec.py`
- **Root Cause:**
  On Windows systems (corporate workstations, developer environments with custom certificate roots), Python standard `ssl.create_default_context()` does not automatically query the Windows CryptoAPI certificate stores (`ROOT` and `CA`), causing `[SSL: CERTIFICATE_VERIFY_FAILED]` when connecting to official endpoints.
- **Remediation:**
  Implemented `get_ssl_context()` in `backend/config.py` to enumerate certificates from Windows system stores using `ssl.enum_certificates()`:
  ```python
  def get_ssl_context() -> ssl.SSLContext:
      ctx = ssl.create_default_context()
      if sys.platform == "win32":
          for store in ("ROOT", "CA"):
              try:
                  for cert, encoding, trust in ssl.enum_certificates(store):
                      try:
                          ctx.load_verify_locations(cadata=cert)
                      except Exception:
                          pass
              except Exception:
                  pass
      return ctx
  ```
  Both `RSSConnector` and `SECConnector` now use `get_ssl_context()` in their HTTP clients.
- **Verification:**
  Confirmed by successful live execution of `scripts/live_smoke_test.py` against `https://www.sec.gov/news/pressreleases.rss` and `https://data.sec.gov/submissions/CIK0000320193.json`.

---

## 3. Summary of Remediations

| # | Finding | Severity | File(s) Changed | Status |
|---|---|---|---|---|
| 1 | Shared SEC Rate Limiter | High | `backend/connectors/sec.py` | Remediated & Verified |
| 2 | Conflated Health & 304 Metrics | Medium | `backend/connectors/base.py`, `backend/database/repository.py`, `backend/monitoring/health.py` | Remediated & Verified |
| 3 | Premature Scheduler Task Cancellation | Medium | `backend/ingestion/scheduler.py` | Remediated & Verified |
| 4 | In-Memory Cursor Ack Outside Shield | High | `backend/ingestion/orchestrator.py` | Remediated & Verified |
| 5 | Windows Root CA TLS Resolution | Medium | `backend/config.py`, `backend/connectors/rss.py`, `backend/connectors/sec.py` | Remediated & Verified |
