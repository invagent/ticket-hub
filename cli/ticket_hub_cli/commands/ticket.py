"""ticket commands — D1 will wire real CRUD."""

import typer

app = typer.Typer(help="Ticket operations")


@app.command("list")
def list_tickets() -> None:  # pragma: no cover - D1 impl
    typer.echo("D1 pending: GET /api/tickets")


@app.command("show")
def show_ticket(_ticket_id: str = typer.Argument(...)) -> None:  # pragma: no cover - D1 impl
    typer.echo("D1 pending: GET /api/tickets/{id}")
