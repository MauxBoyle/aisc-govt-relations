"""Allow-listed public display data and wording for external PDF reports.

Map geometry deliberately does not appear here: it is private drawing input,
not public company display data.
"""

from dataclasses import dataclass

from .imis_fields import CATEGORY_LABELS, MEMBERSHIP_TYPE_LABELS

_CERTIFICATION_RELATIONSHIPS = frozenset(
    {
        "AISC Certified Fabricator",
        "AISC Certified Erector",
        "AISC Certified Fabricator/Erector",
    }
)
_MEMBERSHIP_RELATIONSHIPS = frozenset(
    " ".join(part for part in parts if part)
    for membership in (*MEMBERSHIP_TYPE_LABELS.values(), "")
    for category in (*CATEGORY_LABELS.values(), "")
    if (parts := (membership, category)) and any(parts)
)
_PUBLIC_RELATIONSHIPS = (
    _CERTIFICATION_RELATIONSHIPS
    | _MEMBERSHIP_RELATIONSHIPS
    | {"AISC relationship unavailable"}
)


@dataclass(frozen=True)
class PublicCompany:
    """The only company fields that an external PDF may display."""

    name: str
    city: str
    county: str
    relationship_summary: str | None = None

    def __post_init__(self):
        if (
            self.relationship_summary
            and self.relationship_summary not in _PUBLIC_RELATIONSHIPS
        ):
            raise ValueError("relationship summary is not approved for public display")


@dataclass(frozen=True)
class PublicJobAggregate:
    """Aggregate figures eligible for external display after thresholding."""

    included_company_count: int
    known_jobs: int
    companies_with_employee_data: int


@dataclass(frozen=True)
class PublicContact:
    """Public official details that are permitted in an external PDF."""

    name: str
    affiliation: str = ""
    address: str = ""
    phone: str = ""
    website_url: str = ""
    contact_form_url: str = ""


def format_public_contact(contact: PublicContact) -> tuple[tuple[str, str], ...]:
    """Return present contact fields, or one clear fallback for an empty card."""
    fields = (
        ("Address", contact.address),
        ("Phone", contact.phone),
        ("Website", contact.website_url),
        ("Contact form", contact.contact_form_url),
    )
    present = tuple((label, value) for label, value in fields if value.strip())
    if not present:
        return (("", "Public contact information unavailable"),)
    return present


def format_company_text(company: PublicCompany) -> str:
    """Return the allow-listed company text, including an optional future note."""
    text = f"{company.name} — {company.city}, {company.county}"
    return (
        f"{text} — {company.relationship_summary}"
        if company.relationship_summary
        else text
    )


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
