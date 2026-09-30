"""Allow-listed public display data and wording for external PDF reports.

Map geometry deliberately does not appear here: it is private drawing input,
not public company display data.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PublicCompany:
    """The only company fields that an external PDF may display."""

    name: str
    city: str
    county: str
    relationship_summary: str | None = None


@dataclass(frozen=True)
class PublicJobAggregate:
    """Aggregate figures eligible for external display after thresholding."""

    included_company_count: int
    known_jobs: int
    companies_with_employee_data: int


def format_company_text(company: PublicCompany) -> str:
    """Return the allow-listed company text, including an optional future note."""
    text = f"{company.name} — {company.city}, {company.county}"
    return f"{text} — {company.relationship_summary}" if company.relationship_summary else text


def format_known_jobs(label: str, aggregate: PublicJobAggregate) -> str:
    """Format a thresholded aggregate without exposing individual employment."""
    if aggregate.companies_with_employee_data < 2:
        return f"{label} known jobs: N/A (employee data not available)"
    return (
        f"{label} known jobs: {aggregate.known_jobs:,} "
        f"({aggregate.companies_with_employee_data} of "
        f"{aggregate.included_company_count} companies have employee data)"
    )


def format_source_dates(census_retrieved_at: str, district_as_of: str) -> str:
    """Return the common public provenance sentence for both report types."""
    source_date = str(census_retrieved_at)[:10]
    return (
        f"Census boundaries as of {source_date}; district data as of "
        f"{district_as_of or 'saved snapshot'}."
    )
