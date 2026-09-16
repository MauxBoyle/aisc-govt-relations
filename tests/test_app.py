"""Tests for the application entry point."""

from aisc_gr_statistics.app import _salesforce_accounts_if_configured, main


def test_main_logs_greeting(capfd):
    """Confirm the application writes its greeting to stderr."""
    main()
    captured = capfd.readouterr()
    assert "Hello from aisc_gr_statistics!" in captured.err


def test_report_command_creates_a_pdf_without_salesforce_credentials(tmp_path):
    """Keep the local CSV-only workflow usable without Salesforce access."""
    output = tmp_path / "report.pdf"

    main(
        [
            "report",
            "--imis-csv",
            "tests/fixtures/imis-membership-sample.csv",
            "--output",
            str(output),
        ]
    )

    assert output.read_bytes().startswith(b"%PDF")
    assert _salesforce_accounts_if_configured({"SF_CLIENT_ID": "id"}) == []
