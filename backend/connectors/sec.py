"""SEC EDGAR Submissions connector using the official SEC JSON API."""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from backend.connectors.base import BaseConnector
from backend.connectors.models import ConnectorStatus, FetchBatch, SourceItem
from backend.ingestion.normalizer import normalize_record

logger = logging.getLogger(__name__)


class SECConfigurationError(ValueError):
    """Raised when SEC connector configuration invariants (e.g. User-Agent) are violated."""
    pass


def normalize_cik(cik: str) -> str:
    """Normalise SEC CIK identifier to required 10-digit zero-padded string."""
    clean = cik.strip()
    # Strip any leading 'CIK' prefix if present
    if clean.upper().startswith("CIK"):
        clean = clean[3:]
    return clean.zfill(10)


class SECConnector(BaseConnector):
    """
    Ingests official SEC EDGAR company filing submissions via data.sec.gov.
    Enforces SEC automated access policy:
    1. Identifiable User-Agent with valid contact address (e.g., 'AppName admin@example.com').
    2. Rate limiting (maximum 10 requests per second enforced with token pacing).
    """

    BASE_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
    ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_num}/{accession_nodash}/{primary_doc}"

    def __init__(
        self,
        connector_id: str = "sec_edgar",
        name: str = "SEC EDGAR Submissions",
        user_agent: str = "",
        tracked_ciks: Optional[Dict[str, str]] = None,
        enabled: bool = True,
        poll_interval_seconds: int = 600,
        http_client: Optional[httpx.AsyncClient] = None,
        min_request_interval_seconds: float = 0.12,  # Max ~8 requests/sec, well under SEC's 10 req/s limit
    ) -> None:
        super().__init__(
            connector_id=connector_id,
            name=name,
            enabled=enabled,
            poll_interval_seconds=poll_interval_seconds,
        )
        self.user_agent = user_agent.strip()
        # Mapping: CIK (10-digits) -> TICKER
        self.tracked_ciks = {
            normalize_cik(k): v.upper() for k, v in (tracked_ciks or {}).items()
        }
        self._http_client = http_client
        self.min_request_interval_seconds = min_request_interval_seconds
        self._last_request_timestamp = 0.0

        # Validate configuration if connector is enabled
        if self.enabled and not self._is_valid_user_agent(self.user_agent):
            self.status = ConnectorStatus.AUTH_ERROR
            self.last_error = (
                "SEC requires an identifiable User-Agent in the format 'Sample Company Name AdminContact@domain.com'. "
                f"Configured value was: '{self.user_agent}'"
            )

    @staticmethod
    def _is_valid_user_agent(ua: str) -> bool:
        """Validate SEC User-Agent requirement: application name + email address."""
        if not ua or "@" not in ua:
            return False
        parts = ua.strip().split()
        return len(parts) >= 2

    async def _rate_limit_pause(self) -> None:
        """Enforce rate-limiting interval between SEC API calls."""
        now = time.monotonic()
        elapsed = now - self._last_request_timestamp
        if elapsed < self.min_request_interval_seconds:
            await asyncio.sleep(self.min_request_interval_seconds - elapsed)
        self._last_request_timestamp = time.monotonic()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client and not self._http_client.is_closed:
            return self._http_client
        return httpx.AsyncClient(timeout=20.0, follow_redirects=True)

    def _parse_cursor(self, cursor: Optional[str]) -> Dict[str, Any]:
        """Cursor structure: {'ciks': {'0000320193': {'last_accession': '...', 'seen': [...]}}}"""
        if not cursor:
            return {"ciks": {}}
        try:
            data = json.loads(cursor)
            if isinstance(data, dict) and "ciks" in data:
                return data
        except (json.JSONDecodeError, TypeError):
            pass
        return {"ciks": {}}

    def _serialize_cursor(self, state: Dict[str, Any]) -> str:
        return json.dumps(state)

    async def fetch(self, cursor: Optional[str] = None) -> FetchBatch:
        """Fetch latest submissions for all tracked companies."""
        self.record_attempt()

        if not self._is_valid_user_agent(self.user_agent):
            err_msg = (
                "SEC EDGAR User-Agent is not properly configured. "
                "Set SEC_USER_AGENT='AppTitle admin@domain.com' in .env"
            )
            self.record_failure(err_msg, is_auth=True)
            raise SECConfigurationError(err_msg)

        cursor_state = self._parse_cursor(cursor)
        cik_cursors: Dict[str, Dict[str, Any]] = cursor_state.get("ciks", {})

        client = await self._get_client()
        should_close = client != self._http_client

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }

        all_items: List[SourceItem] = []
        total_raw_count = 0
        new_cik_cursors: Dict[str, Dict[str, Any]] = dict(cik_cursors)

        try:
            for cik, ticker in self.tracked_ciks.items():
                await self._rate_limit_pause()
                url = self.BASE_URL.format(cik=cik)

                response = await client.get(url, headers=headers)
                response.raise_for_status()

                try:
                    data = response.json()
                except Exception as ex:
                    self.record_failure(f"Invalid JSON returned from SEC for CIK {cik}: {ex}")
                    continue

                entity_name = data.get("name") or ticker or f"CIK-{cik}"
                filings = data.get("filings", {}).get("recent", {})
                accession_numbers = filings.get("accessionNumber", [])
                total_raw_count += len(accession_numbers)

                cik_history = cik_cursors.get(cik, {})
                last_seen_accession = cik_history.get("last_accession")
                seen_set = set(cik_history.get("seen", []))

                forms = filings.get("form", [])
                filing_dates = filings.get("filingDate", [])
                report_dates = filings.get("reportDate", [])
                acceptance_dates = filings.get("acceptanceDateTime", [])
                primary_docs = filings.get("primaryDocument", [])
                primary_descs = filings.get("primaryDocDescription", [])
                acts = filings.get("act", [])
                file_numbers = filings.get("fileNumber", [])
                film_numbers = filings.get("filmNumber", [])
                is_xbrls = filings.get("isXBRL", [])

                new_accessions_this_run: List[str] = []

                for i, accession in enumerate(accession_numbers):
                    # If we've reached the last seen accession from a previous run, stop descending
                    if last_seen_accession and accession == last_seen_accession:
                        break
                    if accession in seen_set:
                        continue

                    form = forms[i] if i < len(forms) else "UNKNOWN"
                    filing_date = filing_dates[i] if i < len(filing_dates) else None
                    report_date = report_dates[i] if i < len(report_dates) else None
                    acceptance_dt = acceptance_dates[i] if i < len(acceptance_dates) else None
                    primary_doc = primary_docs[i] if i < len(primary_docs) else None
                    primary_desc = primary_descs[i] if i < len(primary_descs) else None

                    # Construct official SEC EDGAR document URL
                    cik_num = str(int(cik))  # strip leading zeros for archive path
                    accession_nodash = accession.replace("-", "")
                    if primary_doc:
                        doc_url = self.ARCHIVE_URL.format(
                            cik_num=cik_num,
                            accession_nodash=accession_nodash,
                            primary_doc=primary_doc,
                        )
                    else:
                        doc_url = f"https://www.sec.gov/edgar/browse/?CIK={cik}"

                    # Construct descriptive title and snippet
                    title_desc = f" - {primary_desc}" if primary_desc and primary_desc != form else ""
                    title = f"{entity_name} ({ticker}) filed Form {form}{title_desc}"
                    snippet = (
                        f"Form {form} submission for {entity_name} (CIK: {cik}). "
                        f"Filing Date: {filing_date or 'N/A'}. "
                        f"Report Date: {report_date or 'N/A'}. "
                        f"Accession: {accession}."
                    )

                    metadata = {
                        "cik": cik,
                        "ticker": ticker,
                        "entity_name": entity_name,
                        "form": form,
                        "accession_number": accession,
                        "filing_date": filing_date,
                        "report_date": report_date,
                        "acceptance_datetime": acceptance_dt,
                        "act": acts[i] if i < len(acts) else None,
                        "file_number": file_numbers[i] if i < len(file_numbers) else None,
                        "film_number": film_numbers[i] if i < len(film_numbers) else None,
                        "is_xbrl": is_xbrls[i] if i < len(is_xbrls) else None,
                        "primary_document": primary_doc,
                    }

                    # Use exact SEC acceptance timestamp if provided, fallback to filing date
                    published_timestamp = acceptance_dt or filing_date

                    try:
                        item = normalize_record(
                            connector_id=self.connector_id,
                            publisher=f"SEC EDGAR ({entity_name})",
                            source_type="sec_filing",
                            raw_id=f"sec_{accession}",
                            raw_title=title,
                            raw_url=doc_url,
                            raw_published=published_timestamp,
                            raw_content=snippet,
                            raw_metadata=metadata,
                            tickers=[ticker],
                            language="en",
                        )
                        all_items.append(item)
                        new_accessions_this_run.append(accession)
                    except Exception as err:
                        logger.warning(
                            "Failed to normalize SEC filing",
                            extra={"accession": accession, "error": str(err)}
                        )
                        continue

                # Update cursor state for this CIK
                latest_accession = accession_numbers[0] if accession_numbers else last_seen_accession
                updated_seen = (new_accessions_this_run + list(seen_set))[:100]
                new_cik_cursors[cik] = {
                    "last_accession": latest_accession,
                    "seen": updated_seen,
                }

            next_cursor = self._serialize_cursor({"ciks": new_cik_cursors})
            if self.consecutive_failures == 0:
                self.record_success(items_count=len(all_items))
            elif len(all_items) > 0:
                # Partial success: some items retrieved
                self.status = ConnectorStatus.DEGRADED

            return FetchBatch(
                connector_id=self.connector_id,
                items=all_items,
                next_cursor=next_cursor,
                raw_count=total_raw_count,
                source_status="OK",
            )

        except httpx.HTTPStatusError as e:
            is_auth = e.response.status_code in (401, 403)
            self.record_failure(f"HTTP {e.response.status_code}: {e.response.text[:200]}", is_auth=is_auth)
            raise
        except Exception as e:
            self.record_failure(str(e))
            raise
        finally:
            if should_close:
                await client.aclose()

    async def ack(self, cursor: str) -> None:
        """Acknowledge cursor persistence."""
        self.committed_cursor = cursor
