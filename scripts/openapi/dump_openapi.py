"""Dump the FastAPI OpenAPI schema to a JSON file with stable formatting.

Stability matters because the file is committed and the CI gate diffs it.
We sort keys + use 2-space indent + trailing newline so re-runs are
byte-stable across machines.

Usage:
    cd backend
    .venv/bin/python ../scripts/openapi/dump_openapi.py ../frontend/src/api/openapi.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the backend `app` importable when running from repo root or backend/.
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump FastAPI OpenAPI schema")
    parser.add_argument(
        "out",
        type=Path,
        nargs="?",
        default=BACKEND_DIR.parent / "frontend" / "src" / "api" / "openapi.json",
    )
    args = parser.parse_args()

    from app.main import app  # noqa: E402  (must be after sys.path.insert)

    schema = app.openapi()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.out} ({len(schema.get('paths', {}))} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
