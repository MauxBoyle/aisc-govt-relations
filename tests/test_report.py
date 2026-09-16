"""Tests for Illinois membership report data and PDF generation."""

from pathlib import Path

import pytest
from pypdf import PdfReader

from aisc_gr_statistics.report import (
    CERTIFICATION_CATEGORY_PLACEHOLDER,
    PLACEHOLDER,
    Company,
    ReportDataError,
    build_report_companies,
    normalize_company_name,
    read_imis_companies,
    render_illinois_report,
)


def write_csv(tmp_path, contents):
    """Create a small CSV fixture for a focused test."""
    path = tmp_path / "members.csv"
    path.write_text(contents, encoding="utf-8")
    return path


def test_reads_common_headers_filters_illinois_and_sorts(tmp_path):
    path = write_csv(
        tmp_path,
        "Company Name,Membership Type,Annual Structural Steel Tonnage,State,Congressional District\n"
        "Zulu Steel,Producer,30,IL,12\n"
        "Other Steel,Producer,40,IN,3\n"
        "Alpha Steel,Fabricator,20,Illinois,7\n",
    )

    companies = read_imis_companies(path)

    assert [company.name for company in companies] == ["Alpha Steel", "Zulu Steel"]
    assert companies[0].membership_type == "Fabricator"
    assert companies[0].district == "7"


def test_reads_actual_imis_headers_and_totals_three_tonnage_columns(tmp_path):
    path = write_csv(
        tmp_path,
        "Full Name,State Province,Full Address,Member Type,US Congress,Bridge Tonnage,Building Tonnage,S C Tonnage\n"
        'Example Steel,IL,"1 Main St",Producer,7,"1,200",300,25.5\n',
    )

    companies = read_imis_companies(path)

    assert companies == [
        Company(
            name="Example Steel",
            state="IL",
            address="1 Main St",
            membership_type="Producer",
            district="7",
            tonnage="1,525.5",
        )
    ]


def test_rejects_invalid_actual_imis_tonnage(tmp_path):
    path = write_csv(
        tmp_path,
        "Full Name,State Province,Bridge Tonnage,Building Tonnage,S C Tonnage\n"
        "Example Steel,IL,not-a-number,2,3\n",
    )

    with pytest.raises(ReportDataError, match="Bridge Tonnage"):
        read_imis_companies(path)


def test_ignores_non_illinois_rows_without_company_names(tmp_path):
    path = write_csv(
        tmp_path,
        "Full Name,State Province,Bridge Tonnage,Building Tonnage,S C Tonnage\n"
        ",OH,1,2,3\n"
        "Example Steel,IL,1,2,3\n",
    )

    assert [company.name for company in read_imis_companies(path)] == ["Example Steel"]


def test_explains_when_an_illinois_row_has_no_company_name(tmp_path):
    path = write_csv(tmp_path, "Full Name,State Province\n,IL\n")

    with pytest.raises(ReportDataError, match="company-name data"):
        read_imis_companies(path)


@pytest.mark.parametrize(
    "contents, message",
    [
        ("State\nIL\n", "company name"),
        ("Company Name\nExample Steel\n", "state"),
        ("Company Name,State\n,IL\n", "company name"),
        ("Company Name,State\nExample Steel,\n", "state"),
    ],
)
def test_requires_company_name_and_state(tmp_path, contents, message):
    with pytest.raises(ReportDataError, match=message):
        read_imis_companies(write_csv(tmp_path, contents))


def test_report_data_uses_placeholders_and_unique_normalized_salesforce_match():
    companies = [
        Company(name="Example  Steel, Inc.", state="IL"),
        Company(name="No Match Steel", state="IL"),
        Company(name="Ambiguous Steel", state="IL"),
    ]
    accounts = [
        {"Name": "example steel inc", "Cert_Certification_Status__c": "Certified"},
        {"Name": "Ambiguous Steel", "Cert_Certification_Status__c": "Initials"},
        {"Name": "ambiguous-steel", "Cert_Certification_Status__c": "Certified"},
    ]

    rows = build_report_companies(companies, accounts)

    assert rows[0].certification_status == "Certified"
    assert rows[0].address == PLACEHOLDER
    assert rows[0].certification_category == CERTIFICATION_CATEGORY_PLACEHOLDER
    assert rows[1].certification_status == PLACEHOLDER
    assert rows[2].certification_status == PLACEHOLDER
    assert normalize_company_name("Example  Steel, Inc.") == "example steel inc"


def test_report_data_without_salesforce_records_has_status_placeholder():
    rows = build_report_companies([Company(name="Example Steel", state="IL")])

    assert rows[0].certification_status == PLACEHOLDER


def test_rendered_pdf_contains_report_text_and_placeholders(tmp_path):
    output = tmp_path / "illinois.pdf"
    companies = read_imis_companies(Path("tests/fixtures/imis-membership-sample.csv"))
    rows = build_report_companies(
        companies,
        [
            {
                "Name": "Example Steel Company",
                "Cert_Certification_Status__c": "Certified",
            }
        ],
    )

    render_illinois_report(rows, output)

    reader = PdfReader(output)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert output.read_bytes().startswith(b"%PDF")
    assert "Illinois Certification & Membership Report" in text
    assert "Illinois" in text
    assert "Example Steel Company" in text
    assert "Membership type: Producer" in text
    assert "Certification status: Certified" in text
    assert CERTIFICATION_CATEGORY_PLACEHOLDER in text
    assert "[PLACEHOLDER: U.S. Senators needed]" in text
    assert "[PLACEHOLDER: U.S. Representatives needed]" in text
