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
  --unknown-imis-codes-csv data/processed/unknown-imis-codes.csv
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
Without Salesforce access, certification values are shown as placeholders.

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
