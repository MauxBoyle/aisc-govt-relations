"""Derive the single relationship phrase approved for external reports."""

from collections.abc import Mapping
from datetime import date

from .report import CombinedCompany
from .salesforce_fields import (
    CertificationAccountField,
    CertificationField,
    CertificationRelationship,
    CertificationStatus,
    is_active_certification,
)

_CERTIFIED_CLIENT_TYPES = {
    "Fabricator": "AISC Certified Fabricator",
    "Erector": "AISC Certified Erector",
    "Fabricator/Erector": "AISC Certified Fabricator/Erector",
}


def relationship_summary(company: CombinedCompany, as_of: date | str) -> str:
    """Return an approved public phrase without exposing source values.

    Certification wording requires the Account's Certified status, one active
    and effective child certification, and an exactly mapped Client Type.
    iMIS's already translated membership/category label is the fallback.
    """
    account = company.salesforce
    client_type = _account_value(account, CertificationAccountField.CLIENT_TYPE)
    if (
        _account_value(account, CertificationAccountField.CERTIFICATION_STATUS)
        == CertificationStatus.CERTIFIED
        and client_type in _CERTIFIED_CLIENT_TYPES
        and _has_active_child_certification(account, as_of)
    ):
        return _CERTIFIED_CLIENT_TYPES[client_type]
    if company.imis and company.imis.membership_type:
        return company.imis.membership_type
    return "AISC relationship unavailable"


def _has_active_child_certification(
    account: Mapping[str, object] | None, as_of: date | str
) -> bool:
    if account is None:
        return False
    relationship = account.get(CertificationRelationship.ACCOUNT_CHILD)
    if not isinstance(relationship, Mapping):
        return False
    records = relationship.get("records")
    return isinstance(records, list) and any(
        isinstance(certification, Mapping)
        and is_active_certification(
            certification.get(CertificationField.STATUS),
            certification.get(CertificationField.START_DATE),
            certification.get(CertificationField.END_DATE),
            as_of,
        )
        for certification in records
    )


def _account_value(account, field) -> str:
    value = account.get(field, "") if account else ""
    return value.strip() if isinstance(value, str) else ""
