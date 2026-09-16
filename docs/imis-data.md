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
| Company name | Match a membership record to a certification record. |
| Company type | Group companies in the report. |
| Annual structural steel tonnage | Calculate membership totals. |
| State | Group totals by state. |
| Congressional district | Group totals by congressional district. |

Export names can differ from these labels. The report recognizes common
space-separated and underscore-separated variants, including the current iMIS
labels `Full Name`, `State Province`, `Full Address`, `Member Type`, and `US
Congress`. Company name and state are required; unavailable address,
membership type, and district display as placeholders.

For the current iMIS export, **Structural Steel Tonnage** is calculated as:
`Bridge Tonnage + Building Tonnage + S C Tonnage`. Blank values are treated as
zero. A non-numeric tonnage value stops the report and identifies its row and
column so the source export can be corrected.

## Workflow

1. Place the unchanged export in `data/raw/imis/`.
2. Run the statewide report with `uv run aisc_gr_statistics report --imis-csv
   data/raw/imis/membership-export.csv --output
   data/processed/illinois-certification-membership.pdf`.
3. Put generated results in `data/processed/`; this folder is also ignored by
   Git because it may contain sensitive data.
4. Use `tests/fixtures/imis-membership-sample.csv` for tests. It contains only
   fictional data and is safe to publish.
