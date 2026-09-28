"""Tests for the application entry point."""

from datetime import UTC

import pytest
from pypdf import PdfReader

from aisc_gr_statistics.app import (
    _salesforce_accounts_if_configured,
    load_local_environment,
    main,
)
from aisc_gr_statistics.salesforce import SalesforceError


def test_main_logs_greeting(capfd):
    """Confirm the application writes its greeting to stderr."""
    main()
    captured = capfd.readouterr()
    assert "Hello from aisc_gr_statistics!" in captured.err


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


def test_enrich_districts_writes_outputs_and_exits_nonzero_after_census_outage(
    tmp_path, monkeypatch
):
    """Partial enrichment remains inspectable even when Census is unavailable."""
    districts = tmp_path / "districts.csv"
    review = tmp_path / "review.csv"
    conversions = tmp_path / "address-conversions.csv"

    monkeypatch.setattr(
        "aisc_gr_statistics.app.enrich_companies",
        lambda *args: ([], [], [], True),
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
            ]
        )

    assert districts.read_text(encoding="utf-8").startswith("company_name")
    assert review.read_text(encoding="utf-8").startswith("company_name")
    assert conversions.read_text(encoding="utf-8").startswith("company_name")


def test_aggregate_districts_writes_a_csv(tmp_path, monkeypatch):
    from aisc_gr_statistics.districts import DistrictAggregateRow

    output = tmp_path / "aggregates.csv"
    monkeypatch.setattr(
        "aisc_gr_statistics.app.aggregate_districts",
        lambda *args: [DistrictAggregateRow("national", "", "", "", "", 1, 10, 1, 0)],
    )
    main(
        [
            "aggregate-districts", "--imis-csv", "members.csv", "--districts-csv",
            "districts.csv", "--aggregates-csv", str(output),
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
