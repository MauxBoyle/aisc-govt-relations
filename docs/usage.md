# Usage

## Installation

Clone the repository and install dependencies:

```bash
uv sync
```

## Running

Via the CLI entrypoint:

```bash
uv run aisc_gr_statistics                          # loads .env when present
```

Or as a Python module:

```bash
uv run python -m aisc_gr_statistics
```

## Illinois PDF report

Create a printable, letter-size statewide report by selecting a local iMIS
CSV explicitly:

```bash
uv run aisc_gr_statistics report \
  --imis-csv data/raw/imis/membership-export.csv \
  --output data/processed/illinois-certification-membership.pdf \
  --conflicts-csv data/processed/field-conflicts.csv \
  --candidate-matches-csv data/processed/candidate-matches.csv \
  --reconciliation-csv data/processed/reconciliation.csv \
  --reconciliation-log data/processed/reconciliation.log \
  --unknown-imis-codes-csv data/processed/unknown-imis-codes.csv \
  --tonnage-review-csv data/processed/tonnage-review.csv
```

Keep real exports under ignored `data/raw/imis/` and generated PDFs under
ignored `data/processed/`; neither should be committed. The command accepts
common forms of the company-name, state, city, shared-iMIS-ID, membership-type,
tonnage, and congressional-district headers. Company name, state, city, and a
recognizable shared iMIS ID column are required (individual ID and city values
may be blank).
Tonnage submissions may be monthly or irregular. The report selects the most
recently completed calendar year (so a 2026 run selects 2025), then totals each
unique `(iMIS ID, Tonnage Year, Submission Date)` entry's `Bridge Tonnage`,
`Building Tonnage`, and `S C Tonnage`. The PDF labels the selected year. Exact
duplicate keys with identical tonnage count once; missing timestamps,
conflicting same-key tonnage, and other invalid selected-year rows are excluded
and written to the required `--tonnage-review-csv`. Company display fields come
from the latest valid selected-year submission.

When both `SF_CLIENT_ID` and `SF_CLIENT_SECRET` are configured, the command
reads Salesforce Account records with their child certifications. It joins only
on a populated, exact-text matching `IMISID__c` value—similar company names do
not join. IDs are whitespace-trimmed text keys and are never converted to
numbers, so `00123` and `123` do not match. If a nonblank ID is duplicated in
either source, all records using that ID remain source-only rather than being
automatically joined. This prevents a duplicate export value from silently
enriching the wrong company.
When sources disagree on a shared display value, the PDF shows both values with
`iMIS` and `Salesforce` labels. A child certification is active only when
its status is `Active` and today's date is inclusively between its start and end
dates. If more than one Salesforce Account shares a normalized name, the iMIS
company is not enriched.

The report also adds every Salesforce-only Account whose `BillingState` is
`IL` or `Illinois`, even if it has no active child certification. These
rows use Salesforce billing-address data when available and placeholders for
iMIS-only membership type, tonnage, and congressional district. The report
continues to use placeholders for U.S. Senators and Representatives.

`--conflicts-csv` always writes columns for shared iMIS ID, classification,
field, and the two source values. In addition to value conflicts from valid ID
joins, it records duplicate-ID findings with `duplicate iMIS ID` in the field
column, the affected source classification, and a count such as `2 iMIS
records` or `2 Salesforce records`. `--candidate-matches-csv` records
review-only lookalikes when normalized name, city, and state agree but IDs
differ or are missing; it never changes report matching. Records with equal
duplicate IDs are not candidate matches because their duplicate finding is
already recorded in the conflicts CSV.

`--reconciliation-csv` writes one row in `data/processed/` for every combined
company record. It includes matched, iMIS-only, and Salesforce-only records,
source IDs and names, the Salesforce Account ID, and any review issues. It is
the complete, spreadsheet-filterable reconciliation artifact. Its companion
`--reconciliation-log` writes readable counts for matches, source-only records,
duplicate IDs, missing IDs, and ID-matched name differences, followed by the
details of every questionable record. Review both reconciliation files before
using the PDF.

`--unknown-imis-codes-csv` is a required review file for the source export. It
scans every row before Illinois filtering and aggregates blank or unconfirmed
Membership Type and Category values into `iMIS field`, `iMIS code`, `status`,
and `occurrences` columns. Known codes match after trimming and ignoring case.
Blank and unknown codes have no PDF label, so the report does not display raw,
unconfirmed values. Review this CSV, confirm each code’s meaning, and then add
the confirmed mapping in `src/aisc_gr_statistics/imis_fields.py`.

## Environment Variables

| Variable    | Default    | Description                          |
|-------------|------------|--------------------------------------|
| `LOG_LEVEL` | `INFO`     | Console log level (DEBUG, INFO, …)   |
| `LOG_FILE`  | `app.log`  | Path to the log file                 |

Copy `.env.example` to `.env` for development defaults. The CLI loads it
automatically; shell environment values take precedence.
