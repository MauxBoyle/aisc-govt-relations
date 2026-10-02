"""Tests for the application entry point."""

import os
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest
from pypdf import PdfReader

import aisc_gr_statistics.app as app
from aisc_gr_statistics.app import (
    SnapshotStatus,
    _salesforce_accounts_if_configured,
    load_local_environment,
    main,
)
from aisc_gr_statistics.districts import DistrictAggregateRow, ReviewRow
from aisc_gr_statistics.salesforce import SalesforceError


def test_pyproject_keeps_both_console_script_names():
    configuration = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'aisc_gr_statistics = "aisc_gr_statistics.app:cli"' in configuration
    assert 'aisc-gr-statistics = "aisc_gr_statistics.app:cli"' in configuration


def test_no_argument_noninteractive_run_prints_status_and_usage_without_input(
    monkeypatch,
):
    output = StringIO()
    monkeypatch.setattr(app, "inspect_local_status", lambda: ([], None))
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("prompted"))

    main([], stdin=StringIO(), stdout=output)

    assert "Local data status:" in output.getvalue()
    assert "usage:" in output.getvalue()
    assert "explicit subcommand" in output.getvalue()


def test_no_argument_interactive_run_shows_status_menu_and_exits(monkeypatch):
    class Terminal(StringIO):
        def isatty(self):
            return True

    output = Terminal()
    monkeypatch.setattr(app, "inspect_local_status", lambda: ([], None))
    monkeypatch.setattr("builtins.input", lambda prompt: "7")

    main([], stdin=Terminal(), stdout=output)

    assert "Local data status:" in output.getvalue()
    assert "Create statewide report" in output.getvalue()
    assert "Exit" in output.getvalue()


def test_status_summary_shows_retrieval_dates_and_illinois_aggregate(monkeypatch):
    output = StringIO()
    aggregate = DistrictAggregateRow(
        "state",
        "IL",
        "17",
        "",
        "",
        10,
        200,
        6,
        4,
        unresolved_company_count=3,
    )
    monkeypatch.setattr(
        app,
        "inspect_local_status",
        lambda: (
            [SnapshotStatus("House representatives", True, "current", "2026-09-01")],
            aggregate,
        ),
    )

    app._print_status_summary(output)

    assert "retrieved 2026-09-01" in output.getvalue()
    assert "unresolved companies=3" in output.getvalue()
    assert "employee-data coverage=6/10" in output.getvalue()


def test_status_colors_reflect_usability_and_age_only_for_terminals():
    class Terminal(StringIO):
        def isatty(self):
            return True

    now = datetime(2026, 10, 2, tzinfo=UTC)
    fresh = SnapshotStatus("Fresh", True, "current", "2026-09-18T00:00:00Z")
    old = SnapshotStatus("Old", True, "current", "2026-09-17T23:59:59Z")
    missing_date = SnapshotStatus("Undated", True, "current")

    assert app._color_status_line("fresh", fresh, Terminal(), now=now).startswith("\033[32m")
    assert app._color_status_line("old", old, Terminal(), now=now).startswith("\033[37m")
    assert app._color_status_line("undated", missing_date, Terminal(), now=now).startswith("\033[31m")
    assert app._color_status_line("plain", fresh, StringIO(), now=now) == "plain"


def test_imis_status_uses_the_csv_modification_time(tmp_path, monkeypatch):
    imis = tmp_path / "imis.csv"
    imis.write_text("header\n", encoding="utf-8")
    modified = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    os.utime(imis, (modified.timestamp(), modified.timestamp()))
    monkeypatch.setattr(app, "DEFAULT_IMIS_CSV", imis)

    status = app._inspect_imis_source()

    assert status.usable
    assert status.retrieved_at == modified.isoformat()


def test_invalid_snapshot_status_includes_refresh_command():
    status = app._inspect_snapshot(
        "House representatives",
        lambda: (_ for _ in ()).throw(app.HouseDataError("checksum does not match")),
        "refresh-representatives",
    )

    assert not status.usable
    assert "not usable" in status.detail
    assert "refresh-representatives" in status.detail


def test_snapshot_status_reads_retrieval_date_from_metadata_returned_by_loader():
    status = app._inspect_snapshot(
        "Census boundaries",
        lambda: ({}, {"retrieved_at": "2026-09-01T12:00:00Z"}),
        "refresh-district-boundaries",
    )

    assert status.usable
    assert status.retrieved_at == "2026-09-01T12:00:00Z"


def test_menu_dispatches_to_existing_command_handler(monkeypatch):
    output = StringIO()
    calls = []
    parser = app._build_parser()
    monkeypatch.setattr("builtins.input", lambda prompt: next(choices))
    monkeypatch.setattr(
        app, "_menu_report", lambda parser: app.argparse.Namespace(command="report")
    )
    monkeypatch.setattr(
        app, "_run_report", lambda arguments: calls.append(arguments.command)
    )
    choices = iter(["1", "7"])

    app._run_menu(parser, output)

    assert calls == ["report"]


def test_menu_redisplays_status_after_an_action(monkeypatch):
    output = StringIO()
    parser = app._build_parser()
    choices = iter(["1", "7"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(choices))
    monkeypatch.setattr(
        app, "_menu_report", lambda parser: app.argparse.Namespace(command="report")
    )
    monkeypatch.setattr(app, "_run_report", lambda arguments: None)
    monkeypatch.setattr(app, "inspect_local_status", lambda: ([], None))

    app._run_menu(parser, output)

    assert output.getvalue().count("Local data status:") == 1
    assert "Update district assignments and review queue" in output.getvalue()
    assert "Update House representatives or Senate senators" in output.getvalue()


def test_load_local_environment_reads_credentials_without_overriding_shell(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        'SF_CLIENT_ID=from-file\nSF_CLIENT_SECRET="saved secret"\n',
        encoding="utf-8",
    )
    environment = {"SF_CLIENT_ID": "from-shell"}

    load_local_environment(dotenv, environment)

    assert environment == {
        "SF_CLIENT_ID": "from-shell",
        "SF_CLIENT_SECRET": "saved secret",
    }


def test_report_command_creates_a_pdf_without_salesforce_credentials(tmp_path):
    """Keep the local CSV-only workflow usable without Salesforce access."""
    external_output = tmp_path / "external-report.pdf"
    internal_output = tmp_path / "internal-report.pdf"
    conflicts = tmp_path / "conflicts.csv"
    candidates = tmp_path / "candidates.csv"
    reconciliation_csv = tmp_path / "reconciliation.csv"
    reconciliation_log = tmp_path / "reconciliation.log"
    unknown_imis_codes = tmp_path / "unknown-imis-codes.csv"
    tonnage_review = tmp_path / "tonnage-review.csv"

    main(
        [
            "report",
            "--imis-csv",
            "tests/fixtures/imis-membership-sample.csv",
            "--imis-export-date",
            "2026-09-18",
            "--external-output",
            str(external_output),
            "--internal-output",
            str(internal_output),
            "--conflicts-csv",
            str(conflicts),
            "--candidate-matches-csv",
            str(candidates),
            "--reconciliation-csv",
            str(reconciliation_csv),
            "--reconciliation-log",
            str(reconciliation_log),
            "--unknown-imis-codes-csv",
            str(unknown_imis_codes),
            "--tonnage-review-csv",
            str(tonnage_review),
        ]
    )

    assert external_output.read_bytes().startswith(b"%PDF")
    assert internal_output.read_bytes().startswith(b"%PDF")
    assert conflicts.read_text(encoding="utf-8").startswith("shared iMIS ID")
    assert candidates.read_text(encoding="utf-8").startswith("iMIS ID")
    assert reconciliation_csv.read_text(encoding="utf-8").startswith("classification")
    assert "Matched records:" in reconciliation_log.read_text(encoding="utf-8")
    assert unknown_imis_codes.read_text(encoding="utf-8").startswith("iMIS field")
    assert tonnage_review.read_text(encoding="utf-8").startswith("iMIS ID")
    assert _salesforce_accounts_if_configured({"SF_CLIENT_ID": "id"}) == ([], None)


def test_report_exclusions_omit_combined_outputs_but_keep_source_wide_audits(tmp_path):
    imis_csv = tmp_path / "members.csv"
    imis_csv.write_text(
        "iMIS ID,Full Name,State Province,City,Member Type,Category,Tonnage Year,Submission Date,Bridge Tonnage,Building Tonnage,S C Tonnage\n"
        "A,Test Company,IL,Chicago,UNKNOWN,FAB,2025,2025-01-01,1,2,3\n"
        "B,Keep Steel,IL,Aurora,ACT,FAB,2025,2025-01-01,4,5,6\n"
        "C,Test Company Invalid,IL,Chicago,ACT,FAB,2025,2025-01-02,not-a-number,2,3\n",
        encoding="utf-8",
    )
    destinations = {
        "--external-output": tmp_path / "external.pdf",
        "--internal-output": tmp_path / "internal.pdf",
        "--conflicts-csv": tmp_path / "conflicts.csv",
        "--candidate-matches-csv": tmp_path / "candidates.csv",
        "--reconciliation-csv": tmp_path / "reconciliation.csv",
        "--reconciliation-log": tmp_path / "reconciliation.log",
        "--unknown-imis-codes-csv": tmp_path / "unknown.csv",
        "--tonnage-review-csv": tmp_path / "tonnage.csv",
    }
    arguments = [
        "report",
        "--imis-csv",
        str(imis_csv),
        "--imis-export-date",
        "2026-09-18",
    ]
    for option, path in destinations.items():
        arguments.extend((option, str(path)))

    main(arguments)

    pdf_text = "\n".join(
        page.extract_text() or ""
        for page in PdfReader(destinations["--internal-output"]).pages
    )
    assert "Keep Steel" in pdf_text
    assert "Test Company" not in pdf_text
    assert "Test Company" not in destinations["--conflicts-csv"].read_text(
        encoding="utf-8"
    )
    assert "Test Company" not in destinations["--candidate-matches-csv"].read_text(
        encoding="utf-8"
    )
    assert "Test Company" not in destinations["--reconciliation-csv"].read_text(
        encoding="utf-8"
    )
    assert "UNKNOWN" in destinations["--unknown-imis-codes-csv"].read_text(
        encoding="utf-8"
    )
    assert "C" in destinations["--tonnage-review-csv"].read_text(encoding="utf-8")


def test_report_requires_an_imis_export_date():
    with pytest.raises(SystemExit):
        main(["report", "--imis-csv", "members.csv"])


def test_report_pdf_uses_the_supplied_imis_export_date(tmp_path):
    external_output = tmp_path / "external-report.pdf"
    internal_output = tmp_path / "internal-report.pdf"
    destinations = {
        "--conflicts-csv": tmp_path / "conflicts.csv",
        "--candidate-matches-csv": tmp_path / "candidates.csv",
        "--reconciliation-csv": tmp_path / "reconciliation.csv",
        "--reconciliation-log": tmp_path / "reconciliation.log",
        "--unknown-imis-codes-csv": tmp_path / "unknown.csv",
        "--tonnage-review-csv": tmp_path / "tonnage.csv",
    }
    arguments = [
        "report",
        "--imis-csv",
        "tests/fixtures/imis-membership-sample.csv",
        "--imis-export-date",
        "2026-09-17",
        "--external-output",
        str(external_output),
        "--internal-output",
        str(internal_output),
    ]
    for name, path in destinations.items():
        arguments.extend((name, str(path)))

    main(arguments)

    text = "\n".join(
        page.extract_text() or "" for page in PdfReader(internal_output).pages
    )
    assert "iMIS export: imis-membership-sample.csv (exported 2026-09-17)" in text
    external_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(external_output).pages
    )
    assert "Report provenance" not in external_text


def test_salesforce_load_records_a_utc_retrieval_time_after_success(monkeypatch):
    class Client:
        def query_records(self, *args, **kwargs):
            return [{"Id": "001"}]

    monkeypatch.setattr(
        "aisc_gr_statistics.app.create_client", lambda environment: Client()
    )

    accounts, retrieved_at = _salesforce_accounts_if_configured(
        {"SF_CLIENT_ID": "id", "SF_CLIENT_SECRET": "secret"}
    )

    assert accounts == [{"Id": "001"}]
    assert retrieved_at is not None
    assert retrieved_at.tzinfo is UTC


def test_failed_salesforce_load_has_no_retrieval_time(monkeypatch):
    def fail_to_create_client(environment):
        raise SalesforceError("authentication failed")

    monkeypatch.setattr("aisc_gr_statistics.app.create_client", fail_to_create_client)

    assert _salesforce_accounts_if_configured(
        {"SF_CLIENT_ID": "id", "SF_CLIENT_SECRET": "secret"}
    ) == ([], None)


def test_enrich_districts_writes_outputs_after_census_outage(tmp_path, monkeypatch):
    """Partial enrichment remains inspectable even when Census is unavailable."""
    districts = tmp_path / "districts.csv"
    review = tmp_path / "review.csv"
    conversions = tmp_path / "address-conversions.csv"

    monkeypatch.setattr(
        "aisc_gr_statistics.app.enrich_companies",
        lambda *args, **kwargs: ([], [], [], True),
    )
    main(
        [
            "enrich-districts",
            "--imis-csv",
            "tests/fixtures/imis-membership-sample.csv",
            "--districts-csv",
            str(districts),
            "--review-csv",
            str(review),
            "--address-conversions-csv",
            str(conversions),
            "--as-of",
            "2026-09-30",
        ]
    )

    assert districts.read_text(encoding="utf-8").startswith("company_name")
    assert review.read_text(encoding="utf-8").startswith("company_name")
    assert conversions.read_text(encoding="utf-8").startswith("company_name")


def test_enrich_districts_exits_nonzero_when_census_outage_leaves_unresolved_companies(
    tmp_path, monkeypatch
):
    """A partial Census outage must not look like a completed enrichment run."""
    districts = tmp_path / "districts.csv"
    review = tmp_path / "review.csv"
    conversions = tmp_path / "address-conversions.csv"
    unresolved_company = ReviewRow(
        "Unresolved Steel",
        "Fabricator",
        "123",
        "",
        "iMIS",
        "1 Main St",
        "Chicago",
        "IL",
        "60601",
        "Census service error",
        "",
    )

    monkeypatch.setattr(
        "aisc_gr_statistics.app.enrich_companies",
        lambda *args, **kwargs: ([], [unresolved_company], [], True),
    )

    with pytest.raises(SystemExit, match="1"):
        main(
            [
                "enrich-districts",
                "--imis-csv",
                "tests/fixtures/imis-membership-sample.csv",
                "--districts-csv",
                str(districts),
                "--review-csv",
                str(review),
                "--address-conversions-csv",
                str(conversions),
                "--as-of",
                "2026-09-30",
            ]
        )

    assert districts.read_text(encoding="utf-8").startswith("company_name")
    assert review.read_text(encoding="utf-8").startswith("company_name")
    assert conversions.read_text(encoding="utf-8").startswith("company_name")


def test_aggregate_districts_writes_a_csv(tmp_path, monkeypatch):
    from aisc_gr_statistics.districts import DistrictAggregateRow

    output = tmp_path / "aggregates.csv"
    districts = tmp_path / "districts.csv"
    review = tmp_path / "review.csv"
    districts.write_text("snapshot", encoding="utf-8")
    review.write_text("snapshot", encoding="utf-8")
    monkeypatch.setattr(
        "aisc_gr_statistics.app.aggregate_districts",
        lambda *args: [DistrictAggregateRow("national", "", "", "", "", 1, 10, 1, 0)],
    )
    main(
        [
            "aggregate-districts",
            "--imis-csv",
            "members.csv",
            "--districts-csv",
            str(districts),
            "--review-csv",
            str(review),
            "--aggregates-csv",
            str(output),
        ]
    )

    assert output.read_text(encoding="utf-8").startswith("scope,state,state_fips")


def test_report_command_never_constructs_a_census_geocoder(tmp_path, monkeypatch):
    """Normal PDF creation stays offline with respect to Census."""
    monkeypatch.setattr(
        "aisc_gr_statistics.districts.CensusGeocoder",
        lambda: (_ for _ in ()).throw(AssertionError("Census was used")),
    )
    destinations = {
        "--external-output": tmp_path / "external.pdf",
        "--internal-output": tmp_path / "internal.pdf",
        "--conflicts-csv": tmp_path / "conflicts.csv",
        "--candidate-matches-csv": tmp_path / "candidates.csv",
        "--reconciliation-csv": tmp_path / "reconciliation.csv",
        "--reconciliation-log": tmp_path / "reconciliation.log",
        "--unknown-imis-codes-csv": tmp_path / "unknown.csv",
        "--tonnage-review-csv": tmp_path / "tonnage.csv",
    }
    arguments = [
        "report",
        "--imis-csv",
        "tests/fixtures/imis-membership-sample.csv",
        "--imis-export-date",
        "2026-09-18",
    ]
    for option, path in destinations.items():
        arguments.extend((option, str(path)))
    main(arguments)
