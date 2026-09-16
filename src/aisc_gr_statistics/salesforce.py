"""Read-only Salesforce authentication and SOQL query helpers."""

import os
from urllib.parse import urlparse

import requests

OAUTH_URL = "https://login.salesforce.com/services/oauth2/token"
API_VERSION = "v60.0"
REQUIRED_CREDENTIALS = ("SF_CLIENT_ID", "SF_CLIENT_SECRET")


class SalesforceError(RuntimeError):
    """Raise when Salesforce configuration, authentication, or a query fails."""


def get_credentials(environment=None):
    """Read Salesforce credentials and give a clear error when one is missing."""
    environment = environment if environment is not None else os.environ
    missing = [
        name for name in REQUIRED_CREDENTIALS if not environment.get(name, "").strip()
    ]
    if missing:
        raise SalesforceError("Missing Salesforce configuration: " + ", ".join(missing))
    return {name: environment[name] for name in REQUIRED_CREDENTIALS}


def get_oauth_url(environment=None):
    """Return the configured OAuth endpoint or Salesforce's production endpoint."""
    environment = environment if environment is not None else os.environ
    login_url = environment.get("SF_LOGIN_URL", "").strip()
    if not login_url:
        return OAUTH_URL

    parsed = urlparse(login_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SalesforceError("SF_LOGIN_URL must be a valid HTTPS URL.")
    if login_url.rstrip("/").endswith("/services/oauth2/token"):
        return login_url.rstrip("/")
    return login_url.rstrip("/") + "/services/oauth2/token"


def create_client(environment=None, session=requests):
    """Authenticate with Salesforce and return a client for read-only queries."""
    credentials = get_credentials(environment)
    oauth_url = get_oauth_url(environment)
    payload = {
        "grant_type": "client_credentials",
        "client_id": credentials["SF_CLIENT_ID"],
        "client_secret": credentials["SF_CLIENT_SECRET"],
    }
    try:
        response = session.post(oauth_url, data=payload, timeout=30)
    except requests.RequestException as error:
        raise SalesforceError(
            f"Could not reach Salesforce for authentication: {error}"
        ) from error

    if not response.ok:
        raise SalesforceError(
            f"Salesforce rejected authentication: {_response_details(response)}"
        )
    try:
        data = response.json()
        return SalesforceClient(data["instance_url"], data["access_token"], session)
    except (KeyError, ValueError, TypeError) as error:
        raise SalesforceError(
            "Salesforce authentication response was incomplete."
        ) from error


class SalesforceClient:
    """Run paginated, read-only SOQL queries against one Salesforce org."""

    def __init__(self, instance_url, access_token, session=requests):
        self.instance_url = instance_url
        self.session = session
        self.headers = {"Authorization": f"Bearer {access_token}"}

    def query_records(self, object_name, fields, where=None, order_by=None):
        """Return all records for a query, following Salesforce result pages."""
        soql = f"SELECT {', '.join(fields)} FROM {object_name}"
        if where:
            soql += f" WHERE {where}"
        if order_by:
            soql += f" ORDER BY {order_by}"

        url = f"{self.instance_url}/services/data/{API_VERSION}/query"
        params = {"q": soql}
        records = []
        while url:
            response = self._get(url, params, f"query {object_name}")
            try:
                payload = response.json()
                records.extend(payload["records"])
                done = payload["done"]
            except (KeyError, TypeError, ValueError) as error:
                raise SalesforceError(
                    f"Invalid Salesforce query response for {object_name}."
                ) from error

            next_url = payload.get("nextRecordsUrl")
            if not done and not next_url:
                raise SalesforceError(
                    f"Salesforce query for {object_name} ended without a next page."
                )
            url = self._absolute_url(next_url) if next_url else None
            params = None
        return records

    def _get(self, url, params, action):
        try:
            response = self.session.get(
                url, headers=self.headers, params=params, timeout=30
            )
        except requests.RequestException as error:
            raise SalesforceError(f"Could not {action}: {error}") from error
        if not response.ok:
            raise SalesforceError(
                f"Salesforce failed to {action}: {_response_details(response)}"
            )
        return response

    def _absolute_url(self, url):
        if not isinstance(url, str):
            raise SalesforceError("Salesforce pagination URL was invalid.")
        return url if url.startswith("https://") else self.instance_url + url


def _response_details(response):
    """Return Salesforce's error text without exposing credentials."""
    try:
        details = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
    if isinstance(details, list) and details:
        details = details[0]
    if isinstance(details, dict):
        return str(
            details.get("message") or details.get("error_description") or details
        )
    return str(details)
