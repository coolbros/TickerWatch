"""Unit tests for Provider-Neutral Historical Event Dataset Ingestion."""

from datetime import datetime, timezone, timedelta
import pytest

from backend.alpha.historical_import import (
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

    rec = HistoricalImportRecord(
        event_id="valid_1",
        jurisdiction=MarketJurisdiction.IN,
        source_feed="TEST_FEED",
        headline="Valid Announcement",
        event_timestamp_utc=now,
        system_available_at_utc=now + timedelta(seconds=2),
    )
    assert rec.event_id == "valid_1"

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
    """Verify NSE naive local IST timestamps convert accurately to UTC (-05:30) and preserve provenance."""
    importer = NSECorporateAnnouncementImporter()
    raw = {
        "SYMBOL": "LT",
        "COMPANY": "Larsen & Toubro Limited",
        "PURPOSE": "Award of major order worth Rs 2,500 crore",
        "BROADCAST_DATE_TIME": "10-Nov-2026 09:15:00",  # 09:15 IST -> 03:45 UTC
        "ATTACHMENT_URL": "https://nsearchives.nseindia.com/corporate/LT_01.pdf",
    }
    rec = importer.parse_record(raw)

    assert rec.jurisdiction == MarketJurisdiction.IN
    assert rec.instrument_id == "LT.NS"
    assert rec.company_name == "Larsen & Toubro Limited"
    assert "2,500 crore" in rec.headline
    # Strict IST -> UTC conversion!
    assert rec.event_timestamp_utc == datetime(2026, 11, 10, 3, 45, 0, tzinfo=timezone.utc)
    assert rec.system_available_at_utc >= rec.event_timestamp_utc
    prov = rec.raw_metadata["_timestamp_provenance"]
    assert prov["source_default_timezone"] == "Asia/Kolkata"
    assert prov["event"]["raw"] == "10-Nov-2026 09:15:00"
    assert "Asia/Kolkata" in prov["event"]["interpretation"]

    # Explicit UTC / offset strings must preserve their explicit offset cleanly
    raw_explicit = dict(raw)
    raw_explicit["BROADCAST_DATE_TIME"] = "2026-11-10T09:15:00+05:30"
    rec_exp = importer.parse_record(raw_explicit)
    assert rec_exp.event_timestamp_utc == datetime(2026, 11, 10, 3, 45, 0, tzinfo=timezone.utc)


def test_bse_filings_importer():
    """Verify BSE naive IST dissemination timestamps convert accurately to UTC."""
    importer = BSEFilingsImporter()
    raw = {
        "SCRIP_CD": "500510",
        "SCRIP_ID": "LT",
        "COMPANY_NAME": "Larsen & Toubro Ltd",
        "HEADLINE": "Outcome of Board Meeting",
        "DISSEMINATION_TIME": "15/03/2026 15:45:00",  # 15:45 IST -> 10:15 UTC
        "PDF_URL": "https://www.bseindia.com/xml-data/corpfiling/AttachLive/lt.pdf",
    }
    rec = importer.parse_record(raw)

    assert rec.jurisdiction == MarketJurisdiction.IN
    assert rec.instrument_id == "LT.BO"
    assert rec.headline == "Outcome of Board Meeting"
    assert rec.event_timestamp_utc == datetime(2026, 3, 15, 10, 15, 0, tzinfo=timezone.utc)


def test_rbi_macro_importer():
    """Verify RBI timestamps (explicit IST and naive local) convert accurately to UTC."""
    importer = RBIMacroReleaseImporter()
    raw = {
        "TITLE": "Monetary Policy Committee (MPC) Resolution - Repo Rate Hiked by 25 bps",
        "RELEASE_DATE_TIME": "Feb 06, 2026 10:00 IST",  # 10:00 IST -> 04:30 UTC
        "CONTENT": "The MPC at its meeting today decided to increase the policy repo rate by 25 basis points.",
        "URL": "https://rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=12345",
    }
    rec = importer.parse_record(raw)

    assert rec.jurisdiction == MarketJurisdiction.IN
    assert rec.instrument_id == "NIFTY_BANK"
    assert rec.event_family == "MACROECONOMIC"
    assert rec.event_subtype == "INTEREST_RATE_DECISION"
    assert rec.event_timestamp_utc == datetime(2026, 2, 6, 4, 30, 0, tzinfo=timezone.utc)


def test_sec_filings_importer_and_us_dst_handling():
    """Verify US SEC EDGAR explicit UTC and DST-aware Eastern Time (EST -05:00 vs EDT -04:00) handling."""
    importer = SECFilingsImporter()

    # 1. Explicit Z suffix
    raw_z = {
        "cik": "0000320193",
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "form": "8-K",
        "title": "Item 2.02 Results of Operations and Financial Condition",
        "acceptance_datetime": "2026-01-30T16:30:00Z",
        "accession_number": "0000320193-26-000010",
    }
    rec_z = importer.parse_record(raw_z)
    assert rec_z.jurisdiction == MarketJurisdiction.US
    assert rec_z.instrument_id == "AAPL.US"
    assert rec_z.event_timestamp_utc == datetime(2026, 1, 30, 16, 30, 0, tzinfo=timezone.utc)

    # 2. Winter EST (January -> UTC-5): naive SEC acceptance 16:30 EST == 21:30 UTC
    raw_est = dict(raw_z, acceptance_datetime="20260115163000", accession_number="0000320193-26-000011")
    rec_est = importer.parse_record(raw_est)
    assert rec_est.event_timestamp_utc == datetime(2026, 1, 15, 21, 30, 0, tzinfo=timezone.utc)

    # 3. Summer EDT (July -> UTC-4): naive SEC acceptance 16:30 EDT == 20:30 UTC
    raw_edt = dict(raw_z, acceptance_datetime="20260715163000", accession_number="0000320193-26-000012")
    rec_edt = importer.parse_record(raw_edt)
    assert rec_edt.event_timestamp_utc == datetime(2026, 7, 15, 20, 30, 0, tzinfo=timezone.utc)

    # 4. Generic JSONL rejects naive ambiguous timestamps unless default_timezone is explicitly declared
    generic_strict = GenericHistoricalJSONLImporter()
    with pytest.raises(ValueError, match="without timezone offset"):
        generic_strict.parse_record({
            "event_id": "g1",
            "jurisdiction": "US",
            "source_feed": "CUSTOM",
            "headline": "Ambiguous local timestamp",
            "event_timestamp_utc": "2026-07-15T10:00:00",
            "system_available_at_utc": "2026-07-15T10:01:00Z",
        })
