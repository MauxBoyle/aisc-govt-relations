"""Salesforce fields and picklist values used by the reporting project.

Keep Salesforce API names and controlled picklist values here. This gives the
report code one dependable place to look when a field is added or changed.
"""

from datetime import date
from enum import StrEnum


class CertificationAccountField(StrEnum):
    """Account fields needed for the Certification portion of a report."""

    ID = "Id"
    NAME = "Name"
    CERTIFICATION_ID = "Certification_ID__c"
    IMIS_ID = "IMISID__c"
    CLIENT_TYPE = "Industry"
    COMPANY_OWNER = "Company_Owner__c"
    BILLING_STREET = "BillingStreet"
    BILLING_CITY = "BillingCity"
    BILLING_STATE = "BillingState"
    BILLING_POSTAL_CODE = "BillingPostalCode"
    BILLING_COUNTRY = "BillingCountry"
    CERTIFICATION_STATUS = "Cert_Certification_Status__c"
    EMPLOYEE_COUNT = "NumberOfEmployees"
    WEBSITE = "Website"
    CERTIFICATION_CONTACT_ID = "Cert_Certification_Contact__c"
    PRINCIPAL_CONTACT_ID = "Cert_Principal_Contact__c"


class CertificationStatus(StrEnum):
    """Certification-status picklist values used by AISC."""

    INITIALS = "Initials"
    CERTIFIED = "Certified"
    DROPPED = "Dropped"
    SUSPENDED = "Suspended"


ACTIVE_CERTIFICATION_STATUSES = frozenset(
    {CertificationStatus.CERTIFIED, CertificationStatus.INITIALS}
)
"""Statuses treated as active by the existing Salesforce project."""


class CertificationRelationship(StrEnum):
    """Verified Account-to-certification relationship API names."""

    OBJECT = "Cert_Certification__c"
    ACCOUNT_FIELD = "Cert_Account__c"
    ACCOUNT_PARENT = "Cert_Account__r"
    ACCOUNT_CHILD = "Certifications__r"


class CertificationField(StrEnum):
    """Fields on the certification child object used by the report."""

    NAME = "Name"
    TYPE = "Cert_Certification_Type_Skill__c"
    STATUS = "Status__c"
    START_DATE = "Start_Date__c"
    END_DATE = "End_Date__c"


class ChildCertificationStatus(StrEnum):
    """Status picklist values on ``Cert_Certification__c``."""

    ACTIVE = "Active"
    INACTIVE = "Inactive"


REPORT_ACCOUNT_FIELDS = (
    *CertificationAccountField,
    "(SELECT "
    f"{CertificationField.NAME}, {CertificationField.TYPE}, {CertificationField.STATUS}, "
    f"{CertificationField.START_DATE}, {CertificationField.END_DATE} "
    f"FROM {CertificationRelationship.ACCOUNT_CHILD})",
)
"""Account fields and read-only child query used by the Illinois report."""


def is_active_certification(status, start_date, end_date, as_of=None):
    """Return whether a child certification is active on ``as_of``.

    A certification must have the ``Active`` status and an inclusive start/end
    date range.  Missing or malformed values are deliberately not treated as
    active, so incomplete source data cannot silently enter a report.
    """
    if status != ChildCertificationStatus.ACTIVE:
        return False
    try:
        effective_date = _as_date(start_date)
        expiration_date = _as_date(end_date)
        comparison_date = date.today() if as_of is None else _as_date(as_of)
    except (TypeError, ValueError):
        return False
    return effective_date <= comparison_date <= expiration_date


def _as_date(value):
    """Accept Salesforce ISO dates and Python ``date`` values."""
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError("Expected a date or ISO date string.")
