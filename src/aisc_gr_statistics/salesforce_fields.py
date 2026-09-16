"""Salesforce fields and picklist values used by the reporting project.

Keep Salesforce API names and controlled picklist values here. This gives the
report code one dependable place to look when a field is added or changed.
"""

from enum import StrEnum


class CertificationAccountField(StrEnum):
    """Account fields needed for the Certification portion of a report."""

    ID = "Id"
    NAME = "Name"
    CERTIFICATION_ID = "Certification_ID__c"
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


REPORT_ACCOUNT_FIELDS = tuple(CertificationAccountField)
"""The Account fields selected by the first Certification data query."""

ACTIVE_CERTIFICATION_STATUSES = frozenset(
    {CertificationStatus.CERTIFIED, CertificationStatus.INITIALS}
)
"""Statuses treated as active by the existing Salesforce project."""
