"""ticket-hub CLI entry point."""

import typer

from ticket_hub_cli import __version__
from ticket_hub_cli.commands import admin, ticket

app = typer.Typer(no_args_is_help=True, add_completion=False, help="ticket-hub CLI")
app.add_typer(admin.app, name="admin")
app.add_typer(ticket.app, name="ticket")


@app.command()
def version() -> None:
    """Print CLI version."""
    typer.echo(f"ticket-hub-cli {__version__}")


if __name__ == "__main__":
    app()
