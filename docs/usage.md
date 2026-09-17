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
reads Salesforce Account records with their child certifications. It displays
the Account certification status and each active child certification for an
exact normalized company-name match. A child certification is active only when
its status is `Active` and today's date is inclusively between its start and end
dates. If more than one Salesforce Account shares a normalized name, the iMIS
company is not enriched.

The report also adds Salesforce-only Accounts when their `BillingState` is
`IL` or `Illinois` and they have at least one active child certification. These
rows use Salesforce billing-address data when available and placeholders for
iMIS-only membership type, tonnage, and congressional district. The report
continues to use placeholders for U.S. Senators and Representatives.

## Environment Variables

| Variable    | Default    | Description                          |
|-------------|------------|--------------------------------------|
| `LOG_LEVEL` | `INFO`     | Console log level (DEBUG, INFO, …)   |
| `LOG_FILE`  | `app.log`  | Path to the log file                 |

Copy `.env.example` to `.env` for development defaults, then run with `uv run --env-file .env`.
