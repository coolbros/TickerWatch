"""Unit tests for Provider-Neutral Historical Event Dataset Ingestion."""

from datetime import datetime, timezone, timedelta
import json
import pytest

from backend.alpha.historical_import import (
    BaseHistoricalImporter,
    BSEFilingsImporter,
    GenericHistoricalJSONLImporter,
    HistoricalImportRecord,
    MarketJurisdiction,
    NSECorporateAnnouncementImporter,
    RBIMacroReleaseImporter,
    SECFilingsImporter,
)


def test_historical_record_causality_validation():
    """Verify system_available_at_utc cannot precede event_timestamp_utc."""
    now = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
    earlier = now - timedelta(seconds=10)

    # Valid: system availability is after or equal to event occurrence
    rec = HistoricalImportRecord(
        event_id="valid_1",
        jurisdiction=MarketJurisdiction.IN,
        source_feed="TEST_FEED",
        headline="Valid Announcement",
        event_timestamp_utc=now,
        system_available_at_utc=now + timedelta(seconds=2),
    )
    assert rec.event_id == "valid_1"

    # Invalid: system claims availability before occurrence (look-ahead)
    with pytest.raises(ValueError, match="Causality violated"):
        HistoricalImportRecord(
            event_id="invalid_1",
            jurisdiction=MarketJurisdiction.IN,
            source_feed="TEST_FEED",
            headline="Invalid Announcement",
            event_timestamp_utc=now,
            system_available_at_utc=earlier,
        )


def test_nse_corporate_importer():
    """Verify parsing of NSE corporate announcements."""
    importer = NSECorporateAnnouncementImporter()
    raw = {
        "SYMBOL": "LT",
        "COMPANY": "Larsen & Toubro Limited",
        "PURPOSE": "Award of major order worth Rs 2,500 crore",
        "BROADCAST_DATE_TIME": "15-Mar-2026 09:30:00",
        "ATTACHMENT_URL": "https://nsearchives.nseindia.com/corporate/LT_01.pdf",
    }
    rec = importer.parse_record(raw)

    assert rec.jurisdiction == MarketJurisdiction.IN
    assert rec.instrument_id == "LT.NS"
    assert rec.company_name == "Larsen & Toubro Limited"
    assert "2,500 crore" in rec.headline
    assert rec.event_timestamp_utc == datetime(2026, 3, 15, 9, 30, 0, tzinfo=timezone.utc)
    assert rec.system_available_at_utc >= rec.event_timestamp_utc


def test_bse_filings_importer():
    """Verify parsing of BSE announcements."""
    importer = BSEFilingsImporter()
    raw = {
        "SCRIP_CD": "500510",
        "SCRIP_ID": "LT",
        "COMPANY_NAME": "Larsen & Toubro Ltd",
        "HEADLINE": "Outcome of Board Meeting",
        "DISSEMINATION_TIME": "15/03/2026 15:45:00",
        "PDF_URL": "https://www.bseindia.com/xml-data/corpfiling/AttachLive/lt.pdf",
    }
    rec = importer.parse_record(raw)

    assert rec.jurisdiction == MarketJurisdiction.IN
    assert rec.instrument_id == "LT.BO"
    assert rec.headline == "Outcome of Board Meeting"
    assert rec.event_timestamp_utc == datetime(2026, 3, 15, 15, 45, 0, tzinfo=timezone.utc)


def test_rbi_macro_importer():
    """Verify parsing of RBI monetary policy releases."""
    importer = RBIMacroReleaseImporter()
    raw = {
        "TITLE": "Monetary Policy Committee (MPC) Resolution - Repo Rate Hiked by 25 bps",
        "RELEASE_DATE_TIME": "Feb 06, 2026 10:00 IST",
        "CONTENT": "The MPC at its meeting today decided to increase the policy repo rate by 25 basis points.",
        "URL": "https://rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=12345",
    }
    rec = importer.parse_record(raw)

    assert rec.jurisdiction == MarketJurisdiction.IN
    assert rec.instrument_id == "NIFTY_BANK"
    assert rec.event_family == "MACROECONOMIC"
    assert rec.event_subtype == "INTEREST_RATE_DECISION"
    assert rec.event_timestamp_utc == datetime(2026, 2, 6, 10, 0, 0, tzinfo=timezone.utc)


def test_sec_filings_importer():
    """Verify parsing of US SEC EDGAR filings."""
    importer = SECFilingsImporter()
    raw = {
        "cik": "0000320193",
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "form": "8-K",
        "title": "Item 2.02 Results of Operations and Financial Condition",
        "acceptance_datetime": "2026-01-30T16:30:00Z",
        "accession_number": "0000320193-26-000010",
        "url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/aapl-20260130.htm",
    }
    rec = importer.parse_record(raw)

    assert rec.jurisdiction == MarketJurisdiction.US
    assert rec.instrument_id == "AAPL.US"
    assert rec.event_id == "sec_000032019326000010"
    assert rec.event_timestamp_utc == datetime(2026, 1, 30, 16, 30, 0, tzinfo=timezone.utc)
