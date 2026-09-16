"""Application entry point, logging setup, and command-line interface."""

import argparse
import os
import sys
from pathlib import Path

from loguru import logger

from .report import build_report_companies, read_imis_companies, render_illinois_report
from .salesforce import SalesforceError, create_client
from .salesforce_fields import REPORT_ACCOUNT_FIELDS


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
    configure_logging()
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "report":
        _run_report(arguments)
        return
    logger.info("Hello from aisc_gr_statistics!")


def cli():
    """Run the installed console command with the shell's arguments."""
    main(sys.argv[1:])


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
        "--output", required=True, type=Path, help="Destination PDF path."
    )
    return parser


def _run_report(arguments):
    """Prepare the report and enrich it only when both Salesforce secrets exist."""
    companies = read_imis_companies(arguments.imis_csv)
    accounts = _salesforce_accounts_if_configured()
    report_companies = build_report_companies(companies, accounts)
    render_illinois_report(report_companies, arguments.output)
    logger.info("Created Illinois report: {}", arguments.output)


def _salesforce_accounts_if_configured(environment=None):
    """Return Salesforce accounts only when both report credentials are configured."""
    environment = environment if environment is not None else os.environ
    if not all(
        environment.get(name, "").strip()
        for name in ("SF_CLIENT_ID", "SF_CLIENT_SECRET")
    ):
        logger.info(
            "Salesforce credentials are not configured; certification statuses are placeholders."
        )
        return []
    try:
        client = create_client(environment)
        return client.query_records("Account", REPORT_ACCOUNT_FIELDS, order_by="Name")
    except SalesforceError as error:
        logger.warning(
            "Salesforce could not be read; certification statuses are placeholders: {}",
            error,
        )
        return []
