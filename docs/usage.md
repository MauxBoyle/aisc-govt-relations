# Usage

## Installation

Clone the repository and install dependencies:

```bash
uv sync
```

## Running

Via the CLI entrypoint:

```bash
uv run aisc_gr_statistics                          # production defaults
uv run --env-file .env aisc_gr_statistics          # dev settings
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
  --output data/processed/illinois-certification-membership.pdf
```

Keep real exports under ignored `data/raw/imis/` and generated PDFs under
ignored `data/processed/`; neither should be committed. The command accepts
common forms of the company-name, state, membership-type, tonnage, and
congressional-district headers. Company name and state are required.
For the current iMIS export, the report totals `Bridge Tonnage`, `Building
Tonnage`, and `S C Tonnage` into Structural Steel Tonnage.

When both `SF_CLIENT_ID` and `SF_CLIENT_SECRET` are configured, the command
reads Salesforce Account records and displays a certification status only for
an exact normalized company-name match. Missing credentials, no match, or more
than one match leave the status as a placeholder. The report also intentionally
uses placeholders for the certification category and U.S. Senators and
Representatives because those data sources are not yet available.

## Environment Variables

| Variable    | Default    | Description                          |
|-------------|------------|--------------------------------------|
| `LOG_LEVEL` | `INFO`     | Console log level (DEBUG, INFO, …)   |
| `LOG_FILE`  | `app.log`  | Path to the log file                 |

Copy `.env.example` to `.env` for development defaults, then run with `uv run --env-file .env`.
