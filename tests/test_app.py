"""Tests for the application entry point."""

from aisc_gr_statistics.app import (
    _salesforce_accounts_if_configured,
    load_local_environment,
    main,
)


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
    output = tmp_path / "report.pdf"
    conflicts = tmp_path / "conflicts.csv"
    candidates = tmp_path / "candidates.csv"

    main(
        [
            "report",
            "--imis-csv",
            "tests/fixtures/imis-membership-sample.csv",
            "--output",
            str(output),
            "--conflicts-csv",
            str(conflicts),
            "--candidate-matches-csv",
            str(candidates),
        ]
    )

    assert output.read_bytes().startswith(b"%PDF")
    assert conflicts.read_text(encoding="utf-8").startswith("shared iMIS ID")
    assert candidates.read_text(encoding="utf-8").startswith("iMIS ID")
    assert _salesforce_accounts_if_configured({"SF_CLIENT_ID": "id"}) == []
