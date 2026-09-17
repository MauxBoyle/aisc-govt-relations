"""Tests for read-only Salesforce helpers."""

from datetime import date

import pytest

from aisc_gr_statistics.salesforce import (
    SalesforceClient,
    SalesforceError,
    create_client,
    get_credentials,
    get_oauth_url,
)
from aisc_gr_statistics.salesforce_fields import (
    REPORT_ACCOUNT_FIELDS,
    CertificationAccountField,
    CertificationField,
    CertificationRelationship,
    CertificationStatus,
    ChildCertificationStatus,
    is_active_certification,
)


class Response:
    """Small stand-in for a requests response."""

    def __init__(self, payload, ok=True, text=""):
        self.payload = payload
        self.ok = ok
        self.text = text
        self.status_code = 400

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class Session:
    """Small stand-in for the HTTP methods used by the client."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append(("post", args, kwargs))
        return self.responses.pop(0)

    def get(self, *args, **kwargs):
        self.calls.append(("get", args, kwargs))
        return self.responses.pop(0)


def test_report_field_catalog_uses_salesforce_api_names():
    """Keep the core report fields and picklist values explicit."""
    assert CertificationAccountField.EMPLOYEE_COUNT == "NumberOfEmployees"
    assert CertificationAccountField.BILLING_STATE == "BillingState"
    assert CertificationStatus.CERTIFIED == "Certified"


def test_verified_salesforce_mapping_constants_use_api_names():
    """Keep the iMIS, Client Type, and child-certification mapping explicit."""
    assert CertificationAccountField.IMIS_ID == "IMISID__c"
    assert CertificationAccountField.CLIENT_TYPE == "Industry"
    assert CertificationRelationship.OBJECT == "Cert_Certification__c"
    assert CertificationRelationship.ACCOUNT_FIELD == "Cert_Account__c"
    assert CertificationRelationship.ACCOUNT_PARENT == "Cert_Account__r"
    assert CertificationRelationship.ACCOUNT_CHILD == "Certifications__r"
    assert CertificationField.NAME == "Name"
    assert CertificationField.TYPE == "Cert_Certification_Type_Skill__c"
    assert CertificationField.STATUS == "Status__c"
    assert CertificationField.START_DATE == "Start_Date__c"
    assert CertificationField.END_DATE == "End_Date__c"
    assert ChildCertificationStatus.ACTIVE == "Active"
    assert ChildCertificationStatus.INACTIVE == "Inactive"


def test_report_query_includes_the_nested_certification_fields():
    """Fetch child fields needed to apply the active-certification rule locally."""
    assert REPORT_ACCOUNT_FIELDS[-1] == (
        "(SELECT Name, Cert_Certification_Type_Skill__c, Status__c, Start_Date__c, End_Date__c "
        "FROM Certifications__r)"
    )


@pytest.mark.parametrize(
    ("status", "start_date", "end_date", "as_of", "expected"),
    [
        ("Active", "2026-01-01", "2026-12-31", "2026-06-01", True),
        ("Active", "2026-06-01", "2026-06-01", "2026-06-01", True),
        ("Inactive", "2026-01-01", "2026-12-31", "2026-06-01", False),
        ("Active", "2026-07-01", "2026-12-31", "2026-06-01", False),
        ("Active", "2026-01-01", "2026-05-31", "2026-06-01", False),
        ("", "2026-01-01", "2026-12-31", "2026-06-01", False),
        ("Unknown", "2026-01-01", "2026-12-31", "2026-06-01", False),
        ("Active", "", "2026-12-31", "2026-06-01", False),
        ("Active", "2026-01-01", None, "2026-06-01", False),
        ("Active", "not-a-date", "2026-12-31", "2026-06-01", False),
    ],
)
def test_active_child_certification_rule(
    status, start_date, end_date, as_of, expected
):
    """Require active status and an inclusive, complete effective date range."""
    assert is_active_certification(status, start_date, end_date, as_of) is expected


def test_active_child_certification_accepts_date_objects():
    """The helper also supports date values after Salesforce data is parsed."""
    assert is_active_certification(
        ChildCertificationStatus.ACTIVE,
        date(2026, 1, 1),
        date(2026, 12, 31),
        date(2026, 6, 1),
    )


def test_credentials_require_client_id_and_secret():
    """Reject incomplete configuration before an HTTP request is attempted."""
    with pytest.raises(SalesforceError, match="SF_CLIENT_SECRET"):
        get_credentials({"SF_CLIENT_ID": "id"})


def test_oauth_url_accepts_an_org_url_or_complete_token_url():
    """Allow the usual Salesforce org URL configuration."""
    assert (
        get_oauth_url({"SF_LOGIN_URL": "https://example.my.salesforce.com"})
        == "https://example.my.salesforce.com/services/oauth2/token"
    )
    assert (
        get_oauth_url(
            {"SF_LOGIN_URL": "https://example.my.salesforce.com/services/oauth2/token"}
        )
        == "https://example.my.salesforce.com/services/oauth2/token"
    )
    with pytest.raises(SalesforceError, match="valid HTTPS"):
        get_oauth_url({"SF_LOGIN_URL": "not a URL"})


def test_create_client_uses_client_credentials():
    """Authenticate using the Salesforce Connected App credentials."""
    session = Session(
        [
            Response(
                {
                    "instance_url": "https://example.my.salesforce.com",
                    "access_token": "access-token",
                }
            )
        ]
    )
    client = create_client(
        {"SF_CLIENT_ID": "id", "SF_CLIENT_SECRET": "secret"}, session
    )
    assert client.instance_url == "https://example.my.salesforce.com"
    assert session.calls[0][2]["data"]["grant_type"] == "client_credentials"


def test_create_client_reports_rejected_authentication():
    """Keep authentication failures understandable without exposing secrets."""
    session = Session([Response({"error_description": "bad login"}, ok=False)])

    with pytest.raises(SalesforceError, match="rejected authentication: bad login"):
        create_client({"SF_CLIENT_ID": "id", "SF_CLIENT_SECRET": "secret"}, session)


def test_query_records_follows_salesforce_pages():
    """Collect each page returned by Salesforce."""
    session = Session(
        [
            Response(
                {
                    "done": False,
                    "records": [{"Name": "First"}],
                    "nextRecordsUrl": "/next-page",
                }
            ),
            Response({"done": True, "records": [{"Name": "Second"}]}),
        ]
    )
    client = SalesforceClient("https://example", "token", session)

    records = client.query_records(
        "Account",
        [CertificationAccountField.NAME, CertificationAccountField.EMPLOYEE_COUNT],
        where="Cert_Certification_Status__c = 'Certified'",
        order_by="Name",
    )

    assert records == [{"Name": "First"}, {"Name": "Second"}]
    assert session.calls[0][2]["params"]["q"] == (
        "SELECT Name, NumberOfEmployees FROM Account "
        "WHERE Cert_Certification_Status__c = 'Certified' ORDER BY Name"
    )
    assert session.calls[1][1][0] == "https://example/next-page"


def test_query_records_rejects_an_incomplete_page_sequence():
    """Reject a Salesforce response that is unfinished but has no next page."""
    client = SalesforceClient(
        "https://example",
        "token",
        Session([Response({"done": False, "records": []})]),
    )

    with pytest.raises(SalesforceError, match="ended without a next page"):
        client.query_records("Account", ["Name"])


def test_describe_object_requests_salesforce_metadata():
    """Retrieve metadata through the documented describe endpoint."""
    payload = {"name": "Account", "fields": [], "childRelationships": []}
    session = Session([Response(payload)])
    client = SalesforceClient("https://example", "token", session)

    assert client.describe_object("Account") == payload
    method, args, kwargs = session.calls[0]
    assert method == "get"
    assert args[0] == "https://example/services/data/v60.0/sobjects/Account/describe"
    assert kwargs["headers"] == {"Authorization": "Bearer token"}
    assert kwargs["params"] is None


def test_describe_object_reports_a_salesforce_error():
    """Make describe failures as clear as query failures."""
    client = SalesforceClient(
        "https://example",
        "token",
        Session([Response({"message": "Unknown object"}, ok=False)]),
    )

    with pytest.raises(
        SalesforceError, match="failed to describe Account: Unknown object"
    ):
        client.describe_object("Account")


def test_describe_object_rejects_invalid_metadata():
    """Avoid treating an unexpected JSON response as object metadata."""
    client = SalesforceClient("https://example", "token", Session([Response([])]))

    with pytest.raises(SalesforceError, match="Invalid Salesforce describe response"):
        client.describe_object("Account")
