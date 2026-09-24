"""Tests for the Pakistan extension module.

Covers: company model, identity handling, classifier, fiscal year logic,
folder naming, source profiles, discovery, and gap matrix.
"""

import csv
import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from annual_reports.pakistan.company import PakistanCompany
from annual_reports.pakistan.classifier import (
    classify_annual_report,
    extract_fiscal_year,
    determine_period_end,
)
from annual_reports.pakistan.config import (
    PakistanConfig,
    COUNTRY,
    EXCHANGE_MIC,
    detect_google_drive,
)
from annual_reports.pakistan.report import PakistanReport
from annual_reports.pakistan.source_profile import SourceProfileStore
from annual_reports.pakistan.universe import resolve_companies, get_seed_universe


# ── Company Model ─────────────────────────────────────────────────────────

class TestPakistanCompany:
    def test_basic_properties(self):
        c = PakistanCompany(company_name="Habib Bank Limited", psx_symbol="HBL")
        assert c.country == "PAK"
        assert c.exchange == "XKAR"
        assert c.identity_key == "PAK|XKAR|HBL"
        assert c.folder_name == "Habib Bank Limited [HBL]"

    def test_effective_lei_with_genuine(self):
        c = PakistanCompany(company_name="Test", psx_symbol="TST",
                            lei="549300GKFG0RYRRQ1414")
        assert c.has_genuine_lei
        assert c.effective_lei == "549300GKFG0RYRRQ1414"

    def test_effective_lei_without_genuine(self):
        c = PakistanCompany(company_name="Test", psx_symbol="TST")
        assert not c.has_genuine_lei
        lei = c.effective_lei
        assert lei.startswith("XPAK")
        assert len(lei) == 20
        # Deterministic
        assert c.effective_lei == lei

    def test_no_fabricated_lei(self):
        """Fallback LEI must NOT look like a real LEI."""
        c = PakistanCompany(company_name="Test", psx_symbol="TST")
        assert c.effective_lei.startswith("XPAK")

    def test_effective_isin_with_genuine(self):
        c = PakistanCompany(company_name="Test", psx_symbol="TST",
                            isin="PK0078801017")
        assert c.has_genuine_isin
        assert c.effective_isin == "PK0078801017"

    def test_effective_isin_without_genuine(self):
        c = PakistanCompany(company_name="Test", psx_symbol="TST")
        assert not c.has_genuine_isin
        isin = c.effective_isin
        assert isin.startswith("PK")
        assert len(isin) == 12

    def test_no_fabricated_isin(self):
        """Fallback ISIN starts with PK but is deterministic hash-based."""
        c1 = PakistanCompany(company_name="Test A", psx_symbol="TSTA")
        c2 = PakistanCompany(company_name="Test B", psx_symbol="TSTB")
        assert c1.effective_isin != c2.effective_isin

    def test_folder_name_sanitization(self):
        c = PakistanCompany(company_name='Test "Company" (Pak)', psx_symbol="TCP")
        folder = c.folder_name
        assert '"' not in folder
        assert "[TCP]" in folder

    def test_fiscal_year_range_full(self):
        c = PakistanCompany(company_name="Test", psx_symbol="TST")
        assert c.fiscal_year_range(2017, 2025) == list(range(2017, 2026))

    def test_fiscal_year_range_with_listing_date(self):
        c = PakistanCompany(company_name="Test", psx_symbol="TST",
                            listing_date="2020-01-01")
        years = c.fiscal_year_range(2017, 2025)
        assert 2017 not in years
        assert 2020 in years
        assert 2025 in years

    def test_fiscal_year_range_with_delisting(self):
        c = PakistanCompany(company_name="Test", psx_symbol="TST",
                            delisting_date="2022-12-31")
        years = c.fiscal_year_range(2017, 2025)
        assert 2017 in years
        assert 2022 in years
        assert 2023 not in years

    def test_matches_query(self):
        c = PakistanCompany(company_name="Habib Bank Limited", psx_symbol="HBL",
                            aliases=["Habib Bank"])
        assert c.matches_query("HBL")
        assert c.matches_query("Habib Bank Limited")
        assert c.matches_query("Habib Bank")
        assert c.matches_query("hbl")
        assert not c.matches_query("UBL")

    def test_historical_alias_matching(self):
        c = PakistanCompany(company_name="K-Electric Limited", psx_symbol="KEL",
                            historical_names=["Karachi Electric Supply Company"],
                            historical_symbols=["KESC"])
        assert c.matches_query("KESC")
        assert c.matches_query("Karachi Electric Supply Company")


# ── Classifier ────────────────────────────────────────────────────────────

class TestClassifier:
    def test_annual_report_positive(self):
        assert classify_annual_report("Annual Report 2024") == "ANNUAL"

    def test_annual_accounts(self):
        assert classify_annual_report("Annual Accounts FY2023") == "ANNUAL"

    def test_audited_statements(self):
        assert classify_annual_report("Audited Financial Statements for year ended June 30, 2024") == "ANNUAL"

    def test_quarterly_negative(self):
        assert classify_annual_report("Quarterly Report Q1 2024") == "NOT_ANNUAL"

    def test_half_year_negative(self):
        assert classify_annual_report("Half Year Financial Results 2024") == "NOT_ANNUAL"

    def test_agm_notice_negative(self):
        assert classify_annual_report("AGM Notice 2024") == "NOT_ANNUAL"

    def test_proxy_form_negative(self):
        assert classify_annual_report("Proxy Form 2024") == "NOT_ANNUAL"

    def test_ambiguous_review(self):
        assert classify_annual_report("Report 2024.pdf") == "REVIEW"

    def test_nine_months_negative(self):
        assert classify_annual_report("Nine Months Financial Results") == "NOT_ANNUAL"

    def test_corporate_briefing_negative(self):
        assert classify_annual_report("Corporate Briefing Session") == "NOT_ANNUAL"

    def test_no_quarterly_as_annual(self):
        """Quarterly must NEVER be accepted as annual."""
        text = "First Quarter Financial Results 2024"
        assert classify_annual_report(text) == "NOT_ANNUAL"
        text2 = "Second Quarter Report for period ended December 31, 2024"
        assert classify_annual_report(text2) == "NOT_ANNUAL"


# ── Fiscal Year ───────────────────────────────────────────────────────────

class TestFiscalYear:
    def test_year_ended_pattern(self):
        assert extract_fiscal_year("year ended June 30, 2025") == 2025

    def test_fy_prefix(self):
        assert extract_fiscal_year("FY2024 Annual Report") == 2024

    def test_annual_report_year(self):
        assert extract_fiscal_year("Annual Report 2023") == 2023

    def test_bare_year(self):
        assert extract_fiscal_year("Report 2022") == 2022

    def test_no_year(self):
        assert extract_fiscal_year("Company Report") is None

    def test_period_end_extraction(self):
        text = "year ended December 31, 2024"
        assert "December 31, 2024" in determine_period_end(text)

    def test_fiscal_year_not_publication_year(self):
        """FY must come from report period, not publication context."""
        text = "Annual Report for the year ended June 30, 2024 published in 2025"
        fy = extract_fiscal_year(text)
        assert fy == 2024  # fiscal year, NOT 2025


# ── Report Model ──────────────────────────────────────────────────────────

class TestPakistanReport:
    def test_relative_path_format(self):
        c = PakistanCompany(company_name="Systems Limited", psx_symbol="SYS")
        r = PakistanReport(company=c, fiscal_year=2024, pdf_url="https://example.com/ar.pdf")
        path = str(r.relative_path).replace("\\", "/")
        assert "Systems Limited [SYS]" in path
        assert "FY2024" in path
        assert path.endswith("_AR_EN.pdf")
        assert "_PAK_XKAR_SYS_" in path

    def test_manifest_row(self):
        c = PakistanCompany(company_name="Test", psx_symbol="TST",
                            isin="PK0078801017")
        r = PakistanReport(company=c, fiscal_year=2024,
                           pdf_url="https://example.com/ar.pdf")
        row = r.to_manifest_row()
        assert row["country"] == "PAK"
        assert row["MIC"] == "XKAR"
        assert row["ticker"] == "TST"
        assert row["fiscal_year"] == "FY2024"


# ── Universe Resolution ──────────────────────────────────────────────────

class TestUniverse:
    def test_seed_universe_not_empty(self):
        universe = get_seed_universe()
        assert len(universe) > 20

    def test_resolve_ticker(self):
        result = resolve_companies(["HBL"])
        assert len(result) == 1
        assert result[0].psx_symbol == "HBL"
        assert result[0].identity_status == "RESOLVED"

    def test_resolve_name(self):
        result = resolve_companies(["Lucky Cement"])
        assert len(result) == 1
        assert result[0].psx_symbol == "LUCK"

    def test_resolve_unknown(self):
        result = resolve_companies(["UNKNOWN_COMPANY_XYZ"])
        assert len(result) == 1
        assert result[0].identity_status == "REVIEW"

    def test_resolve_multiple(self):
        result = resolve_companies(["HBL", "SYS", "LUCK"])
        assert len(result) == 3
        symbols = {c.psx_symbol for c in result}
        assert symbols == {"HBL", "SYS", "LUCK"}


# ── Source Profile Store ─────────────────────────────────────────────────

class TestSourceProfileStore:
    def test_create_and_profile(self, tmp_path: Path):
        store = SourceProfileStore(tmp_path / "test.sqlite3")
        store.upsert_profile("PAK|XKAR|HBL",
                             company_name="Habib Bank Limited",
                             psx_symbol="HBL",
                             annual_report_page="https://www.hbl.com/reports")
        profile = store.get_profile("PAK|XKAR|HBL")
        assert profile is not None
        assert profile["annual_report_page"] == "https://www.hbl.com/reports"
        store.close()

    def test_report_tracking(self, tmp_path: Path):
        store = SourceProfileStore(tmp_path / "test.sqlite3")
        store.upsert_report("PAK|XKAR|HBL", 2024,
                            "https://example.com/ar2024.pdf",
                            status="DISCOVERED")
        report = store.get_report("PAK|XKAR|HBL", 2024)
        assert report is not None
        assert report["status"] == "DISCOVERED"
        store.close()

    def test_gap_matrix(self, tmp_path: Path):
        store = SourceProfileStore(tmp_path / "test.sqlite3")
        store.upsert_report("PAK|XKAR|HBL", 2024,
                            "https://example.com/ar.pdf",
                            status="PUBLISHED")
        matrix = store.gap_matrix(["PAK|XKAR|HBL"], [2023, 2024, 2025])
        assert matrix["PAK|XKAR|HBL"][2024] == "VERIFIED"
        assert matrix["PAK|XKAR|HBL"][2023] == "MISSING"
        store.close()

    def test_gap_csv_export(self, tmp_path: Path):
        store = SourceProfileStore(tmp_path / "test.sqlite3")
        store.upsert_report("PAK|XKAR|HBL", 2024,
                            "https://example.com/ar.pdf",
                            status="PUBLISHED")
        matrix = store.gap_matrix(["PAK|XKAR|HBL"], [2023, 2024])
        csv_path = tmp_path / "gap.csv"
        store.export_gap_csv(matrix, csv_path, [2023, 2024])
        assert csv_path.is_file()
        with csv_path.open() as f:
            rows = list(csv.reader(f))
        assert len(rows) == 2  # header + 1 company
        assert rows[0] == ["company_id", "2023", "2024"]
        store.close()

    def test_snapshot(self, tmp_path: Path):
        store = SourceProfileStore(tmp_path / "test.sqlite3")
        store.upsert_profile("PAK|XKAR|TST", company_name="Test", psx_symbol="TST")
        dest = store.snapshot_to_drive(tmp_path / "backups")
        assert dest.is_file()
        # Verify the backup is a valid SQLite
        conn = sqlite3.connect(str(dest))
        rows = conn.execute("SELECT * FROM source_profiles").fetchall()
        assert len(rows) == 1
        conn.close()
        store.close()


# ── Config ────────────────────────────────────────────────────────────────

class TestConfig:
    def test_country_and_mic(self):
        assert COUNTRY == "PAK"
        assert EXCHANGE_MIC == "XKAR"

    def test_google_drive_detection(self):
        # This may or may not find Drive depending on environment
        result = detect_google_drive()
        # Just verify it doesn't crash
        assert result is None or result.is_dir()


# ── Failure scenarios ─────────────────────────────────────────────────────

class TestFailureScenarios:
    def test_no_quarterly_substitution(self):
        """Quarterly reports must never substitute for annual."""
        assert classify_annual_report("Quarterly Financial Report Q3 2024") == "NOT_ANNUAL"
        assert classify_annual_report("Half-Yearly Financial Results 2024") == "NOT_ANNUAL"

    def test_no_invented_identifiers(self):
        """Fallback identifiers must be visibly non-genuine."""
        c = PakistanCompany(company_name="Test Corp", psx_symbol="TST")
        assert not c.has_genuine_lei
        assert not c.has_genuine_isin
        # Must start with marker prefixes
        assert c.effective_lei.startswith("XPAK")

    def test_deterministic_fallback(self):
        """Same company always gets the same fallback identifiers."""
        c1 = PakistanCompany(company_name="Test", psx_symbol="ABC")
        c2 = PakistanCompany(company_name="Test", psx_symbol="ABC")
        assert c1.effective_lei == c2.effective_lei
        assert c1.effective_isin == c2.effective_isin

    def test_different_companies_different_fallbacks(self):
        c1 = PakistanCompany(company_name="Test A", psx_symbol="TSTA")
        c2 = PakistanCompany(company_name="Test B", psx_symbol="TSTB")
        assert c1.effective_lei != c2.effective_lei
        assert c1.effective_isin != c2.effective_isin


# ── Identity & Fiscal Year Intelligence ───────────────────────────────────

class TestIdentityAndFiscalYear:
    def test_ticker_normalization(self):
        from annual_reports.pakistan.identity import normalize_ticker
        assert normalize_ticker("psx:hbl") == "HBL"
        assert normalize_ticker("XKAR:SYS") == "SYS"
        assert normalize_ticker("[LUCK]") == "LUCK"
        assert normalize_ticker("  mcb  ") == "MCB"

    def test_genuine_id_verification(self):
        from annual_reports.pakistan.identity import is_genuine_isin, is_genuine_lei
        assert is_genuine_isin("PK0078801017")
        assert not is_genuine_isin("XPAK00000000")
        assert not is_genuine_isin("US0378331005")  # Not Pakistan
        assert is_genuine_lei("549300GKFG0RYRRQ1414")
        assert not is_genuine_lei("XPAK1234567890ABCDEF")

    def test_expected_company_years_eligibility(self):
        from annual_reports.pakistan.fiscal_year import determine_company_year_eligibility
        # Octopus listed Oct 2021
        st_2018, _ = determine_company_year_eligibility(2018, listing_date="2021-10-11")
        assert st_2018 == "NOT_LISTED"
        st_2022, _ = determine_company_year_eligibility(2022, listing_date="2021-10-11")
        assert st_2022 == "ELIGIBLE"
        # Suzuki delisted May 2024
        st_delist, _ = determine_company_year_eligibility(2025, delisting_date="2024-05-06")
        assert st_delist == "DELISTED"


# ── Adapters & Fingerprinting ─────────────────────────────────────────────

class TestAdapters:
    def test_fingerprint_wordpress(self):
        from annual_reports.pakistan.adapters import fingerprint_website
        html = '<link rel="stylesheet" href="https://example.com/wp-content/themes/theme.css">'
        assert fingerprint_website(html, "https://example.com") == "WordPressMediaAdapter"

    def test_fingerprint_sitemap(self):
        from annual_reports.pakistan.adapters import fingerprint_website
        html = '<?xml version="1.0"?><urlset xmlns="..."></urlset>'
        assert fingerprint_website(html, "https://example.com/sitemap.xml") == "SitemapAdapter"

    def test_static_year_archive_adapter(self):
        from annual_reports.pakistan.adapters import StaticYearArchiveAdapter
        html = """
        <div>
            <h3>2024</h3>
            <a href="/reports/ar2024.pdf">Annual Report</a>
        </div>
        """
        adapter = StaticYearArchiveAdapter()
        candidates = adapter.extract_candidates(html, "https://example.com", [2024])
        assert len(candidates) == 1
        assert candidates[0].fiscal_year == 2024
        assert candidates[0].pdf_url == "https://example.com/reports/ar2024.pdf"


# ── Multi-Stage Validation & Failure Categories ───────────────────────────

class TestValidation:
    def test_corrupt_pdf_rejection(self):
        from annual_reports.pakistan.validation import validate_pdf_content, FailureCategory
        res = validate_pdf_content(b"not a pdf at all")
        assert not res.is_valid
        assert res.failure_category == FailureCategory.CORRUPT_PDF

    def test_html_instead_of_pdf(self):
        from annual_reports.pakistan.validation import validate_pdf_content, FailureCategory
        html = b"<!DOCTYPE html><html><head><title>404</title></head><body>Not found</body></html>"
        res = validate_pdf_content(html)
        assert not res.is_valid
        assert res.failure_category == FailureCategory.HTML_INSTEAD_OF_PDF

    def test_valid_pdf_structure(self):
        from annual_reports.pakistan.validation import validate_pdf_content
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 50), "Annual Report 2024 Audited Financial Statements")
        raw = doc.tobytes()
        doc.close()

        res = validate_pdf_content(raw)
        assert res.is_valid
        assert res.page_count == 1
        assert res.sha256 == hashlib.sha256(raw).hexdigest()

    def test_quarterly_content_rejection(self):
        from annual_reports.pakistan.validation import validate_pdf_content, FailureCategory
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 50), "Condensed Interim Financial Statements First Quarter Report 2024")
        raw = doc.tobytes()
        doc.close()

        res = validate_pdf_content(raw)
        assert not res.is_valid
        assert res.failure_category == FailureCategory.QUARTERLY_NOT_ANNUAL


# ── Crash-Resume & Durability (Requirement 38) ────────────────────────────

class TestCrashResume:
    def test_resumability_skips_existing(self, tmp_path: Path):
        """Verified existing reports must be skipped; no duplicate downloads."""
        from annual_reports.pakistan.autonomous import _check_existing
        from annual_reports.pakistan.config import PakistanConfig

        config = PakistanConfig(
            drive_root=tmp_path / "Drive",
            pakistan_root=tmp_path / "Drive" / "Pakistan stock",
            local_runtime=tmp_path / "runtime",
            state_path=tmp_path / "runtime" / "harvest.sqlite3",
        )
        c = PakistanCompany(company_name="Systems Limited", psx_symbol="SYS")
        rep = PakistanReport(company=c, fiscal_year=2024, pdf_url="https://example.com/ar.pdf")

        # Simulate pre-existing file on Drive
        target = config.pakistan_root / rep.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"%PDF-1.4 simulated pdf")

        assert _check_existing(config, rep)

    def test_folder_cleanliness_verification(self, tmp_path: Path):
        """Company folders should contain ONLY .pdf files, no stray logs or temp files."""
        from annual_reports.pakistan.audit import AuditManager
        from annual_reports.pakistan.config import PakistanConfig

        config = PakistanConfig(
            drive_root=tmp_path,
            pakistan_root=tmp_path / "Pakistan stock",
            local_runtime=tmp_path / "runtime",
            state_path=tmp_path / "state.sqlite3",
        )
        profile_store = SourceProfileStore(tmp_path / "prof.sqlite3")
        mgr = AuditManager(config, profile_store)

        # Clean folder with pdf
        comp_dir = config.pakistan_root / "HBL [HBL]" / "FY2024"
        comp_dir.mkdir(parents=True, exist_ok=True)
        (comp_dir / "report.pdf").write_bytes(b"pdf")

        violations = mgr.verify_folder_cleanliness(config.pakistan_root)
        assert len(violations) == 0

        # Inject dirty file
        (comp_dir / "debug.log").write_text("junk")
        violations2 = mgr.verify_folder_cleanliness(config.pakistan_root)
        assert len(violations2) == 1
        assert "debug.log" in violations2[0]
        profile_store.close()
