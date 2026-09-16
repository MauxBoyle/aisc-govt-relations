"""Tests for the application entry point."""

from aisc_gr_statistics.app import main


def test_main_logs_greeting(capfd):
    """Confirm the application writes its greeting to stderr."""
    main()
    captured = capfd.readouterr()
    assert "Hello from aisc_gr_statistics!" in captured.err
