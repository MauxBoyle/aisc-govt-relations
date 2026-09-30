"""Tests for the public-data contract shared by external PDFs."""

import pytest

from aisc_gr_statistics.external_report_contract import (
    PublicCompany,
    PublicJobAggregate,
    format_company_text,
    format_known_jobs,
    format_source_dates,
)


def test_public_company_has_only_allowlisted_display_fields():
    assert tuple(PublicCompany.__dataclass_fields__) == (
        "name",
        "city",
        "county",
        "relationship_summary",
    )
    company = PublicCompany("Example Steel", "Chicago", "Cook County")
    assert format_company_text(company) == "Example Steel — Chicago, Cook County"


def test_optional_relationship_summary_has_a_safe_fallback():
    assert format_company_text(
        PublicCompany("Example Steel", "Chicago", "Cook County", "Member")
    ) == "Example Steel — Chicago, Cook County — Member"
    assert format_company_text(
        PublicCompany("Example Steel", "Chicago", "Cook County", "")
    ) == "Example Steel — Chicago, Cook County"


@pytest.mark.parametrize(
    ("contributors", "expected"),
    [
        (0, "District known jobs: N/A (employee data not available)"),
        (1, "District known jobs: N/A (employee data not available)"),
        (2, "District known jobs: 1,234 (2 of 3 companies have employee data)"),
    ],
)
def test_known_jobs_requires_at_least_two_employee_data_contributors(contributors, expected):
    aggregate = PublicJobAggregate(3, 1_234, contributors)
    assert format_known_jobs("District", aggregate) == expected


def test_known_jobs_labels_are_report_specific_and_lower_case():
    aggregate = PublicJobAggregate(2, 25, 2)
    assert format_known_jobs("Illinois", aggregate) == (
        "Illinois known jobs: 25 (2 of 2 companies have employee data)"
    )
    assert format_known_jobs("National", aggregate) == (
        "National known jobs: 25 (2 of 2 companies have employee data)"
    )


def test_source_date_wording_is_shared():
    assert format_source_dates("2026-09-30T00:00:00Z", "2026-09-29") == (
        "Census boundaries as of 2026-09-30; district data as of 2026-09-29."
    )
