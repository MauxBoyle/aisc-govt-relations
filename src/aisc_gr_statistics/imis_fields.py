"""iMIS codes and readable labels used by the report."""

MEMBERSHIP_TYPE_LABELS = {
    "ACT": "Full Member",
    "ACTB": "Full Member Branch",
    "ASSOC": "Associate Member",
    "ASSCB": "Associate Member Branch",
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
}
"""Translate confirmed iMIS Category codes into report-friendly labels."""


def membership_type_label(code: str) -> str:
    """Return a readable label, preserving an unknown value for review."""
    cleaned_code = code.strip()
    return MEMBERSHIP_TYPE_LABELS.get(cleaned_code.upper(), cleaned_code)


def category_label(code: str) -> str:
    """Return a readable Category label, preserving unknown values for review."""
    cleaned_code = code.strip()
    return CATEGORY_LABELS.get(cleaned_code.upper(), cleaned_code)


def membership_label(membership_type: str, category: str) -> str:
    """Combine readable Membership Type and Category labels for the report."""
    parts = (membership_type_label(membership_type), category_label(category))
    return " ".join(part for part in parts if part)
