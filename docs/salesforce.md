# Salesforce Data

## Overview

This project reads Certification information from Salesforce. The connection is
**read-only**: it can authenticate and run Salesforce metadata and SOQL queries,
but it does not create, change, or delete Salesforce records.

The connection code lives in `src/aisc_gr_statistics/salesforce.py`. The
report's Salesforce field names, relationship names, and certification rules
live in `src/aisc_gr_statistics/salesforce_fields.py`.

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

## Verified report mapping

Salesforce's **Account** object identifies a company. Its `Certifications__r`
child relationship contains zero or more `Cert_Certification__c` records. The
relationship is important: one Account can have more than one certification,
so a certification category must not be stored as a single Account value.

The following mapping was verified against the org's describe metadata. A
direct path means the field is selected from `Account`; a child path is used in
a SOQL subquery such as `(SELECT Name ... FROM Certifications__r)`.

| Report value | Object | API field | Field type | Relationship path | Report meaning | Active rule |
|---|---|---|---|---|---|---|
| Company name | Account | `Name` | string | Direct | Company label and a cautious fallback lookup value | Not applicable |
| Shared iMIS ID | Account | `IMISID__c` | string | Direct | Stable cross-system key when populated | Not applicable; a blank value does not mean the Account is absent from Salesforce |
| Client Type | Account | `Industry` | picklist | Direct | Account-level business/client classification | Not applicable; this is **not** a certification category |
| Certification summary status | Account | `Cert_Certification_Status__c` | picklist | Direct | Account-level summary, for example `Certified` | Not the child-certification active rule |
| Active certification count | Account | `Cert_Active_Certification_Count__c` | double | Direct | Account-level summary count for validation | Not the child-certification active rule |
| Certification category | `Cert_Certification__c` | `Name` | string | `Account.Certifications__r.Name` | A distinct certified category; an Account can return multiple rows | `Status__c` must be `Active` and the report date must be within `Start_Date__c` through `End_Date__c`, inclusive |
| Certification type | `Cert_Certification__c` | `Cert_Certification_Type_Skill__c` | string | `Account.Certifications__r.Cert_Certification_Type_Skill__c` | Certification object's type label | Same child-certification rule when the row is used as a certification |
| Certification status | `Cert_Certification__c` | `Status__c` | picklist | `Account.Certifications__r.Status__c` | Status of one certification row (`Active` or `Inactive`) | Must equal `Active` |
| Effective date | `Cert_Certification__c` | `Start_Date__c` | `date` | `Account.Certifications__r.Start_Date__c` | First date the certification is effective | Must be present and on or before the report date |
| End date | `Cert_Certification__c` | `End_Date__c` | `date` | `Account.Certifications__r.End_Date__c` | Last date the certification is effective | Must be present and on or after the report date |

The verified relationship API names are:

| Direction | API name |
|---|---|
| Certification object | `Cert_Certification__c` |
| Child-to-parent Account lookup | `Cert_Account__c` |
| Child-to-parent Account relationship | `Cert_Account__r` |
| Account-to-child relationship | `Certifications__r` |

`CertificationAccountField`, `CertificationRelationship`, and
`CertificationField` keep these API names in one tested Python catalog.
`is_active_certification()` implements the child-row rule. It returns `False`
for inactive, unknown, blank, malformed, not-yet-effective, and expired rows.

### Client Type is separate from certifications

`Industry` has the Salesforce label **Client Type**. It describes the Account,
not a certification row. For example, a Client Type can help describe what a
company does, while the child records identify every certification category it
holds. Do not use `Industry` as a substitute for `Certifications__r.Name`, and
do not collapse multiple child rows into one category without an explicit
reporting decision.

### Sanitized validation conclusions

Read-only validation on 2026-09-16 confirmed the mapping without saving query
results or Salesforce IDs:

- The Account whose supplied name begins **A. Lucas & Sons** is named *A. Lucas
  & Sons Steel* in Salesforce. It has an `IMISID__c` value, an Account summary
  status of `Certified`, and an active-count summary of two. Its two separate
  active child certifications are **Building Fabricator** and **Highway
  Component Manufacturer**; both were effective on the validation date and end
  on 2027-01-31.

- **A&H Steel, LLC** has no `IMISID__c` value but is still identifiable as a
  Salesforce Account by its name. Its Client Type is `Erector`, its Account
  summary status is `Certified`, and it has one active child certification:
  **Erector**. That record was effective on the validation date and ends on
  2027-02-28.

These checks show why an iMIS ID is preferred for joining systems when it is
available, but why a missing iMIS ID must not exclude a company already known
to Salesforce. They also show that the two A. Lucas certifications must remain
separate report values.

Congressional district is not in this verified Salesforce mapping. Its source
and matching rule still need confirmation before it is added to the report.

## How Data Access Works

1. `create_client()` reads the three environment variables above.
2. It uses Salesforce's OAuth client-credentials flow to obtain a short-lived
   access token.
3. `SalesforceClient.describe_object()` reads object metadata when a field or
   relationship needs verification.
4. `SalesforceClient.query_records()` sends a SOQL query to Salesforce.
5. If Salesforce returns multiple result pages, the client follows each page
   and combines the records.

`REPORT_ACCOUNT_FIELDS` already includes the child subquery for `Name`,
`Status__c`, `Start_Date__c`, and `End_Date__c`, so report code queries Account
fields and certification children together:

```python
from aisc_gr_statistics.salesforce import create_client
from aisc_gr_statistics.salesforce_fields import (
    REPORT_ACCOUNT_FIELDS,
)

client = create_client()
accounts = client.query_records(
    "Account",
    REPORT_ACCOUNT_FIELDS,
    order_by="Name",
)
```

This example reads records only. It is intentionally not run automatically,
which prevents a test run from accessing live Salesforce data.
