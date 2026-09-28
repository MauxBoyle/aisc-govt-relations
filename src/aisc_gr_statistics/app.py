"""Application entry point, logging setup, and command-line interface."""

import argparse
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from loguru import logger

from .districts import (
    DistrictSnapshotError,
    aggregate_districts,
    enrich_companies,
    write_district_aggregates_csv,
    write_districts_csv,
    write_review_csv,
)
from .report import (
    ReportAudience,
    build_reconciliation_rows,
    build_report_companies,
    candidate_matches,
    combine_companies,
    combined_conflicts,
    find_undefined_imis_codes,
    read_imis_companies_with_tonnage_review,
    render_illinois_report,
    write_candidate_matches_csv,
    write_conflicts_csv,
    write_reconciliation_csv,
    write_reconciliation_log,
    write_tonnage_review_csv,
    write_undefined_imis_codes_csv,
)
from .salesforce import SalesforceError, create_client
from .salesforce_fields import REPORT_ACCOUNT_FIELDS
from .senate import SenateDataError, load_snapshot, refresh_snapshot, senators_for_state


def configure_logging():
    """Configure loguru for console and file logging.

    Removes the default handler and sets up:
    - stderr handler at LOG_LEVEL (default: INFO, configurable via env var)
    - File handler at DEBUG level writing to LOG_FILE (default: app.log)
    """
    import os

    log_level = os.environ.get("LOG_LEVEL", "INFO")
    log_file = os.environ.get("LOG_FILE", "app.log")
    logger.remove()
    logger.add(sys.stderr, level=log_level)
    logger.add(log_file, level="DEBUG", rotation="50 KB", retention=1)


def main(argv=()):
    """Run the application with explicitly supplied command-line arguments."""
    load_local_environment()
    configure_logging()
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "report":
        _run_report(arguments)
        return
    if arguments.command == "refresh-senators":
        _run_refresh_senators()
        return
    if arguments.command == "enrich-districts":
        _run_enrich_districts(arguments)
        return
    if arguments.command == "aggregate-districts":
        _run_aggregate_districts(arguments)
        return
    logger.info("Hello from aisc_gr_statistics!")


def cli():
    """Run the installed console command with the shell's arguments."""
    main(sys.argv[1:])


def load_local_environment(path=Path(".env"), environment=None):
    """Load simple ``KEY=value`` entries from a local .env file.

    Existing environment variables win, which lets a shell or deployment
    environment intentionally override local development settings.
    """
    environment = environment if environment is not None else os.environ
    environment_path = Path(path)
    if not environment_path.is_file():
        return
    for line_number, raw_line in enumerate(
        environment_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            logger.warning("Ignoring invalid .env line {}.", line_number)
            continue
        name, value = line.split("=", maxsplit=1)
        name = name.strip()
        value = _unquote_environment_value(value.strip())
        if name and name not in environment:
            environment[name] = value


def _unquote_environment_value(value):
    """Remove one matching pair of quotes from a .env value."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _build_parser():
    parser = argparse.ArgumentParser(prog="aisc_gr_statistics")
    subcommands = parser.add_subparsers(dest="command")
    report = subcommands.add_parser(
        "report", help="Create a printable statewide Illinois PDF report."
    )
    report.add_argument(
        "--imis-csv", required=True, type=Path, help="Path to an iMIS CSV export."
    )
    report.add_argument(
        "--imis-export-date",
        required=True,
        type=_iso_date,
        help="Date the iMIS CSV export was created, in YYYY-MM-DD format.",
    )
    report.add_argument(
        "--external-output",
        required=True,
        type=Path,
        help="Destination PDF path for the externally shareable report.",
    )
    report.add_argument(
        "--internal-output",
        required=True,
        type=Path,
        help="Destination PDF path for the detailed internal report.",
    )
    report.add_argument(
        "--conflicts-csv",
        required=True,
        type=Path,
        help="Destination CSV for ID-joined value conflicts and duplicate-ID review.",
    )
    report.add_argument(
        "--candidate-matches-csv",
        required=True,
        type=Path,
        help="Destination CSV for review-only name/location candidates.",
    )
    report.add_argument(
        "--reconciliation-csv",
        required=True,
        type=Path,
        help="Destination CSV for complete iMIS/Salesforce reconciliation review.",
    )
    report.add_argument(
        "--reconciliation-log",
        required=True,
        type=Path,
        help="Destination readable log summarizing reconciliation findings.",
    )
    report.add_argument(
        "--unknown-imis-codes-csv",
        required=True,
        type=Path,
        help="Destination CSV for blank and unconfirmed iMIS Type/Category codes.",
    )
    report.add_argument(
        "--tonnage-review-csv",
        required=True,
        type=Path,
        help="Destination CSV for selected-year tonnage rows excluded from totals.",
    )
    subcommands.add_parser(
        "refresh-senators",
        help="Download and validate the official Senate.gov contact snapshot.",
    )
    districts = subcommands.add_parser(
        "enrich-districts",
        help="Look up report companies' congressional districts using public Census data.",
    )
    districts.add_argument(
        "--imis-csv", required=True, type=Path, help="Path to an iMIS CSV export."
    )
    districts.add_argument(
        "--districts-csv",
        required=True,
        type=Path,
        help="Destination CSV for successfully assigned congressional districts.",
    )
    districts.add_argument(
        "--review-csv",
        required=True,
        type=Path,
        help="Destination CSV for addresses or Census results requiring review.",
    )
    aggregates = subcommands.add_parser(
        "aggregate-districts",
        help="Create offline national and congressional-district job aggregates.",
    )
    aggregates.add_argument(
        "--imis-csv", required=True, type=Path, help="Path to an iMIS CSV export."
    )
    aggregates.add_argument(
        "--districts-csv",
        required=True,
        type=Path,
        help="Previously generated company-districts.csv snapshot.",
    )
    aggregates.add_argument(
        "--aggregates-csv",
        required=True,
        type=Path,
        help="Destination CSV for national and district aggregates.",
    )
    return parser


def _run_report(arguments):
    """Prepare the report and enrich it only when both Salesforce secrets exist."""
    unknown_imis_codes = find_undefined_imis_codes(arguments.imis_csv)
    report_date = date.today()
    companies, tonnage_findings, tonnage_year = read_imis_companies_with_tonnage_review(
        arguments.imis_csv, report_date=report_date
    )
    accounts, salesforce_retrieved_at = _salesforce_accounts_if_configured()
    combined = combine_companies(companies, accounts)
    report_companies = build_report_companies(combined, as_of=report_date)
    snapshot = load_snapshot()
    senators = senators_for_state(snapshot.senators, "IL")
    render_illinois_report(
        report_companies,
        arguments.external_output,
        tonnage_year,
        audience=ReportAudience.EXTERNAL,
    )
    render_illinois_report(
        report_companies,
        arguments.internal_output,
        tonnage_year,
        senators,
        snapshot.source_url,
        snapshot.retrieved_at,
        imis_export_filename=arguments.imis_csv.name,
        imis_export_date=arguments.imis_export_date,
        salesforce_retrieved_at=salesforce_retrieved_at,
        audience=ReportAudience.INTERNAL,
    )
    write_conflicts_csv(combined_conflicts(combined), arguments.conflicts_csv)
    write_candidate_matches_csv(
        candidate_matches(combined), arguments.candidate_matches_csv
    )
    reconciliation_rows = build_reconciliation_rows(combined, as_of=report_date)
    write_reconciliation_csv(reconciliation_rows, arguments.reconciliation_csv)
    write_reconciliation_log(reconciliation_rows, arguments.reconciliation_log)
    write_undefined_imis_codes_csv(unknown_imis_codes, arguments.unknown_imis_codes_csv)
    write_tonnage_review_csv(tonnage_findings, arguments.tonnage_review_csv)
    logger.info(
        "Created Illinois reports: external={}, internal={}",
        arguments.external_output,
        arguments.internal_output,
    )


def _run_refresh_senators():
    """Refresh the checked-in Senate.gov reference data on maintainer request."""
    try:
        snapshot = refresh_snapshot()
    except SenateDataError as error:
        logger.error("Senate snapshot was not refreshed: {}", error)
        raise SystemExit(1) from error
    logger.info(
        "Refreshed Senate contacts for {} senators from {}.",
        len(snapshot.senators),
        snapshot.source_url,
    )


def _run_enrich_districts(arguments):
    """Create district and review CSVs, failing after output on Census outages."""
    accounts, _ = _salesforce_accounts_if_configured()
    districts, reviews, service_failed = enrich_companies(arguments.imis_csv, accounts)
    write_districts_csv(districts, arguments.districts_csv)
    write_review_csv(reviews, arguments.review_csv)
    logger.info(
        "Created district enrichment files: matched={}, review={}",
        len(districts),
        len(reviews),
    )
    if service_failed:
        logger.error("One or more Census requests failed; review the partial outputs.")
        raise SystemExit(1)


def _run_aggregate_districts(arguments):
    """Create aggregates from saved district assignments without Census access."""
    accounts, _ = _salesforce_accounts_if_configured()
    try:
        rows = aggregate_districts(
            arguments.imis_csv, arguments.districts_csv, accounts
        )
    except DistrictSnapshotError as error:
        logger.error("District aggregates were not created: {}", error)
        raise SystemExit(1) from error
    write_district_aggregates_csv(rows, arguments.aggregates_csv)
    logger.info("Created district aggregate file: rows={}", len(rows))


def _salesforce_accounts_if_configured(environment=None):
    """Return Salesforce accounts and their UTC retrieval time when available."""
    environment = environment if environment is not None else os.environ
    if not all(
        environment.get(name, "").strip()
        for name in ("SF_CLIENT_ID", "SF_CLIENT_SECRET")
    ):
        logger.info(
            "Salesforce credentials are not configured; certification statuses are placeholders."
        )
        return [], None
    try:
        client = create_client(environment)
        accounts = client.query_records(
            "Account", REPORT_ACCOUNT_FIELDS, order_by="Name"
        )
        return accounts, datetime.now(UTC)
    except SalesforceError as error:
        logger.warning(
            "Salesforce could not be read; certification statuses are placeholders: {}",
            error,
        )
        return [], None


def _iso_date(value):
    """Parse a CLI date while requiring the documented ISO calendar-date form."""
    try:
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise ValueError
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must use YYYY-MM-DD format") from error
