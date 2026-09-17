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
  --output data/processed/illinois-certification-membership.pdf
```

The source export and generated PDF should stay in the ignored `data/raw/` and
`data/processed/` folders. The report reads Salesforce only when both
`SF_CLIENT_ID` and `SF_CLIENT_SECRET` are set; otherwise, certification status
is shown as a placeholder.

Run with development environment settings:

```bash
uv run --env-file .env aisc_gr_statistics
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

`uv run --env-file .env` loads the development environment explicitly; `.env` is not loaded automatically.

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
