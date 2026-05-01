"""CLI smoke tests."""

from typer.testing import CliRunner

from ticket_hub_cli.main import app

runner = CliRunner()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "admin" in result.stdout
    assert "ticket" in result.stdout


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "ticket-hub-cli" in result.stdout
