# aisc-gr-statistics

## Installation

Clone the repository, then install the project and its dependencies:

```bash
uv sync
```

## Usage

Run via the CLI entrypoint:

```bash
uv run aisc_gr_statistics
```

Create a statewide Illinois certification and membership PDF from a local iMIS
CSV export:

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

The source export and generated PDF/CSV files should stay in the ignored `data/raw/` and
`data/processed/` folders. The report reads Salesforce only when both
`SF_CLIENT_ID` and `SF_CLIENT_SECRET` are set. iMIS and Salesforce records are
joined only by an exact shared iMIS ID; names never create a join. IDs are
trimmed text keys, not numbers, so `00123` and `123` remain different IDs. If a
nonblank ID appears more than once in either source, none of the records with
that ID are joined; they remain separate for review. The conflicts CSV lists
both differing values on valid ID joins and duplicate-ID findings. The
candidate-matches CSV lists name/city/state lookalikes with different or missing
IDs. The reconciliation CSV is the complete, spreadsheet-filterable source
review file; its companion log summarizes counts and records needing attention.
Review both reconciliation files in `data/processed/` before using the PDF.
Also review `unknown-imis-codes.csv`: it lists blank and unconfirmed iMIS Type
and Category codes from every export row, including rows outside Illinois.
Unknown or blank codes have no PDF label. Confirm each code’s meaning, then add
it to the central mapping in `src/aisc_gr_statistics/imis_fields.py`.
The modernized, sample-inspired PDF is a bordered two-column card directory
titled **AISC Certification and Membership Summary: Illinois**. It displays a
company name, address, `N Employees`, translated membership type/category,
`YEAR Structural Steel Tonnage: VALUE Tons`, and concise `AISC Certified …`
active-certification sentences. Detailed Salesforce certification names are
grouped, deduplicated, and ordered for display using the maintained rules in
`src/aisc_gr_statistics/certification_groups.py`; unfamiliar active names stay
visible in their own `AISC Certified …` sentence so they can be reviewed and
mapped later. Salesforce owns the displayed company name (with iMIS used when
that name is blank), employee count, and certification data; iMIS owns the
membership type/category and annual structural-steel tonnage. Employee counts
are whole numbers with thousands separators.

Public company cards are ordered alphabetically without considering case or
punctuation. Addresses omit `United States` regardless of capitalization and
place the city/locality on its own line. Matched records retain their separate
iMIS and Salesforce addresses when the two sources differ.

Optional PDF values follow one exact rule: blank, invalid, unrecognized, or
unavailable values omit both their label and value. The PDF never uses blanks
or placeholders for Client Type, certification status, congressional district,
senators, or representatives. Name validation and source-data issues remain in
the separate reconciliation outputs. A certification is displayed only when the
Account status is exactly `Certified` and its child certification is `Active`
and effective on the report date. Client Type, including `Erector`, never
creates a certification label. Only Salesforce-only Illinois Accounts whose
Account status is exactly `Certified` are included as certification-only cards.
Eligible Salesforce-only Accounts with complete addresses that match after
case, punctuation, and whitespace normalization are combined into one card:
their names are slash-separated, active certification categories are retained
for display grouping, and valid employee counts are summed. The first formatted address
is displayed. iMIS-only and ID-matched records are never address-merged. These
cards omit iMIS membership and tonnage lines. A matched certified Account without an active child remains in
the PDF and is reported in reconciliation as needing review.

Tonnage submissions may be monthly or irregular. Each report selects the most
recently completed calendar year (a 2026 run selects 2025) and totals unique
`(iMIS ID, Tonnage Year, Submission Date)` entries. Submission Date accepts ISO
timestamps, `MM/DD/YYYY`, 24-hour `MM/DD/YYYY HH:MM:SS`, and iMIS 12-hour
`MM/DD/YYYY HH:MM:SS AM/PM` timestamps. Valid timestamps are normalized in
memory, so equivalent 24-hour and AM/PM forms are duplicate keys. Exact
duplicate keys with the same tonnage are counted once. Missing timestamps,
conflicting same-key tonnage, and other invalid selected-year rows are excluded
and written to the required `--tonnage-review-csv`; review rows retain the
original exported timestamp, and the newest valid submission supplies company
display fields. The PDF labels the selected tonnage year.

Run with development environment settings:

```bash
uv run aisc_gr_statistics
```

Or run as a Python module:

```bash
uv run python -m aisc_gr_statistics
```

## Environment Variables

`.env.example` is the committed template. Copy it to `.env` for local development:

```bash
cp .env.example .env
```

- `LOG_LEVEL` defaults to `INFO`; `.env` sets it to `DEBUG` for more detailed console output.
- `LOG_FILE` defaults to `app.log` and controls where file logs are written.

The CLI automatically loads `.env` from the directory where you run it. Values
already set in your shell take precedence over `.env` values.

## Testing

Run the test suite:

```bash
uv run pytest
```

Run tests with coverage:

```bash
uv run pytest --cov
```

## Documentation

The [verified Salesforce mapping](docs/salesforce.md) documents the Account,
iMIS ID, Client Type, and child-certification relationships. Its live
validation uses read-only Salesforce metadata and SOQL requests only.

Preview documentation locally:

```bash
uv run python scripts/serve_docs.py
```

Build static documentation:

```bash
uv run mkdocs build
```
