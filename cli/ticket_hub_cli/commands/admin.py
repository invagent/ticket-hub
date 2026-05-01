"""admin commands — D0: list-only."""

import json

import typer

from ticket_hub_cli.client import get_client

app = typer.Typer(help="Admin operations")


@app.command("sources")
def list_sources() -> None:
    """List registered sources."""
    with get_client() as client:
        resp = client.get("/api/admin/sources")
        resp.raise_for_status()
        typer.echo(json.dumps(resp.json(), indent=2, ensure_ascii=False))


@app.command("users")
def list_users() -> None:
    """List active users."""
    with get_client() as client:
        resp = client.get("/api/admin/users")
        resp.raise_for_status()
        typer.echo(json.dumps(resp.json(), indent=2, ensure_ascii=False))
