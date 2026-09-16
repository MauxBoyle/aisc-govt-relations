# Salesforce Data

## Overview

This project reads Certification information from Salesforce. The connection is
**read-only**: it can authenticate and run Salesforce queries, but it does not
create, change, or delete Salesforce records.

The connection code lives in
`src/aisc_gr_statistics/salesforce.py`. The report's Salesforce field names
and certification-status values live in
`src/aisc_gr_statistics/salesforce_fields.py`.

## Configuration

Your local `.env` file contains the Salesforce Connected App credentials:

| Variable | Required | Purpose |
|---|---:|---|
| `SF_CLIENT_ID` | Yes | Connected App client ID |
| `SF_CLIENT_SECRET` | Yes | Connected App secret |
| `SF_LOGIN_URL` | No | Salesforce org URL or full OAuth token URL |

The default login endpoint is Salesforce production:
`https://login.salesforce.com/services/oauth2/token`.

Keep `.env` private. It is ignored by Git so credentials are not committed.
The committed `.env.example` shows the variable names without real values.

## Account Fields for Certification Reporting

Salesforce's **Account** object is the starting point for Certification data.
The initial field catalog is:

| Report meaning | Salesforce API field |
|---|---|
| Salesforce record ID | `Id` |
| Company name | `Name` |
| Certification ID | `Certification_ID__c` |
| Company owner | `Company_Owner__c` |
| Street address | `BillingStreet` |
| City | `BillingCity` |
| State | `BillingState` |
| Postal code | `BillingPostalCode` |
| Country | `BillingCountry` |
| Certification status | `Cert_Certification_Status__c` |
| Employee count | `NumberOfEmployees` |
| Company website | `Website` |
| Certification contact lookup | `Cert_Certification_Contact__c` |
| Principal contact lookup | `Cert_Principal_Contact__c` |

The fields are named in `CertificationAccountField`, rather than repeated as
text in several files. If a Salesforce API name changes, update that one
catalog and its test.

Congressional district is not currently included in the copied Salesforce
field catalog. Its source and matching rule still need to be confirmed before
it is added to the report.

## Certification-Status Values

`CertificationStatus` documents the picklist values currently used by the
related Salesforce project:

| Python name | Salesforce value |
|---|---|
| `INITIALS` | `Initials` |
| `CERTIFIED` | `Certified` |
| `DROPPED` | `Dropped` |
| `SUSPENDED` | `Suspended` |

Use these named values when filtering or comparing a certification status. This
avoids spelling and capitalization mistakes in Salesforce queries.

The existing Salesforce project treats `Certified` and `Initials` as active
statuses. Confirm that this is also the right rule for the Government Relations
report before using it as a report filter.

## How Data Access Works

1. `create_client()` reads the three environment variables above.
2. It uses Salesforce's OAuth client-credentials flow to obtain a short-lived
   access token.
3. `SalesforceClient.query_records()` sends a SOQL query to Salesforce.
4. If Salesforce returns multiple result pages, the client follows each page
   and combines the records.

For example, future report code can use a defined field catalog:

```python
from aisc_gr_statistics.salesforce import create_client
from aisc_gr_statistics.salesforce_fields import (
    CertificationStatus,
    REPORT_ACCOUNT_FIELDS,
)

client = create_client()
accounts = client.query_records(
    "Account",
    REPORT_ACCOUNT_FIELDS,
    where=(f"Cert_Certification_Status__c = '{CertificationStatus.CERTIFIED}'"),
    order_by="Name",
)
```

This example reads records only. It is intentionally not run automatically,
which prevents a test run from accessing live Salesforce data.
