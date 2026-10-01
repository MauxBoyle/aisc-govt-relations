"""Application entry point, logging setup, and command-line interface."""

import argparse
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from loguru import logger

from .census_map_references import MapReferenceError, refresh_map_references
from .districts import (
    DistrictSnapshotError,
    aggregate_districts,
    enrich_companies,
    write_address_conversions_csv,
    write_district_aggregates_csv,
    write_districts_csv,
    write_review_csv,
)
from .external_district_report import (
    DistrictReportError,
    district_filename,
    render_house_report,
    render_senate_report,
    senate_filename,
)
from .house import (
    HouseDataError,
    members_for_state,
)
from .house import (
    load_snapshot as load_house_snapshot,
)
from .house import (
    refresh_snapshot as refresh_house_snapshot,
)
from .report import (
    ReportAudience,
    build_reconciliation_rows,
    build_report_companies,
    candidate_matches,
    combine_companies,
    combined_conflicts,
    filter_report_exclusions,
    find_undefined_imis_codes,
    load_report_exclusion_phrases,
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
    if arguments.command == "refresh-representatives":
        _run_refresh_representatives()
        return
    if arguments.command == "enrich-districts":
        _run_enrich_districts(arguments)
        return
    if arguments.command == "aggregate-districts":
        _run_aggregate_districts(arguments)
        return
    if arguments.command == "district-report":
        _run_district_report(arguments)
        return
    if arguments.command == "refresh-district-boundaries":
        _run_refresh_district_boundaries(arguments)
        return
    if arguments.command == "refresh-map-references":
        _run_refresh_map_references(arguments)
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
    subcommands.add_parser(
        "refresh-representatives",
        help="Download and validate the official House current-member snapshot.",
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
    districts.add_argument(
        "--address-conversions-csv",
        required=True,
        type=Path,
        help="Destination CSV showing source addresses and normalized Census fields.",
    )
    aggregates = subcommands.add_parser(
        "aggregate-districts",
        help="Create national and congressional-district job aggregates.",
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
    district_report = subcommands.add_parser(
        "district-report",
        help="Create one- or two-page external Illinois district PDFs offline.",
    )
    district_report.add_argument(
        "--district",
        action="append",
        type=int,
        help="Illinois House district; repeat to select several.",
    )
    district_report.add_argument(
        "--all-districts",
        action="store_true",
        help="Create all 17 Illinois House reports.",
    )
    district_report.add_argument(
        "--senate",
        action="store_true",
        help="Also create the Illinois Senate delegation report.",
    )
    district_report.add_argument(
        "--districts-csv",
        type=Path,
        default=Path("data/processed/company-districts.csv"),
        help="Saved district snapshot (default: data/processed/company-districts.csv).",
    )
    district_report.add_argument(
        "--aggregates-csv",
        type=Path,
        default=Path("data/processed/district-aggregates.csv"),
        help="Saved aggregate snapshot (default: data/processed/district-aggregates.csv).",
    )
    district_report.add_argument(
        "--output-dir", type=Path, default=Path("data/processed")
    )
    district_report.add_argument(
        "--as-of", default="", help="Optional YYYY-MM-DD saved district-data date."
    )
    boundaries = subcommands.add_parser(
        "refresh-district-boundaries",
        help="Refresh the reviewed Census KML boundary snapshot.",
    )
    boundaries.add_argument(
        "--source-url", help="Optional official Census KML ZIP URL override."
    )
    boundaries.add_argument(
        "--congressional-session", type=int, help="Optional Congress number override."
    )
    map_references = subcommands.add_parser(
        "refresh-map-references",
        help="Refresh reviewed Census place and county map-reference snapshots.",
    )
    map_references.add_argument(
        "--places-source-url",
        help="Optional official Census place Gazetteer ZIP URL override.",
    )
    map_references.add_argument(
        "--counties-source-url",
        help="Optional official Census county Gazetteer ZIP URL override.",
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
    exclusion_phrases = load_report_exclusion_phrases()
    companies, accounts = filter_report_exclusions(
        companies, accounts, exclusion_phrases
    )
    combined = combine_companies(companies, accounts)
    report_companies = build_report_companies(combined, as_of=report_date)
    snapshot = load_snapshot()
    senators = senators_for_state(snapshot.senators, "IL")
    house_snapshot = load_house_snapshot()
    representatives = members_for_state(house_snapshot.members, "IL")
    render_illinois_report(
        report_companies,
        arguments.external_output,
        tonnage_year,
        representatives=representatives,
        house_clerk_source_url=house_snapshot.clerk_source_url,
        house_directory_source_url=house_snapshot.directory_source_url,
        house_retrieved_at=house_snapshot.retrieved_at,
        audience=ReportAudience.EXTERNAL,
    )
    render_illinois_report(
        report_companies,
        arguments.internal_output,
        tonnage_year,
        senators,
        snapshot.source_url,
        snapshot.retrieved_at,
        representatives=representatives,
        house_clerk_source_url=house_snapshot.clerk_source_url,
        house_directory_source_url=house_snapshot.directory_source_url,
        house_retrieved_at=house_snapshot.retrieved_at,
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


def _run_refresh_representatives():
    """Refresh checked-in House data only on an explicit maintainer command."""
    try:
        snapshot = refresh_house_snapshot()
    except HouseDataError as error:
        logger.error("House snapshot was not refreshed: {}", error)
        raise SystemExit(1) from error
    logger.info(
        "Refreshed House contacts for {} current seats from {}.",
        len(snapshot.members),
        snapshot.clerk_source_url,
    )


def _run_enrich_districts(arguments):
    """Create district, review, and conversion CSVs before reporting outages."""
    accounts, _ = _salesforce_accounts_if_configured()
    districts, reviews, conversions, service_failed = enrich_companies(
        arguments.imis_csv, accounts
    )
    write_districts_csv(districts, arguments.districts_csv)
    write_review_csv(reviews, arguments.review_csv)
    write_address_conversions_csv(conversions, arguments.address_conversions_csv)
    logger.info(
        "Created district enrichment files: matched={}, review={}, conversions={}",
        len(districts),
        len(reviews),
        len(conversions),
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


def _run_district_report(arguments):
    """Render selected PDFs from local snapshots only; no network calls occur."""
    try:
        if (
            not arguments.district
            and not arguments.all_districts
            and not arguments.senate
        ):
            raise DistrictReportError(
                "select --district, --all-districts, and/or --senate"
            )
        house = load_house_snapshot()
        senate = load_snapshot()
        districts = sorted(
            set(
                (list(range(1, 18)) if arguments.all_districts else [])
                + (arguments.district or [])
            )
        )
        output_paths = []
        for district in districts:
            if district < 1 or district > 17:
                raise DistrictReportError(
                    "Illinois House districts must be between 1 and 17."
                )
            output_paths.append(
                render_house_report(
                    district,
                    arguments.districts_csv,
                    arguments.aggregates_csv,
                    arguments.output_dir / district_filename(str(district)),
                    house.members,
                    house_photos=house.photos,
                    as_of=arguments.as_of,
                )
            )
        if arguments.senate:
            output_paths.append(
                render_senate_report(
                    arguments.districts_csv,
                    arguments.aggregates_csv,
                    arguments.output_dir / senate_filename(),
                    senate.senators,
                    as_of=arguments.as_of,
                )
            )
    except (
        DistrictReportError,
        DistrictSnapshotError,
        HouseDataError,
        SenateDataError,
        MapReferenceError,
        ValueError,
    ) as error:
        logger.error("District report was not created: {}", error)
        raise SystemExit(1) from error
    logger.info(
        "Created external district report(s): {}", ", ".join(map(str, output_paths))
    )


def _run_refresh_district_boundaries(arguments):
    from .census_boundaries import BoundarySnapshotError, refresh_boundary_snapshot

    try:
        options = {
            name: value
            for name, value in {
                "source_url": arguments.source_url,
                "congressional_session": arguments.congressional_session,
            }.items()
            if value is not None
        }
        refresh_boundary_snapshot(**options)
    except BoundarySnapshotError as error:
        logger.error("Census boundary snapshot was not refreshed: {}", error)
        raise SystemExit(1) from error


def _run_refresh_map_references(arguments):
    """Refresh map points explicitly; normal PDF rendering remains offline."""
    try:
        options = {
            name: value
            for name, value in {
                "places_source_url": arguments.places_source_url,
                "counties_source_url": arguments.counties_source_url,
            }.items()
            if value is not None
        }
        refresh_map_references(**options)
    except MapReferenceError as error:
        logger.error("Census map references were not refreshed: {}", error)
        raise SystemExit(1) from error


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
