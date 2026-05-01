"""Shared httpx client. Reads ~/.config/ticket-hub/config.toml."""

import os
from pathlib import Path

import httpx

try:
    import tomllib  # py>=3.11
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


def _config_path() -> Path:
    return Path(
        os.environ.get("TICKET_HUB_CONFIG")
        or Path.home() / ".config" / "ticket-hub" / "config.toml"
    )


def load_config() -> dict:
    p = _config_path()
    if not p.exists():
        return {}
    return tomllib.loads(p.read_text(encoding="utf-8"))


def get_client() -> httpx.Client:
    cfg = load_config()
    base = os.environ.get("TICKET_HUB_BASE_URL") or cfg.get("base_url", "http://localhost:8080")
    token = os.environ.get("TICKET_HUB_TOKEN") or cfg.get("token", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=base, headers=headers, timeout=30.0)
