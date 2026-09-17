# iMIS Membership Data

Keep the original iMIS export in `data/raw/imis/`. For example:

```text
data/raw/imis/membership-export-2026-09-16.csv
```

Files in `data/raw/` are intentionally ignored by Git. This keeps real member
and contact data out of the public GitHub repository.

## Expected data

The initial membership report needs these fields:

| Field | Purpose |
| --- | --- |
| Shared iMIS ID | Authoritatively join a membership record to Salesforce `IMISID__c`. |
| Company name | Display the membership company name. |
| City | Compare location with Salesforce and identify review-only candidates. |
| Company type | Group companies in the report. |
| Annual structural steel tonnage | Calculate membership totals. |
| State | Group totals by state. |
| Congressional district | Group totals by congressional district. |

Export names can differ from these labels. The report recognizes common
space-separated and underscore-separated variants, including the current iMIS
labels `Full Name`, `State Province`, `Full Address`, `Member Type`, and `US
Congress`. A recognizable shared iMIS-ID column and city column are also
required. Individual ID or city cells may be blank. The report never joins by
company name: blank IDs remain separate records and may only appear in the
candidate-match review file. Unavailable address, membership type, and district
display as placeholders.

## Membership Type codes

The report translates these confirmed iMIS codes into readable labels:

| iMIS code | Report label |
| --- | --- |
| `ACT` | Full Member |
| `ACTB` | Full Member Branch |
| `ASSOC` | Associate Member |
| `ASSCB` | Associate Member Branch |

The report combines the Membership Type label with the Category label, such as
`Full Member Fabricator`.

## Category codes

The currently confirmed Category codes are:

| iMIS code | Report label |
| --- | --- |
| `BEND` | Bender |
| `DERC` | Erector |
| `DET1` | Detailer |
| `DET10` | Detailer |
| `EQPM` | Equipment Manufacturer |
| `EREC` | Erector |
| `FAB` | Fabricator |
| `SUPP` | Supplier |
| `COTM` | Supplier |
| `WELD` | Detailer |
| `SOFT` | Software |
| `BOLT` | Bolt Manufacturer |

This list may not yet include every Category. Blank and unconfirmed Type or
Category codes have no PDF label. The required `--unknown-imis-codes-csv`
output records them separately, including rows outside Illinois, so they can be
reviewed without showing an unconfirmed raw code in the report.

For the current raw export, `MILL` is an unconfirmed Category (47 occurrences)
and blank Categories are separately reported (12 occurrences). Do not invent a
label for either finding. Review `unknown-imis-codes.csv`, confirm the code’s
meaning with the data owner, and then add the confirmed mapping to
`src/aisc_gr_statistics/imis_fields.py`.

For the current iMIS export, **Structural Steel Tonnage** is calculated as:
`Bridge Tonnage + Building Tonnage + S C Tonnage`. Blank values are treated as
zero. A non-numeric tonnage value stops the report and identifies its row and
column so the source export can be corrected.

## Workflow

1. Place the unchanged export in `data/raw/imis/`.
2. Run the statewide report with `uv run aisc_gr_statistics report --imis-csv
   data/raw/imis/membership-export.csv --output
   data/processed/illinois-certification-membership.pdf --conflicts-csv
   data/processed/field-conflicts.csv --candidate-matches-csv
   data/processed/candidate-matches.csv --reconciliation-csv
   data/processed/reconciliation.csv --reconciliation-log
   data/processed/reconciliation.log --unknown-imis-codes-csv
   data/processed/unknown-imis-codes.csv`.
3. Put generated results in `data/processed/`; this folder is also ignored by
   Git because it may contain sensitive data.
4. Review `unknown-imis-codes.csv`, confirm any code meanings, and update the
   central dictionary before relying on a new report label.
5. Use `tests/fixtures/imis-membership-sample.csv` for tests. It contains only
   fictional data and is safe to publish.
