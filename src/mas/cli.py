"""MAS command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import yaml

from .gateway.config import GatewaySettings
from .redis_client import create_redis_client
from .runner import main as runner_main


@dataclass(frozen=True, slots=True)
class _AuditTailArgs:
    """Arguments for audit tail command."""

    config_file: str | None
    redis_url: str | None
    stream: str
    last: int
    block_ms: int
    json: bool


def main(argv: list[str] | None = None) -> None:
    """Entry point for the MAS CLI."""

    if argv is None:
        argv = sys.argv[1:]

    # Preserve existing behavior: `python -m mas` runs the runner.
    if not argv:
        asyncio.run(runner_main())
        return

    parser = _build_parser()
    args = parser.parse_args(argv)

    command = getattr(args, "command", None)
    if command == "run":
        config_file = getattr(args, "config_file", None)
        asyncio.run(runner_main(config_file=config_file))
        return

    if command == "audit":
        audit_command = getattr(args, "audit_command", None)
        if audit_command == "tail":
            tail_args = _AuditTailArgs(
                config_file=getattr(args, "config_file", None),
                redis_url=getattr(args, "redis_url", None),
                stream=str(getattr(args, "stream")),
                last=int(getattr(args, "last")),
                block_ms=int(getattr(args, "block_ms")),
                json=bool(getattr(args, "json")),
            )
            try:
                asyncio.run(_audit_tail(tail_args))
            except KeyboardInterrupt:
                return
            return

    parser.print_help()
    raise SystemExit(2)


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(prog="mas", description="MAS Framework CLI")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run agents defined in mas.yaml")
    run_p.add_argument(
        "--config-file",
        help="Path to mas.yaml (defaults to searching current/parent directories)",
    )

    audit_p = sub.add_parser("audit", help="Audit log commands")
    audit_sub = audit_p.add_subparsers(dest="audit_command")

    tail_p = audit_sub.add_parser("tail", help="Tail the audit log (Redis Stream)")
    tail_p.add_argument(
        "--stream",
        default="audit:messages",
        help="Redis Stream name (default: audit:messages)",
    )
    tail_p.add_argument(
        "--last",
        type=int,
        default=0,
        help="Print the last N entries before following (default: 0)",
    )
    tail_p.add_argument(
        "--block-ms",
        type=int,
        default=1000,
        help="XREAD block timeout in ms (0 = block forever)",
    )
    tail_p.add_argument(
        "--json",
        action="store_true",
        help="Emit newline-delimited JSON instead of text",
    )
    tail_p.add_argument(
        "--redis-url",
        help="Override Redis URL (otherwise read from mas.yaml gateway.redis.url or env)",
    )
    tail_p.add_argument(
        "--config-file",
        help="Path to mas.yaml (defaults to searching current/parent directories)",
    )

    return parser


async def _audit_tail(args: _AuditTailArgs) -> None:
    """Stream audit entries from Redis."""
    redis_url = _resolve_redis_url(
        redis_url=args.redis_url, config_file=args.config_file
    )
    redis = create_redis_client(url=redis_url, decode_responses=True)

    start_id = "$"
    try:
        if args.last > 0:
            entries = await redis.xrevrange(args.stream, "+", "-", count=args.last)
            entries = list(reversed(entries))
            for stream_id, fields in entries:
                _print_audit_entry(stream_id, fields, json_output=args.json)
            if entries:
                start_id = entries[-1][0]

        while True:
            resp = await redis.xread(
                {args.stream: start_id},
                block=args.block_ms,
            )
            if not resp:
                continue

            for _stream_name, messages in resp:
                for stream_id, fields in messages:
                    _print_audit_entry(stream_id, fields, json_output=args.json)
                    start_id = stream_id
    finally:
        await redis.aclose()


def _resolve_redis_url(*, redis_url: str | None, config_file: str | None) -> str:
    """Resolve Redis URL from args, config, or defaults."""
    if redis_url:
        return redis_url

    config_path = config_file
    if not config_path:
        start = Path.cwd()
        for current in (start, *start.parents):
            candidate = current / "mas.yaml"
            if candidate.exists():
                config_path = str(candidate)
                break

    if config_path:
        path = Path(config_path)
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                gateway_raw = loaded.get("gateway")
                if isinstance(gateway_raw, dict):
                    redis_raw = gateway_raw.get("redis")
                    if isinstance(redis_raw, dict):
                        url = redis_raw.get("url")
                        if isinstance(url, str) and url:
                            return url

    # Fall back to gateway settings (env + defaults).
    return GatewaySettings().redis.url


def _print_audit_entry(
    stream_id: str, fields: Mapping[str, str], *, json_output: bool
) -> None:
    """Print a single audit entry in the selected format."""
    if json_output:
        record: dict[str, object] = {"stream_id": stream_id, **dict(fields)}
        record["timestamp"] = _parse_float(record.get("timestamp"))
        record["latency_ms"] = _parse_float(record.get("latency_ms"))
        record["violations"] = _parse_json_list(record.get("violations"))
        print(json.dumps(record, sort_keys=True), flush=True)
        return

    print(_format_audit_line(stream_id, fields), flush=True)


def _format_audit_line(stream_id: str, fields: Mapping[str, str]) -> str:
    """Format a single audit entry line."""
    ts = _parse_float(fields.get("timestamp"))
    ts_iso = "-"
    if isinstance(ts, float):
        ts_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(
            timespec="seconds"
        )

    decision = fields.get("decision") or "-"

    sender_id = fields.get("sender_id") or "-"
    sender_instance_id = fields.get("sender_instance_id")
    sender = f"{sender_id}[{sender_instance_id}]" if sender_instance_id else sender_id

    target_id = fields.get("target_id") or "-"
    message_type = fields.get("message_type") or "-"

    latency_ms = fields.get("latency_ms") or "-"
    message_id = fields.get("message_id") or "-"
    correlation_id = fields.get("correlation_id") or ""

    violations = _parse_json_list(fields.get("violations"))
    violations_str = ""
    if violations:
        violations_str = " violations=" + ",".join(violations)

    corr_str = f" corr={correlation_id}" if correlation_id else ""
    return (
        f"{ts_iso} {decision} {sender} -> {target_id} "
        f"type={message_type} latency_ms={latency_ms} id={message_id}{corr_str}"
        f" stream_id={stream_id}{violations_str}"
    )


def _parse_float(value: object) -> float | None:
    """Parse a float from a value, returning None on failure."""
    if value is None:
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _parse_json_list(value: object) -> list[str]:
    """Parse a JSON-encoded list of strings."""
    if value is None:
        return []

    raw = str(value)
    if not raw:
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []

    if not isinstance(parsed, list):
        return []

    return [str(v) for v in parsed]
