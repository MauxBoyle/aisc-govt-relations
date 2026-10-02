"""Application entry point, logging setup, and command-line interface."""

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from loguru import logger

from .census_boundaries import BoundarySnapshotError, load_boundary_snapshot
from .census_map_references import (
    MapReferenceError,
    load_map_references,
    refresh_map_references,
)
from .districts import (
    DEFAULT_FALLBACK_CSV,
    DistrictSnapshotError,
    aggregate_districts,
    enrich_companies,
    validate_snapshot_metadata,
    write_address_conversions_csv,
    write_district_aggregates_csv,
    write_districts_csv,
    write_review_csv,
)
from .external_district_report import (
    DistrictReportError,
    all_districts_filename,
    district_filename,
    read_aggregates_csv,
    render_all_house_reports,
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

DEFAULT_IMIS_CSV = Path("data/raw/imis/imis-tonnage-for-gr-statistics.csv")
DEFAULT_DISTRICTS_CSV = Path("data/processed/company-districts.csv")
DEFAULT_REVIEW_CSV = Path("data/processed/address-district-review.csv")
DEFAULT_AGGREGATES_CSV = Path("data/processed/district-aggregates.csv")


@dataclass(frozen=True)
class SnapshotStatus:
    """A local data source's usability for the friendly CLI summary."""

    name: str
    usable: bool
    detail: str
    retrieved_at: str | None = None


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


def main(argv=(), *, stdin=None, stdout=None):
    """Run the application with explicitly supplied command-line arguments."""
    load_local_environment()
    configure_logging()
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command:
        _dispatch(arguments)
        return
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    _print_status_summary(stdout)
    if stdin.isatty() and stdout.isatty():
        _run_menu(parser, stdout)
    else:
        parser.print_usage(file=stdout)
        print(
            "Run an explicit subcommand for an action; no data was changed.",
            file=stdout,
        )


def cli():
    """Run the installed console command with the shell's arguments."""
    main(sys.argv[1:])


def _dispatch(arguments):
    """Send parsed explicit commands and menu choices to the same handlers."""
    handlers = {
        "report": lambda: _run_report(arguments),
        "refresh-senators": _run_refresh_senators,
        "refresh-representatives": _run_refresh_representatives,
        "enrich-districts": lambda: _run_enrich_districts(arguments),
        "aggregate-districts": lambda: _run_aggregate_districts(arguments),
        "district-report": lambda: _run_district_report(arguments),
        "refresh-district-boundaries": lambda: _run_refresh_district_boundaries(
            arguments
        ),
        "refresh-map-references": lambda: _run_refresh_map_references(arguments),
    }
    handlers[arguments.command]()


def inspect_local_status():
    """Inspect local inputs without network access or raising CLI-stopping errors."""
    statuses = [
        _inspect_imis_source(),
        _inspect_snapshot(
            "House representatives", load_house_snapshot, "refresh-representatives"
        ),
        _inspect_snapshot("Senate senators", load_snapshot, "refresh-senators"),
        _inspect_snapshot(
            "Census boundaries", load_boundary_snapshot, "refresh-district-boundaries"
        ),
        _inspect_snapshot(
            "Census map references", load_map_references, "refresh-map-references"
        ),
        _inspect_snapshot(
            "District assignments",
            lambda: validate_snapshot_metadata(DEFAULT_DISTRICTS_CSV, "districts"),
            "enrich-districts",
        ),
        _inspect_snapshot(
            "District review queue",
            lambda: validate_snapshot_metadata(DEFAULT_REVIEW_CSV, "review"),
            "enrich-districts",
        ),
    ]
    aggregates, aggregate_row = _inspect_aggregates()
    statuses.append(aggregates)
    return statuses, aggregate_row


def _inspect_imis_source():
    if DEFAULT_IMIS_CSV.is_file() and os.access(DEFAULT_IMIS_CSV, os.R_OK):
        modified_at = datetime.fromtimestamp(DEFAULT_IMIS_CSV.stat().st_mtime, UTC)
        return SnapshotStatus(
            "iMIS source", True, str(DEFAULT_IMIS_CSV), _display_date(modified_at)
        )
    return SnapshotStatus(
        "iMIS source",
        False,
        f"not usable; add or select {DEFAULT_IMIS_CSV} before running report or enrichment",
    )


def _inspect_snapshot(name, loader, refresh_command):
    try:
        snapshot = loader()
    except (
        BoundarySnapshotError,
        DistrictSnapshotError,
        HouseDataError,
        MapReferenceError,
        SenateDataError,
        OSError,
        ValueError,
    ) as error:
        return SnapshotStatus(
            name,
            False,
            f"not usable: {error}. Run `aisc-gr-statistics {refresh_command}`.",
        )
    retrieved_at = getattr(snapshot, "retrieved_at", None)
    if retrieved_at is None and isinstance(snapshot, dict):
        retrieved_at = snapshot.get("retrieved_at")
    metadata = getattr(snapshot, "metadata", None)
    if retrieved_at is None and isinstance(metadata, dict):
        retrieved_at = metadata.get("retrieved_at")
    if (
        retrieved_at is None
        and isinstance(snapshot, tuple)
        and snapshot
        and isinstance(snapshot[-1], dict)
    ):
        retrieved_at = snapshot[-1].get("retrieved_at")
    return SnapshotStatus(name, True, "current", _display_date(retrieved_at))


def _inspect_aggregates():
    try:
        districts = validate_snapshot_metadata(DEFAULT_DISTRICTS_CSV, "districts")
        metadata = validate_snapshot_metadata(DEFAULT_AGGREGATES_CSV, "aggregates")
        if metadata.get("districts_sha256") != districts.get("sha256"):
            raise DistrictSnapshotError(
                "aggregate snapshot was not generated from this exact district snapshot"
            )
        rows = read_aggregates_csv(DEFAULT_AGGREGATES_CSV)
        illinois = [row for row in rows if row.scope == "state" and row.state == "IL"]
        if len(illinois) != 1:
            raise DistrictReportError(
                "required Illinois aggregate row is missing or duplicated"
            )
    except (DistrictSnapshotError, DistrictReportError, OSError, ValueError) as error:
        return (
            SnapshotStatus(
                "District aggregates",
                False,
                f"not usable: {error}. Run `aisc-gr-statistics aggregate-districts`.",
            ),
            None,
        )
    return (
        SnapshotStatus(
            "District aggregates",
            True,
            "current",
            _display_date(metadata.get("retrieved_at")),
        ),
        illinois[0],
    )


def _display_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _print_status_summary(output):
    """Print concise local-data status, with color only for terminal output."""
    statuses, illinois = inspect_local_status()
    print("Local data status:", file=output)
    for status in statuses:
        retrieval = f" (retrieved {status.retrieved_at})" if status.retrieved_at else ""
        state = "usable" if status.usable else "not usable"
        line = f"- {status.name}: {state} — {status.detail}{retrieval}"
        print(_color_status_line(line, status, output), file=output)
    if illinois is not None:
        print(
            f"- Illinois aggregate: unresolved companies={illinois.unresolved_company_count}, employee-data coverage={illinois.companies_with_employee_data}/{illinois.included_company_count}",
            file=output,
        )


def _color_status_line(line, status, output, *, now=None):
    """Color a whole status line when output is a terminal.

    Red means unavailable or undated, green means data at most 14 days old,
    and white means usable but older local data.
    """
    if not output.isatty():
        return line
    color = _status_color(status, now=now)
    return f"\033[{color}m{line}\033[0m"


def _status_color(status, *, now=None):
    if not status.usable:
        return 31
    timestamp = _status_datetime(status.retrieved_at)
    if timestamp is None:
        return 31
    now = now if now is not None else datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return 32 if now.astimezone(UTC) - timestamp <= timedelta(days=14) else 37


def _status_datetime(value):
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if timestamp.tzinfo is None:
        return None
    return timestamp.astimezone(UTC)


def _run_menu(parser, output):
    """Offer repeatable terminal-only shortcuts for the documented workflows."""
    menu = {
        "1": ("Create statewide report", _menu_report),
        "2": ("Update district assignments and review queue", _menu_enrich),
        "3": ("Update district aggregates", _menu_aggregate),
        "4": ("Create district report", _menu_district_report),
        "5": ("Update House representatives or Senate senators", _menu_representatives),
        "6": ("Update Census boundaries or map references", _menu_census),
        "7": ("Exit", None),
    }
    while True:
        print("\nMenu:", file=output)
        for key, (label, _) in menu.items():
            print(f"  {key}. {label}", file=output)
        choice = input("Choose an option: ").strip()
        if choice == "7":
            return
        item = menu.get(choice)
        if item is None:
            print("Please enter a menu number from 1 to 7.", file=output)
            continue
        arguments = item[1](parser)
        if arguments is not None:
            _dispatch(arguments)
            _print_status_summary(output)


def _menu_date(prompt):
    while True:
        try:
            return _iso_date(input(prompt).strip()).isoformat()
        except argparse.ArgumentTypeError:
            print("Please use YYYY-MM-DD.")


def _menu_report(parser):
    export_date = _menu_date("iMIS export date (YYYY-MM-DD): ")
    return parser.parse_args(
        [
            "report",
            "--imis-csv",
            str(DEFAULT_IMIS_CSV),
            "--imis-export-date",
            export_date,
            "--external-output",
            "data/processed/illinois-certification-membership-external.pdf",
            "--internal-output",
            "data/processed/illinois-certification-membership-internal.pdf",
            "--conflicts-csv",
            "data/processed/field-conflicts.csv",
            "--candidate-matches-csv",
            "data/processed/candidate-matches.csv",
            "--reconciliation-csv",
            "data/processed/reconciliation.csv",
            "--reconciliation-log",
            "data/processed/reconciliation.log",
            "--unknown-imis-codes-csv",
            "data/processed/unknown-imis-codes.csv",
            "--tonnage-review-csv",
            "data/processed/tonnage-review.csv",
        ]
    )


def _menu_enrich(parser):
    as_of = _menu_date("Snapshot date (YYYY-MM-DD): ")
    return parser.parse_args(
        [
            "enrich-districts",
            "--imis-csv",
            str(DEFAULT_IMIS_CSV),
            "--districts-csv",
            str(DEFAULT_DISTRICTS_CSV),
            "--review-csv",
            str(DEFAULT_REVIEW_CSV),
            "--address-conversions-csv",
            "data/processed/address-conversions.csv",
            "--as-of",
            as_of,
        ]
    )


def _menu_aggregate(parser):
    return parser.parse_args(
        [
            "aggregate-districts",
            "--imis-csv",
            str(DEFAULT_IMIS_CSV),
            "--districts-csv",
            str(DEFAULT_DISTRICTS_CSV),
            "--review-csv",
            str(DEFAULT_REVIEW_CSV),
            "--aggregates-csv",
            str(DEFAULT_AGGREGATES_CSV),
        ]
    )


def _menu_district_report(parser):
    while True:
        selection = input("District number, or 'all': ").strip().lower()
        if selection == "all":
            district_arguments = ["--all-districts"]
            break
        try:
            district_arguments = ["--district", str(int(selection))]
            break
        except ValueError:
            print("Please enter a district number or 'all'.")
    senate = input("Include Senate delegation? [y/N]: ").strip().lower() in {"y", "yes"}
    as_of = _menu_date("Snapshot date (YYYY-MM-DD): ")
    arguments = [
        "district-report",
        "--districts-csv",
        str(DEFAULT_DISTRICTS_CSV),
        "--aggregates-csv",
        str(DEFAULT_AGGREGATES_CSV),
        "--output-dir",
        "data/processed",
        "--as-of",
        as_of,
    ]
    arguments.extend(district_arguments)
    if senate:
        arguments.append("--senate")
    return parser.parse_args(arguments)


def _menu_representatives(parser):
    commands = {"1": ["refresh-representatives"], "2": ["refresh-senators"]}
    while True:
        choice = input("Refresh (1) House or (2) Senate? ").strip()
        if choice in commands:
            return parser.parse_args(commands[choice])
        print("Please enter 1 or 2.")


def _menu_census(parser):
    commands = {"1": ["refresh-district-boundaries"], "2": ["refresh-map-references"]}
    while True:
        choice = input("Refresh (1) boundaries or (2) map references? ").strip()
        if choice in commands:
            return parser.parse_args(commands[choice])
        print("Please enter 1 or 2.")


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
    parser = argparse.ArgumentParser(prog="aisc-gr-statistics")
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
        "--fallback-csv",
        type=Path,
        default=DEFAULT_FALLBACK_CSV,
        help="Reviewed exact-match geography fallback CSV (default: config/reviewed-geography-fallback.csv).",
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
    districts.add_argument(
        "--as-of",
        required=True,
        type=_iso_date,
        help="Certification-effective report date, in YYYY-MM-DD format.",
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
        "--review-csv",
        required=True,
        type=Path,
        help="Previously generated address-district-review.csv unresolved queue.",
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
        "--as-of",
        required=True,
        type=_iso_date,
        help="Required YYYY-MM-DD date matching the district snapshot.",
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
        arguments.imis_csv,
        accounts,
        lookup_date=arguments.as_of,
        fallback_csv=arguments.fallback_csv,
    )
    write_districts_csv(
        districts,
        arguments.districts_csv,
        as_of=arguments.as_of,
        unresolved_count=len(reviews),
        included_company_count=len(districts),
    )
    write_review_csv(reviews, arguments.review_csv)
    write_address_conversions_csv(conversions, arguments.address_conversions_csv)
    logger.info(
        "Created district enrichment files: confirmed={}, unresolved={}, conversions={}",
        len(districts),
        len(reviews),
        len(conversions),
    )
    if service_failed and reviews:
        logger.error(
            "One or more Census requests failed and left companies unresolved; "
            "the partial outputs require review."
        )
        raise SystemExit(1)
    if service_failed:
        logger.warning(
            "One or more Census requests failed; reviewed fallback assignments "
            "resolved every affected company."
        )


def _run_aggregate_districts(arguments):
    """Create aggregates from saved district assignments without Census access."""
    accounts, _ = _salesforce_accounts_if_configured()
    try:
        rows = aggregate_districts(
            arguments.imis_csv, arguments.districts_csv, arguments.review_csv, accounts
        )
    except DistrictSnapshotError as error:
        logger.error("District aggregates were not created: {}", error)
        raise SystemExit(1) from error
    write_district_aggregates_csv(
        rows,
        arguments.aggregates_csv,
        districts_csv=arguments.districts_csv,
        review_csv=arguments.review_csv,
    )
    logger.info("Created district aggregate file: rows={}", len(rows))


def _run_district_report(arguments):
    """Render selected PDFs from local snapshots only; no network calls occur."""
    try:
        metadata = validate_snapshot_metadata(arguments.districts_csv, "districts")
        if metadata.get("as_of") != arguments.as_of.isoformat():
            raise DistrictReportError(
                "--as-of must match the district snapshot metadata; rerun enrichment "
                "or use its recorded date"
            )
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
        output_paths = []
        for district in arguments.district or []:
            if district < 1 or district > 17:
                raise DistrictReportError(
                    "Illinois House districts must be between 1 and 17."
                )
            result = render_house_report(
                district,
                arguments.districts_csv,
                arguments.aggregates_csv,
                arguments.output_dir / district_filename(str(district)),
                house.members,
                house_photos=house.photos,
                as_of=arguments.as_of.isoformat(),
            )
            path = getattr(result, "output_path", result)
            if path is None:
                logger.info(
                    "No companies found for Illinois district {}; no PDF was created.",
                    district,
                )
            else:
                output_paths.append(path)
        if arguments.all_districts:
            result = render_all_house_reports(
                arguments.districts_csv,
                arguments.aggregates_csv,
                arguments.output_dir / all_districts_filename(),
                house.members,
                house_photos=house.photos,
                as_of=arguments.as_of.isoformat(),
            )
            skipped = getattr(result, "skipped_districts", ())
            if skipped:
                logger.info(
                    "Omitted districts with no companies from the combined report: {}.",
                    ", ".join(map(str, skipped)),
                )
            path = getattr(result, "output_path", result)
            if path is None:
                logger.info("No House PDF was created because no districts have companies.")
            else:
                output_paths.append(path)
        if arguments.senate:
            output_paths.append(
                render_senate_report(
                    arguments.districts_csv,
                    arguments.aggregates_csv,
                    arguments.output_dir / senate_filename(),
                    senate.senators,
                    as_of=arguments.as_of.isoformat(),
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
    if output_paths:
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
