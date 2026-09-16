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

Export names can differ from these labels. Record the actual iMIS column names
when the import script is created.

## Workflow

1. Place the unchanged export in `data/raw/imis/`.
2. Run a future import-and-cleaning script.
3. Put generated results in `data/processed/`; this folder is also ignored by
   Git because it may contain sensitive data.
4. Use `tests/fixtures/imis-membership-sample.csv` for tests. It contains only
   fictional data and is safe to publish.
