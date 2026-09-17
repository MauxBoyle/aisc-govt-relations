"""Tests for Illinois membership report data and PDF generation."""

import csv
from datetime import date
from io import StringIO
from pathlib import Path

import pytest
from pypdf import PdfReader

from aisc_gr_statistics.imis_fields import (
    CATEGORY_LABELS,
    MEMBERSHIP_TYPE_LABELS,
    category_label,
    membership_label,
    membership_type_label,
    scan_undefined_imis_codes,
)
from aisc_gr_statistics.report import (
    CERTIFICATION_CATEGORY_PLACEHOLDER,
    PLACEHOLDER,
    Company,
    CompanyClassification,
    ReportDataError,
    build_reconciliation_rows,
    build_report_companies,
    candidate_matches,
    combine_companies,
    combined_conflicts,
    find_undefined_imis_codes,
    normalize_company_name,
    read_imis_companies,
    read_imis_companies_with_tonnage_review,
    render_illinois_report,
    write_candidate_matches_csv,
    write_conflicts_csv,
    write_reconciliation_csv,
    write_reconciliation_log,
    write_tonnage_review_csv,
    write_undefined_imis_codes_csv,
)


def write_csv(tmp_path, contents):
    """Create a small CSV fixture for a focused test."""
    path = tmp_path / "members.csv"
    # Older focused tests exercise fields unrelated to the now-required
    # shared-ID and city headers.  Supply blank values for those columns.
    rows = list(csv.reader(StringIO(contents)))
    headers = rows[0]
    for header in ("iMIS ID", "City"):
        if header not in headers:
            headers.append(header)
            for row in rows[1:]:
                row.append("")
    with path.open("w", newline="", encoding="utf-8") as file_handle:
        csv.writer(file_handle).writerows(rows)
    return path


def test_translates_confirmed_imis_membership_type_codes():
    assert MEMBERSHIP_TYPE_LABELS == {
        "ACT": "Full Member",
        "ACTB": "Full Member Branch",
        "ASSOC": "Associate Member",
        "ASSCB": "Associate Member Branch",
    }
    assert membership_type_label("act") == "Full Member"
    assert membership_type_label("Unrecognized") == ""


def test_translates_confirmed_imis_category_codes():
    assert CATEGORY_LABELS == {
        "BEND": "Bender",
        "DERC": "Erector",
        "DET1": "Detailer",
        "DET10": "Detailer",
        "EQPM": "Equipment Manufacturer",
        "EREC": "Erector",
        "FAB": "Fabricator",
        "SUPP": "Supplier",
        "COTM": "Supplier",
        "WELD": "Detailer",
        "SOFT": "Software",
        "BOLT": "Bolt Manufacturer",
        "MILL": "Steel Mill",
        "SVC": "Service Center",
        "JOIS": "Joist Manufacturer",
    }
    assert category_label("erec") == "Erector"
    assert category_label("Unknown") == ""


def test_combines_membership_type_and_category_labels():
    assert membership_label("ACT", "FAB") == "Full Member Fabricator"
    assert membership_label("ACTB", "") == "Full Member Branch"
    assert membership_label("", "SOFT") == "Software"
    assert membership_label("unknown", "FAB") == "Fabricator"
    assert membership_label("ACT", "unknown") == "Full Member"
    assert membership_label("unknown", "also unknown") == ""


def test_reads_and_combines_membership_type_and_category(tmp_path):
    path = write_csv(
        tmp_path,
        "Full Name,State Province,Member Type,Category\n"
        "Example Steel,IL,ACT,EREC\n",
    )

    companies = read_imis_companies(path)

    assert companies[0].membership_type == "Full Member Erector"


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
    assert companies[0].membership_type == ""
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
            membership_type="",
            district="7",
            tonnage="1,525.5",
        )
    ]


def test_aggregates_unique_completed_year_submissions_and_uses_latest_details(tmp_path):
    path = write_csv(
        tmp_path,
        "iMIS ID,Full Name,State Province,City,Tonnage Year,Submission Date,Bridge Tonnage,Building Tonnage,S C Tonnage\n"
        "A,Older Name,IL,Chicago,2025,2025-01-01,10,20,30\n"
        "A,Latest Name,IL,Aurora,2025,2025-12-01,1,2,3\n"
        "A,Old Year,IL,Chicago,2024,2024-12-01,100,100,100\n"
        "A,Current Year,IL,Chicago,2026,2026-01-01,100,100,100\n",
    )

    companies, findings, tonnage_year = read_imis_companies_with_tonnage_review(
        path, report_date=date(2026, 9, 17)
    )

    assert tonnage_year == 2025
    assert findings == []
    assert companies == [
        Company(
            name="Latest Name", state="IL", city="Aurora", imis_id="A", tonnage="66"
        )
    ]


def test_aggregates_selected_year_am_pm_submissions(tmp_path):
    path = write_csv(
        tmp_path,
        "iMIS ID,Full Name,State Province,City,Tonnage Year,Submission Date,Bridge Tonnage,Building Tonnage,S C Tonnage\n"
        "AM-1,Morning Steel,IL,Chicago,2025,5/7/2025 9:30:00 AM,100,50,25\n"
        "AM-2,Afternoon Steel,IL,Aurora,2025,6/15/2025 1:45:00 PM,200,75,50\n",
    )

    companies, findings, tonnage_year = read_imis_companies_with_tonnage_review(
        path, report_date=date(2026, 9, 17)
    )

    assert tonnage_year == 2025
    assert findings == []
    assert sum(int(company.tonnage) for company in companies) == 500


def test_treats_equivalent_24_hour_and_am_pm_timestamps_as_duplicates(tmp_path):
    path = write_csv(
        tmp_path,
        "iMIS ID,Full Name,State Province,City,Tonnage Year,Submission Date,Bridge Tonnage,Building Tonnage,S C Tonnage\n"
        "DUP-1,Duplicate Steel,IL,Chicago,2025,2025-01-15 13:30:00,1,2,3\n"
        "DUP-1,Duplicate Steel,IL,Chicago,2025,01/15/2025 01:30:00 PM,1,2,3\n",
    )

    companies, findings, _ = read_imis_companies_with_tonnage_review(
        path, report_date=date(2026, 1, 1)
    )

    assert companies == [
        Company(
            name="Duplicate Steel", state="IL", city="Chicago", imis_id="DUP-1", tonnage="6"
        )
    ]
    assert [(finding.submission_date, finding.reason) for finding in findings] == [
        ("01/15/2025 01:30:00 PM", "exact duplicate submission key")
    ]


def test_excludes_invalid_submission_timestamp_and_preserves_it_in_review(tmp_path):
    path = write_csv(
        tmp_path,
        "iMIS ID,Full Name,State Province,City,Tonnage Year,Submission Date,Bridge Tonnage,Building Tonnage,S C Tonnage\n"
        "BAD-1,Invalid Date Steel,IL,Chicago,2025,01/15/2025 13:30:00 PM,1,2,3\n",
    )

    companies, findings, _ = read_imis_companies_with_tonnage_review(
        path, report_date=date(2026, 1, 1)
    )

    assert companies == []
    assert [(finding.submission_date, finding.reason) for finding in findings] == [
        ("01/15/2025 13:30:00 PM", "invalid submission date: '01/15/2025 13:30:00 PM'")
    ]


def test_excludes_duplicate_and_conflicting_annual_submission_keys_for_review(tmp_path):
    path = write_csv(
        tmp_path,
        "iMIS ID,Full Name,State Province,City,Tonnage Year,Submission Date,Bridge Tonnage,Building Tonnage,S C Tonnage\n"
        "A,Acme,IL,Chicago,2025,2025-01-01,1,2,3\n"
        "A,Acme,IL,Chicago,2025,2025-01-01,1,2,3\n"
        "A,Acme,IL,Chicago,2025,2025-02-01,4,5,6\n"
        "A,Acme,IL,Chicago,2025,2025-02-01,4,5,7\n"
        "B,Blank Date,IL,Chicago,2025,,4,5,6\n",
    )

    companies, findings, _ = read_imis_companies_with_tonnage_review(
        path, report_date=date(2026, 1, 1)
    )

    assert companies == [Company(name="Acme", state="IL", city="Chicago", imis_id="A", tonnage="6")]
    assert {(finding.imis_id, finding.reason) for finding in findings} == {
        ("A", "exact duplicate submission key"),
        ("A", "conflicting tonnage for submission key"),
        ("B", "missing submission date"),
    }
    assert sum(finding.reason == "conflicting tonnage for submission key" for finding in findings) == 2
    output = tmp_path / "tonnage-review.csv"
    write_tonnage_review_csv(findings, output)
    assert "reason" in output.read_text(encoding="utf-8")


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


def test_imis_requires_shared_id_and_city_headers_but_allows_blank_values(tmp_path):
    path = tmp_path / "members.csv"
    path.write_text("Company Name,State\nExample Steel,IL\n", encoding="utf-8")
    with pytest.raises(ReportDataError, match="shared iMIS ID, city.*Re-export"):
        read_imis_companies(path)

    path.write_text(
        "Company Name,State,City,iMIS ID\nExample Steel,IL,,\n", encoding="utf-8"
    )
    assert read_imis_companies(path)[0].imis_id == ""


def test_combined_model_joins_by_id_preserves_sources_and_reports_conflicts(tmp_path):
    imis = Company(
        name="Acme Steel", state="IL", city="Chicago", imis_id="42", address="1 iMIS Way"
    )
    account = {
        "Id": "001", "IMISID__c": "42", "Name": "ACME Structural",
        "BillingCity": "Evanston", "BillingState": "IL", "BillingStreet": "2 SF Way",
    }
    combined = combine_companies([imis], [account])

    assert combined[0].classification is CompanyClassification.BOTH
    assert combined[0].imis == imis
    assert combined[0].salesforce == account
    assert {(item.field, item.imis_value, item.salesforce_value) for item in combined_conflicts(combined)} == {
        ("name", "Acme Steel", "ACME Structural"), ("city", "Chicago", "Evanston")
    }
    row = build_report_companies(combined)[0]
    assert row.name == "ACME Structural"
    assert row.address == "iMIS: 1 iMIS Way | Salesforce: 2 SF Way, Evanston, IL"
    output = tmp_path / "conflict.pdf"
    render_illinois_report([row], output, tonnage_year=2025)
    text = "\n".join(page.extract_text() or "" for page in PdfReader(output).pages)
    assert "ACME Structural" in text
    assert "iMIS: Acme Steel" not in text
    assert "tonnage (2025)" in text


def test_candidate_matches_require_name_city_and_state_and_never_change_join():
    imis = Company(name="Acme Steel", state="IL", city="Chicago", imis_id="A")
    account = {"Id": "001", "IMISID__c": "B", "Name": "Acme-Steel", "BillingCity": "Chicago", "BillingState": "Illinois"}
    combined = combine_companies([imis], [account])

    assert [row.classification for row in combined] == [
        CompanyClassification.IMIS_ONLY, CompanyClassification.SALESFORCE_ONLY
    ]
    assert candidate_matches(combined)[0].salesforce_account_id == "001"

    no_city = combine_companies([imis], [{**account, "BillingCity": "Aurora"}])
    assert candidate_matches(no_city) == []


def test_joins_only_exact_trimmed_text_ids_and_preserves_leading_zeroes():
    imis = Company(name="Leading Zero Steel", state="IL", imis_id=" 00123 ")
    accounts = [
        {"Id": "matching", "IMISID__c": "00123", "Name": "Leading Zero Steel", "BillingState": "IL"},
        {"Id": "different", "IMISID__c": "123", "Name": "Different Steel", "BillingState": "IL"},
    ]

    combined = combine_companies([imis], accounts)

    assert [row.classification for row in combined] == [
        CompanyClassification.BOTH,
        CompanyClassification.SALESFORCE_ONLY,
    ]
    assert combined[0].shared_imis_id == "00123"
    assert combined[0].salesforce == accounts[0]


@pytest.mark.parametrize(
    "companies, accounts, expected_classifications, expected_source, expected_detail",
    [
        (
            [
                Company(name="Duplicate One", state="IL", city="Chicago", imis_id="DUP"),
                Company(name="Duplicate Two", state="IL", city="Chicago", imis_id="DUP"),
            ],
            [{"Id": "sf-1", "IMISID__c": "DUP", "Name": "Duplicate One", "BillingCity": "Chicago", "BillingState": "IL"}],
            [CompanyClassification.IMIS_ONLY, CompanyClassification.IMIS_ONLY, CompanyClassification.SALESFORCE_ONLY],
            CompanyClassification.IMIS_ONLY,
            "2 iMIS records",
        ),
        (
            [Company(name="Duplicate One", state="IL", city="Chicago", imis_id="DUP")],
            [
                {"Id": "sf-1", "IMISID__c": "DUP", "Name": "Duplicate One", "BillingCity": "Chicago", "BillingState": "IL"},
                {"Id": "sf-2", "IMISID__c": "DUP", "Name": "Duplicate Two", "BillingCity": "Chicago", "BillingState": "IL"},
            ],
            [CompanyClassification.IMIS_ONLY, CompanyClassification.SALESFORCE_ONLY, CompanyClassification.SALESFORCE_ONLY],
            CompanyClassification.SALESFORCE_ONLY,
            "2 Salesforce records",
        ),
    ],
)
def test_duplicate_nonblank_ids_remain_source_only_and_create_review_conflicts(
    companies, accounts, expected_classifications, expected_source, expected_detail
):
    combined = combine_companies(companies, accounts)

    assert [row.classification for row in combined] == expected_classifications
    duplicate_conflicts = [
        conflict for conflict in combined_conflicts(combined)
        if conflict.field == "duplicate iMIS ID"
    ]
    assert [(conflict.shared_imis_id, conflict.company_classification, conflict.imis_value, conflict.salesforce_value) for conflict in duplicate_conflicts] == [
        ("DUP", expected_source, expected_detail if expected_source is CompanyClassification.IMIS_ONLY else "", expected_detail if expected_source is CompanyClassification.SALESFORCE_ONLY else "")
    ]


def test_candidate_matches_include_different_or_missing_ids_but_exclude_equal_duplicates():
    imis = [
        Company(name="Missing ID Steel", state="IL", city="Chicago"),
        Company(name="Different ID Steel", state="IL", city="Aurora", imis_id="iMIS-1"),
        Company(name="Duplicate Steel", state="IL", city="Joliet", imis_id="DUP"),
        Company(name="Duplicate Steel Two", state="IL", city="Joliet", imis_id="DUP"),
    ]
    accounts = [
        {"Id": "missing", "Name": "Missing ID Steel", "BillingCity": "Chicago", "BillingState": "IL"},
        {"Id": "different", "IMISID__c": "sf-1", "Name": "Different-ID Steel", "BillingCity": "Aurora", "BillingState": "IL"},
        {"Id": "duplicate", "IMISID__c": "DUP", "Name": "Duplicate Steel", "BillingCity": "Joliet", "BillingState": "IL"},
    ]

    matches = candidate_matches(combine_companies(imis, accounts))

    assert {(match.imis_id, match.salesforce_account_id) for match in matches} == {
        ("", "missing"),
        ("iMIS-1", "different"),
    }


def test_duplicate_conflicts_are_written_to_the_existing_conflicts_csv(tmp_path):
    combined = combine_companies(
        [
            Company(name="One", state="IL", imis_id="DUP"),
            Company(name="Two", state="IL", imis_id="DUP"),
        ]
    )
    output = tmp_path / "conflicts.csv"

    write_conflicts_csv(combined_conflicts(combined), output)

    assert list(csv.reader(output.open(encoding="utf-8"))) == [
        ["shared iMIS ID", "company classification", "field", "iMIS value", "Salesforce value"],
        ["DUP", "imis-only", "duplicate iMIS ID", "2 iMIS records", ""],
    ]


def test_review_csvs_write_required_headers_even_when_empty(tmp_path):
    conflicts = tmp_path / "conflicts.csv"
    candidates = tmp_path / "candidates.csv"
    unknown_codes = tmp_path / "unknown-imis-codes.csv"
    write_conflicts_csv([], conflicts)
    write_candidate_matches_csv([], candidates)
    write_undefined_imis_codes_csv([], unknown_codes)
    assert conflicts.read_text(encoding="utf-8") == (
        "shared iMIS ID,company classification,field,iMIS value,Salesforce value\n"
    )
    assert candidates.read_text(encoding="utf-8").startswith(
        "iMIS ID,Salesforce Account ID,iMIS name,Salesforce name,"
    )
    assert unknown_codes.read_text(encoding="utf-8") == (
        "iMIS field,iMIS code,status,occurrences\n"
    )


def test_scans_undefined_imis_codes_case_insensitively_and_aggregates():
    findings = scan_undefined_imis_codes(
        [
            ("Type", " act "),
            ("Type", "Other"),
            ("Type", "other"),
            ("Type", ""),
            ("Category", "fab"),
            ("Category", "UNCONFIRMED"),
            ("Category", " "),
        ]
    )

    assert [
        (finding.field, finding.code, finding.status, finding.occurrences)
        for finding in findings
    ] == [
        ("Category", "", "blank", 1),
        ("Category", "UNCONFIRMED", "unknown", 1),
        ("Type", "", "blank", 1),
        ("Type", "OTHER", "unknown", 2),
    ]


def test_scans_every_export_row_for_undefined_imis_codes(tmp_path):
    path = write_csv(
        tmp_path,
        "Company Name,State,Member Type,Category\n"
        "Illinois Known,IL,ACT,FAB\n"
        "Indiana Unknown,IN, other ,UNCONFIRMED\n"
        "Ohio Blank,OH,,\n",
    )

    findings = find_undefined_imis_codes(path)

    assert [
        (finding.field, finding.code, finding.status, finding.occurrences)
        for finding in findings
    ] == [
        ("Category", "", "blank", 1),
        ("Category", "UNCONFIRMED", "unknown", 1),
        ("Type", "", "blank", 1),
        ("Type", "OTHER", "unknown", 1),
    ]


def test_reconciliation_rows_include_every_company_and_report_review_issues(tmp_path):
    companies = [
        Company(name="Acme Steel", state="IL", imis_id="MATCH"),
        Company(name="No ID Steel", state="IL"),
        Company(name="Duplicate One", state="IL", imis_id="DUP"),
        Company(name="Duplicate Two", state="IL", imis_id="DUP"),
    ]
    accounts = [
        {"Id": "001", "IMISID__c": "MATCH", "Name": "ACME Structural", "BillingState": "IL"},
        {"Id": "002", "Name": "Salesforce No ID", "BillingState": "IL"},
        {"Id": "003", "IMISID__c": "DUP", "Name": "Duplicate SF", "BillingState": "IL"},
    ]

    rows = build_reconciliation_rows(combine_companies(companies, accounts))

    assert [(row.classification, row.imis_id, row.salesforce_account_id, row.issues) for row in rows] == [
        ("matched", "MATCH", "001", ("name difference",)),
        ("imis-only", "", "", ("missing iMIS ID",)),
        ("imis-only", "DUP", "", ("duplicate iMIS ID",)),
        ("imis-only", "DUP", "", ("duplicate iMIS ID",)),
        ("salesforce-only", "", "002", ("missing iMIS ID",)),
        ("salesforce-only", "DUP", "003", ("duplicate iMIS ID",)),
    ]

    csv_output = tmp_path / "reconciliation.csv"
    log_output = tmp_path / "reconciliation.log"
    write_reconciliation_csv(rows, csv_output)
    write_reconciliation_log(rows, log_output)

    assert list(csv.reader(csv_output.open(encoding="utf-8"))) == [
        ["classification", "shared iMIS ID", "Salesforce Account ID", "iMIS name", "Salesforce name", "issues"],
        ["matched", "MATCH", "001", "Acme Steel", "ACME Structural", "name difference"],
        ["imis-only", "", "", "No ID Steel", "", "missing iMIS ID"],
        ["imis-only", "DUP", "", "Duplicate One", "", "duplicate iMIS ID"],
        ["imis-only", "DUP", "", "Duplicate Two", "", "duplicate iMIS ID"],
        ["salesforce-only", "", "002", "", "Salesforce No ID", "missing iMIS ID"],
        ["salesforce-only", "DUP", "003", "", "Duplicate SF", "duplicate iMIS ID"],
    ]
    log = log_output.read_text(encoding="utf-8")
    assert "Matched records: 1" in log
    assert "iMIS-only records: 3" in log
    assert "Salesforce-only records: 2" in log
    assert "Distinct duplicate iMIS IDs: 1" in log
    assert "Records missing iMIS IDs: 2" in log
    assert "ID-matched name differences: 1" in log
    assert "Acme Steel | ACME Structural" in log
    assert "Duplicate One" in log


def test_reconciliation_writers_keep_empty_outputs_reviewable(tmp_path):
    csv_output = tmp_path / "reconciliation.csv"
    log_output = tmp_path / "reconciliation.log"

    write_reconciliation_csv([], csv_output)
    write_reconciliation_log([], log_output)

    assert csv_output.read_text(encoding="utf-8") == (
        "classification,shared iMIS ID,Salesforce Account ID,iMIS name,Salesforce name,issues\n"
    )
    log = log_output.read_text(encoding="utf-8")
    assert "Matched records: 0" in log
    assert "Questionable records:" in log


def test_reconciliation_ignores_cosmetic_name_differences():
    imis = Company(name="Example  Steel, Inc.", state="IL", imis_id="A")
    account = {"Id": "001", "IMISID__c": "A", "Name": "example-steel inc", "BillingState": "IL"}

    row = build_reconciliation_rows(combine_companies([imis], [account]))[0]

    assert row.issues == ()


def test_report_data_uses_only_shared_imis_id_not_a_similar_name():
    companies = [
        Company(name="Example  Steel, Inc.", state="IL", imis_id="A"),
        Company(name="No Match Steel", state="IL"),
        Company(name="Ambiguous Steel", state="IL"),
    ]
    accounts = [
        {"Name": "example steel inc", "IMISID__c": "B", "BillingState": "IL", "Cert_Certification_Status__c": "Certified"},
        {"Name": "Ambiguous Steel", "Cert_Certification_Status__c": "Initials"},
        {"Name": "ambiguous-steel", "Cert_Certification_Status__c": "Certified"},
    ]

    rows = build_report_companies(companies, accounts)

    assert rows[0].certification_status == PLACEHOLDER
    assert rows[0].address == PLACEHOLDER
    assert rows[0].certification_categories == (CERTIFICATION_CATEGORY_PLACEHOLDER,)
    assert rows[1].certification_status == PLACEHOLDER
    assert rows[2].certification_status == PLACEHOLDER
    assert normalize_company_name("Example  Steel, Inc.") == "example steel inc"


def test_report_data_without_salesforce_records_has_status_placeholder():
    rows = build_report_companies([Company(name="Example Steel", state="IL")])

    assert rows[0].certification_status == PLACEHOLDER


def test_report_uses_only_active_nested_certifications_and_adds_salesforce_only_il_companies():
    """Keep child categories separate and omit invalid child certification rows."""
    companies = [Company(name="A. Lucas & Sons Steel", state="IL", imis_id="LUCAS")]
    accounts = [
        {
            "Name": "A. Lucas & Sons Steel",
            "IMISID__c": "LUCAS",
            "Cert_Certification_Status__c": "Certified",
            "BillingState": "IL",
            "Certifications__r": {
                "records": [
                    {"Name": "Building Fabricator", "Status__c": "Active", "Start_Date__c": "2026-01-01", "End_Date__c": "2026-12-31"},
                    {"Name": "Highway Component Manufacturer", "Status__c": "Active", "Start_Date__c": "2026-06-01", "End_Date__c": "2026-06-01"},
                    {"Name": "Inactive Certification", "Status__c": "Inactive", "Start_Date__c": "2026-01-01", "End_Date__c": "2026-12-31"},
                    {"Name": "Expired Certification", "Status__c": "Active", "Start_Date__c": "2025-01-01", "End_Date__c": "2025-12-31"},
                    {"Name": "Future Certification", "Status__c": "Active", "Start_Date__c": "2026-06-02", "End_Date__c": "2026-12-31"},
                    {"Name": "Missing Date Certification", "Status__c": "Active", "Start_Date__c": "2026-01-01", "End_Date__c": None},
                    {"Name": "Malformed Certification", "Status__c": "Active", "Start_Date__c": "nope", "End_Date__c": "2026-12-31"},
                ]
            },
        },
        {
            "Name": "A&H Steel, LLC",
            "Cert_Certification_Status__c": "Certified",
            "BillingStreet": "10 Steel Way",
            "BillingCity": "Chicago",
            "BillingState": "Illinois",
            "BillingPostalCode": "60601",
            "Certifications__r": {"records": [{"Name": "Erector", "Status__c": "Active", "Start_Date__c": "2026-01-01", "End_Date__c": "2026-12-31"}]},
        },
    ]

    rows = build_report_companies(companies, accounts, as_of=date(2026, 6, 1))

    assert rows[0].certification_categories == (
        "Salesforce: Building Fabricator",
        "Salesforce: Highway Component Manufacturer",
    )
    ah_steel = rows[1]
    assert ah_steel.name == "A&H Steel, LLC"
    assert ah_steel.address == "10 Steel Way, Chicago, Illinois 60601"
    assert ah_steel.membership_type == PLACEHOLDER
    assert ah_steel.tonnage == PLACEHOLDER
    assert ah_steel.district == PLACEHOLDER
    assert ah_steel.certification_categories == ("Salesforce: Erector",)


def test_salesforce_only_rows_require_certified_status_and_active_children():
    accounts = [
        {"Name": "No Certification", "BillingState": "IL", "Certifications__r": {"records": []}},
        {"Name": "Inactive", "BillingState": "IL", "Cert_Certification_Status__c": "Certified", "Certifications__r": {"records": [{"Name": "Inactive", "Status__c": "Inactive", "Start_Date__c": "2026-01-01", "End_Date__c": "2026-12-31"}]}},
        {"Name": "Certified Active", "BillingState": "IL", "Cert_Certification_Status__c": "Certified", "Certifications__r": {"records": [{"Name": "Fabricator", "Status__c": "Active", "Start_Date__c": "2026-01-01", "End_Date__c": "2026-12-31"}]}},
        {"Name": "Indiana Steel", "BillingState": "IN", "Certifications__r": {"records": [{"Name": "Erector", "Status__c": "Active", "Start_Date__c": "2026-01-01", "End_Date__c": "2026-12-31"}]}},
    ]

    rows = build_report_companies([], accounts, as_of=date(2026, 6, 1))
    assert [row.name for row in rows] == ["Certified Active"]


def test_ambiguous_normalized_salesforce_names_do_not_enrich_imis_company():
    rows = build_report_companies(
        [Company(name="Example Steel", state="IL")],
        [
            {"Name": "Example Steel", "Cert_Certification_Status__c": "Certified", "Certifications__r": {"records": []}},
            {"Name": "Example-Steel", "Cert_Certification_Status__c": "Initials", "Certifications__r": {"records": []}},
        ],
        as_of=date(2026, 6, 1),
    )

    assert len(rows) == 1
    assert rows[0].certification_status == PLACEHOLDER


def test_rendered_pdf_contains_report_text_and_placeholders(tmp_path):
    output = tmp_path / "illinois.pdf"
    companies = read_imis_companies(Path("tests/fixtures/imis-membership-sample.csv"))
    rows = build_report_companies(
        companies,
        [
            {
                "Name": "Example Steel Company",
                "IMISID__c": "IMIS-1",
                "BillingState": "IL",
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
    assert "Membership type: [PLACEHOLDER: unavailable]" in text
    assert "Certification status: Salesforce: Certified" in text
    assert CERTIFICATION_CATEGORY_PLACEHOLDER in text
    assert "[PLACEHOLDER: U.S. Senators needed]" in text
    assert "[PLACEHOLDER: U.S. Representatives needed]" in text


def test_rendered_pdf_lists_each_active_certification(tmp_path):
    output = tmp_path / "certifications.pdf"
    rows = build_report_companies(
        [],
        [{"Name": "A&H Steel, LLC", "BillingState": "IL", "Cert_Certification_Status__c": "Certified", "Certifications__r": {"records": [
            {"Name": "Building Fabricator", "Status__c": "Active", "Start_Date__c": "2026-01-01", "End_Date__c": "2026-12-31"},
            {"Name": "Highway Component Manufacturer", "Status__c": "Active", "Start_Date__c": "2026-01-01", "End_Date__c": "2026-12-31"},
            {"Name": "Excluded Certification", "Status__c": "Inactive", "Start_Date__c": "2026-01-01", "End_Date__c": "2026-12-31"},
        ]}}],
        as_of=date(2026, 6, 1),
    )

    render_illinois_report(rows, output)

    text = "\n".join(page.extract_text() or "" for page in PdfReader(output).pages)
    assert "A&H Steel, LLC" in text
    assert "Building Fabricator" in text
    assert "Highway Component Manufacturer" in text
    assert "Excluded Certification" not in text


def test_pdf_uses_salesforce_owned_client_type_employee_count_and_name(tmp_path):
    imis = Company(name="iMIS Name", state="IL", imis_id="1", membership_type="Full Member", tonnage="50")
    account = {
        "Id": "sf-1", "IMISID__c": "1", "Name": "Salesforce Name",
        "BillingState": "IL", "Industry": "Fabricator", "NumberOfEmployees": "12,500",
    }
    row = build_report_companies([imis], [account])[0]

    assert row.name == "Salesforce Name"
    assert row.client_type == "Fabricator"
    assert row.employee_count == "12,500"
    assert row.membership_type == "iMIS: Full Member"
    assert row.tonnage == "iMIS: 50"
    output = tmp_path / "owned-fields.pdf"
    render_illinois_report([row], output)
    text = "\n".join(page.extract_text() or "" for page in PdfReader(output).pages)
    assert "Client type: Fabricator" in text
    assert "Employee count: 12,500" in text


@pytest.mark.parametrize("employee_count", (12500, 12500.0))
def test_numeric_whole_salesforce_employee_counts_are_formatted(employee_count):
    row = build_report_companies(
        [Company(name="Example", state="IL", imis_id="1")],
        [{"IMISID__c": "1", "Name": "Example", "BillingState": "IL", "NumberOfEmployees": employee_count}],
    )[0]

    assert row.employee_count == "12,500"


@pytest.mark.parametrize(
    "employee_count", (None, "not a number", "3.5", 3.5, -1, True, float("inf"))
)
def test_invalid_salesforce_employee_counts_use_unavailable_placeholder(employee_count):
    row = build_report_companies(
        [Company(name="Example", state="IL", imis_id="1")],
        [{"IMISID__c": "1", "Name": "Example", "BillingState": "IL", "NumberOfEmployees": employee_count}],
    )[0]

    assert row.employee_count == PLACEHOLDER


def test_blank_salesforce_name_falls_back_to_imis_but_name_conflict_is_retained():
    imis = Company(name="iMIS Name", state="IL", imis_id="1")
    differing = {"Id": "sf-1", "IMISID__c": "1", "Name": "Salesforce Name", "BillingState": "IL"}
    blank = {**differing, "Name": ""}

    assert build_report_companies([imis], [differing])[0].name == "Salesforce Name"
    assert build_reconciliation_rows(combine_companies([imis], [differing]))[0].issues == ("name difference",)
    assert build_report_companies([imis], [blank])[0].name == "iMIS Name"


def test_erector_client_type_never_infers_a_certification_category():
    imis = Company(name="Erector Co", state="IL", imis_id="1")
    account = {"Id": "sf-1", "IMISID__c": "1", "Name": "Erector Co", "BillingState": "IL", "Industry": "Erector", "Cert_Certification_Status__c": "Certified", "Certifications__r": {"records": []}}

    row = build_report_companies([imis], [account], as_of=date(2026, 6, 1))[0]

    assert row.client_type == "Erector"
    assert row.certification_categories == (CERTIFICATION_CATEGORY_PLACEHOLDER,)


def test_certified_matched_account_without_active_children_stays_in_pdf_and_reconciliation(tmp_path):
    imis = Company(name="Example", state="IL", imis_id="A")
    account = {"Id": "sf-1", "IMISID__c": "A", "Name": "Example", "BillingState": "IL", "Cert_Certification_Status__c": "Certified", "Certifications__r": {"records": [{"Name": "Expired", "Status__c": "Active", "Start_Date__c": "2025-01-01", "End_Date__c": "2025-12-31"}]}}
    combined = combine_companies([imis], [account])

    rows = build_report_companies(combined, as_of=date(2026, 6, 1))
    reconciliation = build_reconciliation_rows(combined, as_of=date(2026, 6, 1))

    assert [row.name for row in rows] == ["Example"]
    assert rows[0].certification_categories == (CERTIFICATION_CATEGORY_PLACEHOLDER,)
    assert reconciliation[0].issues == ("certified account without active certifications",)
    csv_output = tmp_path / "reconciliation.csv"
    log_output = tmp_path / "reconciliation.log"
    write_reconciliation_csv(reconciliation, csv_output)
    write_reconciliation_log(reconciliation, log_output)
    assert "matched,A,sf-1,Example,Example,certified account without active certifications" in csv_output.read_text(encoding="utf-8")
    assert "Certified accounts without active certifications: 1" in log_output.read_text(encoding="utf-8")
    assert "iMIS ID=A, Salesforce Account ID=sf-1" in log_output.read_text(encoding="utf-8")
