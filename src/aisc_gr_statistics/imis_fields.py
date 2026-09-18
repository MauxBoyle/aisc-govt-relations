"""Confirmed iMIS codes, report labels, and undefined-code review findings."""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

MEMBERSHIP_TYPE_LABELS = {
    "ACT": "Full AISC Member",
    "ACTB": "Full AISC Member Branch",
    "ASSOC": "Associate AISC Member",
    "ASSCB": "Associate AISC Member Branch",
}
"""Translate iMIS Membership Type codes into report-friendly labels."""

CATEGORY_LABELS = {
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
"""Translate confirmed iMIS Category codes into report-friendly labels."""


def membership_type_label(code: str) -> str:
    """Return the confirmed Membership Type label, or blank when unconfirmed."""
    cleaned_code = code.strip()
    return MEMBERSHIP_TYPE_LABELS.get(cleaned_code.upper(), "")


def category_label(code: str) -> str:
    """Return the confirmed Category label, or blank when unconfirmed."""
    cleaned_code = code.strip()
    return CATEGORY_LABELS.get(cleaned_code.upper(), "")


def membership_label(membership_type: str, category: str) -> str:
    """Combine readable Membership Type and Category labels for the report."""
    parts = (membership_type_label(membership_type), category_label(category))
    return " ".join(part for part in parts if part)


@dataclass(frozen=True)
class UndefinedImisCodeFinding:
    """An aggregated blank or unconfirmed iMIS Type or Category value."""

    field: str
    code: str
    status: str
    occurrences: int


def scan_undefined_imis_codes(
    values: Iterable[tuple[str, str]],
) -> list[UndefinedImisCodeFinding]:
    """Aggregate blank and unconfirmed Type/Category values for review.

    Confirmed codes are compared after whitespace trimming and case
    normalization. Blank values retain an empty code cell so a spreadsheet can
    distinguish them from nonblank unknown codes.
    """
    labels_by_field = {
        "Type": MEMBERSHIP_TYPE_LABELS,
        "Category": CATEGORY_LABELS,
    }
    counts: Counter[tuple[str, str, str]] = Counter()
    for field, raw_code in values:
        cleaned_code = raw_code.strip()
        if not cleaned_code:
            counts[(field, "", "blank")] += 1
        elif cleaned_code.upper() not in labels_by_field[field]:
            counts[(field, cleaned_code.upper(), "unknown")] += 1
    return [
        UndefinedImisCodeFinding(field, code, status, occurrences)
        for (field, code, status), occurrences in sorted(counts.items())
    ]
