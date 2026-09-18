"""End-to-end regressions for the data-quality examples in issue #33.

These tests use small, local CSV files and in-memory Salesforce records.  That
makes the important report rules quick to check without needing either live
system.
"""

import csv
from dataclasses import replace
from datetime import date

from pypdf import PdfReader

from aisc_gr_statistics.report import (
    Company,
    CompanyClassification,
    build_reconciliation_rows,
    build_report_companies,
    combine_companies,
    combined_conflicts,
    find_undefined_imis_codes,
    read_imis_companies_with_tonnage_review,
    render_illinois_report,
    write_undefined_imis_codes_csv,
)

REPORT_DATE = date(2026, 9, 17)


def _active_certification(name: str) -> dict[str, str]:
    """Return a child certification that is active on ``REPORT_DATE``."""
    return {
        "Name": name,
        "Status__c": "Active",
        "Start_Date__c": "2026-01-01",
        "End_Date__c": "2026-12-31",
    }


def _write_imis_csv(tmp_path, rows: list[dict[str, str]]):
    """Write the smallest complete dated iMIS export needed by these tests."""
    path = tmp_path / "imis.csv"
    fieldnames = [
        "iMIS ID",
        "Full Name",
        "State Province",
        "City",
        "Member Type",
        "Category",
        "Tonnage Year",
        "Submission Date",
        "Bridge Tonnage",
        "Building Tonnage",
        "S C Tonnage",
    ]
    with path.open("w", newline="", encoding="utf-8") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_issue_33_named_records_follow_id_tonnage_and_unknown_code_rules(tmp_path):
    """Exercise A. Lucas, A&H Steel, annual tonnage, and unknown code examples."""
    imis_csv = _write_imis_csv(
        tmp_path,
        [
            {
                "iMIS ID": "LUCAS-1",
                "Full Name": "A. Lucas Steel",
                "State Province": "IL",
                "City": "Chicago",
                "Member Type": "ACT",
                "Category": "FAB",
                "Tonnage Year": "2025",
                "Submission Date": submission_date,
                "Bridge Tonnage": "100",
                "Building Tonnage": "0",
                "S C Tonnage": "0",
            }
            for submission_date in (
                "2025-03-31",
                "2025-06-30",
                "2025-09-30",
                "2025-12-31",
            )
        ]
        + [
            {
                "iMIS ID": "LUCAS-1",
                "Full Name": "A. Lucas Steel Old Year",
                "State Province": "IL",
                "City": "Chicago",
                "Member Type": "ACT",
                "Category": "FAB",
                "Tonnage Year": "2024",
                "Submission Date": "2024-12-31",
                "Bridge Tonnage": "999",
                "Building Tonnage": "0",
                "S C Tonnage": "0",
            },
            {
                "iMIS ID": "UNKNOWN-1",
                "Full Name": "Unknown Code Steel",
                "State Province": "IL",
                "City": "Aurora",
                "Member Type": "MYSTERY",
                "Category": "ODD",
                "Tonnage Year": "2025",
                "Submission Date": "2025-06-01",
                "Bridge Tonnage": "1",
                "Building Tonnage": "0",
                "S C Tonnage": "0",
            },
        ],
    )
    imis_companies, findings, tonnage_year = read_imis_companies_with_tonnage_review(
        imis_csv, report_date=REPORT_DATE
    )
    accounts = [
        {
            "Id": "sf-lucas",
            "IMISID__c": "LUCAS-1",
            "Name": "A. Lucas Steel",
            "BillingCity": "Chicago",
            "BillingState": "IL",
            "Cert_Certification_Status__c": "Certified",
            "Certifications__r": {
                "records": [
                    _active_certification("Building Fabricator"),
                    _active_certification("Erector"),
                ]
            },
        },
        {
            "Id": "sf-lookalike",
            "IMISID__c": "LOOKALIKE-2",
            "Name": "A. Lucas Steel",
            "BillingCity": "Chicago",
            "BillingState": "IL",
        },
        {
            "Id": "sf-ah",
            "Name": "A&H Steel",
            "BillingCity": "Joliet",
            "BillingState": "IL",
            "Cert_Certification_Status__c": "Certified",
            "Certifications__r": {"records": [_active_certification("Erector")]},
        },
    ]

    combined = combine_companies(imis_companies, accounts)
    report_companies = build_report_companies(combined, as_of=REPORT_DATE)
    lucas = next(
        company for company in report_companies if company.name == "A. Lucas Steel"
    )
    ah_steel = next(
        company for company in report_companies if company.name == "A&H Steel"
    )

    assert tonnage_year == 2025
    assert findings == []
    assert lucas.tonnage == "400"
    assert lucas.certification_categories == ("Building Fabricator", "Erector")
    assert not any(
        row.classification is CompanyClassification.BOTH
        and row.salesforce
        and row.salesforce["Id"] == "sf-lookalike"
        for row in combined
    )
    assert ah_steel.membership_type == ""
    assert ah_steel.tonnage == ""
    assert ah_steel.certification_categories == ("Erector",)

    output = tmp_path / "named-records.pdf"
    render_illinois_report(report_companies, output, tonnage_year=tonnage_year)
    pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(output).pages)
    assert "2025 Structural Steel Tonnage: 400 Tons" in pdf_text
    assert "A. Lucas Steel Old Year" not in pdf_text
    assert "999 Tons" not in pdf_text

    unknown_codes_csv = tmp_path / "unknown-imis-codes.csv"
    write_undefined_imis_codes_csv(
        find_undefined_imis_codes(imis_csv), unknown_codes_csv
    )
    unknown_codes = unknown_codes_csv.read_text(encoding="utf-8")
    assert "Type,MYSTERY,unknown,1" in unknown_codes
    assert "Category,ODD,unknown,1" in unknown_codes


def test_issue_33_duplicate_nonblank_ids_stay_unjoined_and_are_flagged():
    """A duplicate shared ID must be reviewed instead of joined by name."""
    imis_companies = [
        Company(
            name="Duplicate Steel One", state="IL", city="Chicago", imis_id="DUP-1"
        ),
        Company(
            name="Duplicate Steel Two", state="IL", city="Chicago", imis_id="DUP-1"
        ),
    ]
    accounts = [
        {
            "Id": "sf-duplicate",
            "IMISID__c": "DUP-1",
            "Name": "Duplicate Steel One",
            "BillingCity": "Chicago",
            "BillingState": "IL",
        }
    ]

    combined = combine_companies(imis_companies, accounts)
    reconciliation = build_reconciliation_rows(combined)

    assert all(row.classification is not CompanyClassification.BOTH for row in combined)
    assert [conflict.field for conflict in combined_conflicts(combined)] == [
        "duplicate iMIS ID"
    ]
    assert all("duplicate iMIS ID" in row.issues for row in reconciliation)


def test_issue_33_pdf_is_letter_sized_extractable_and_keeps_later_cards_readable(
    tmp_path,
):
    """Check durable PDF properties instead of brittle byte-for-byte output."""
    long_name = "Illinois Structural Steel Fabrication Company " * 5
    long_address = (
        "12345 Very Long Industrial Parkway, Suite 400, Chicago, IL 60601 " * 3
    )
    long_certification = "Extended Certification Scope for Complex Steel Work " * 4
    report_companies = build_report_companies(
        [
            Company(
                name=long_name.strip(),
                state="IL",
                city="Chicago",
                address=long_address.strip(),
                imis_id="LONG-1",
                membership_type="Full AISC Member Fabricator",
                tonnage="1,250",
            ),
            *[
                Company(
                    name=f"Later Company {number}",
                    state="IL",
                    imis_id=f"LATER-{number}",
                )
                for number in range(20)
            ],
        ]
    )
    report_companies[0] = replace(
        report_companies[0],
        certification_categories=(long_certification.strip(),),
    )
    output = tmp_path / "issue-33-report.pdf"

    render_illinois_report(report_companies, output, tonnage_year=2025)

    reader = PdfReader(output)
    layout_text = " ".join(
        " ".join(page.extract_text(extraction_mode="layout").split())
        for page in reader.pages
    )
    readable_text = " ".join(
        " ".join((page.extract_text() or "").split()) for page in reader.pages
    )
    assert output.read_bytes().startswith(b"%PDF")
    assert all(float(page.mediabox.width) == 612 for page in reader.pages)
    assert all(float(page.mediabox.height) == 792 for page in reader.pages)
    assert "AISC Certification and Membership Summary: Illinois" in layout_text
    assert "Later Company 19" in layout_text
    assert long_name.strip() in readable_text
    assert long_address.strip() in readable_text
    assert long_certification.strip() in readable_text
    assert "Later Company 19" in readable_text
    assert len(reader.pages) > 1
