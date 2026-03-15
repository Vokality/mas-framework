"""Command-line entrypoint for the ops-plane API."""

from __future__ import annotations

import asyncio
import argparse
import json
import os
from pathlib import Path

import uvicorn

from mas_ops_api.app import create_app
from mas_ops_api.bootstrap import AdminBootstrapConfig, ensure_admin_user
from mas_ops_api.db.migrations import upgrade_to_head
from mas_ops_api.settings import OpsApiSettings


def main() -> None:
    """Serve the API or export its OpenAPI schema."""

    parser = argparse.ArgumentParser(prog="mas-ops-api")
    subparsers = parser.add_subparsers(dest="command", required=False)

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8080)

    openapi_parser = subparsers.add_parser("openapi")
    openapi_parser.add_argument("output", type=Path)

    subparsers.add_parser("migrate")

    bootstrap_parser = subparsers.add_parser("bootstrap-admin")
    bootstrap_parser.add_argument(
        "--admin-email",
        default=os.environ.get("MAS_OPS_BOOTSTRAP_ADMIN_EMAIL", "admin@example.com"),
    )
    bootstrap_parser.add_argument(
        "--admin-password",
        default=os.environ.get("MAS_OPS_BOOTSTRAP_ADMIN_PASSWORD", "admin123"),
    )
    bootstrap_parser.add_argument(
        "--admin-display-name",
        default=os.environ.get("MAS_OPS_BOOTSTRAP_ADMIN_DISPLAY_NAME", "Local Admin"),
    )

    args = parser.parse_args()
    command = args.command or "serve"

    if command == "openapi":
        app = create_app(OpsApiSettings())
        args.output.write_text(
            json.dumps(app.openapi(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return

    if command == "migrate":
        upgrade_to_head(OpsApiSettings())
        return

    if command == "bootstrap-admin":
        config = AdminBootstrapConfig(
            email=args.admin_email,
            password=args.admin_password,
            display_name=args.admin_display_name,
        )
        asyncio.run(ensure_admin_user(OpsApiSettings(), config=config))
        return

    uvicorn.run(
        "mas_ops_api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
