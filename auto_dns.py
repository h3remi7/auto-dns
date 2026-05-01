#!/usr/bin/env python3
"""
Update a Cloudflare DNS A/AAAA record to the current public IP address.

This script uses only the Python standard library. Configure it with command
line flags or environment variables; command line flags take precedence.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


CF_API_BASE = "https://api.cloudflare.com/client/v4"
IPV4_SERVICES = (
    "https://ip.me",
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://checkip.amazonaws.com",
)
IPV6_SERVICES = (
    "https://ip.me",
    "https://api6.ipify.org",
    "https://ifconfig.co/ip",
)


class CloudflareError(RuntimeError):
    pass


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as env_file:
        for line_number, raw_line in enumerate(env_file, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise CloudflareError(f"Invalid {path}:{line_number}; expected KEY=value")

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def request_json(
    method: str,
    url: str,
    token: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise CloudflareError(f"Cloudflare API HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise CloudflareError(f"Network error: {exc.reason}") from exc

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise CloudflareError(f"Invalid JSON response: {body}") from exc

    if not parsed.get("success", False):
        errors = parsed.get("errors") or []
        messages = "; ".join(
            f"{item.get('code', 'unknown')}: {item.get('message', item)}"
            for item in errors
            if isinstance(item, dict)
        )
        raise CloudflareError(messages or f"Cloudflare API request failed: {parsed}")

    return parsed


def api_url(path: str, query: dict[str, str] | None = None) -> str:
    url = f"{CF_API_BASE}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    return url


def get_public_ip(record_type: str, explicit_ip: str | None) -> str:
    if explicit_ip:
        validate_ip(record_type, explicit_ip)
        return explicit_ip

    services = IPV6_SERVICES if record_type == "AAAA" else IPV4_SERVICES
    errors: list[str] = []

    for service in services:
        try:
            with urllib.request.urlopen(service, timeout=15) as response:
                ip = response.read().decode("utf-8").strip()
            validate_ip(record_type, ip)
            return ip
        except Exception as exc:  # noqa: BLE001 - keep trying fallback services.
            errors.append(f"{service}: {exc}")

    raise CloudflareError("Could not detect public IP. Tried: " + " | ".join(errors))


def validate_ip(record_type: str, value: str) -> None:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError as exc:
        raise CloudflareError(f"Invalid IP address: {value}") from exc

    if record_type == "A" and ip.version != 4:
        raise CloudflareError(f"A records require an IPv4 address, got: {value}")
    if record_type == "AAAA" and ip.version != 6:
        raise CloudflareError(f"AAAA records require an IPv6 address, got: {value}")


def get_zone_id(token: str, zone_name: str, zone_id: str | None) -> str:
    if zone_id:
        return zone_id

    response = request_json(
        "GET",
        api_url("/zones", {"name": zone_name, "status": "active", "per_page": "1"}),
        token,
    )
    zones = response.get("result") or []
    if not zones:
        raise CloudflareError(f"No active Cloudflare zone found for {zone_name}")

    return zones[0]["id"]


def find_dns_record(
    token: str,
    zone_id: str,
    record_name: str,
    record_type: str,
) -> dict[str, Any] | None:
    response = request_json(
        "GET",
        api_url(
            f"/zones/{zone_id}/dns_records",
            {
                "name": record_name,
                "type": record_type,
                "per_page": "1",
                "match": "all",
            },
        ),
        token,
    )
    records = response.get("result") or []
    return records[0] if records else None


def upsert_dns_record(
    token: str,
    zone_id: str,
    record_name: str,
    record_type: str,
    content: str,
    ttl: int,
    proxied: bool,
    create: bool,
    dry_run: bool,
) -> str:
    record = find_dns_record(token, zone_id, record_name, record_type)

    if record and record.get("content") == content and record.get("proxied") == proxied:
        return f"unchanged: {record_type} {record_name} already points to {content}"

    payload = {
        "type": record_type,
        "name": record_name,
        "content": content,
        "ttl": ttl,
        "proxied": proxied,
    }

    if record:
        if dry_run:
            return (
                f"dry-run: would update {record_type} {record_name} "
                f"from {record.get('content')} to {content}"
            )
        request_json(
            "PATCH",
            api_url(f"/zones/{zone_id}/dns_records/{record['id']}"),
            token,
            payload,
        )
        return f"updated: {record_type} {record_name} -> {content}"

    if not create:
        raise CloudflareError(
            f"{record_type} record {record_name} does not exist. "
            "Use --create or set CF_CREATE=true to create it."
        )

    if dry_run:
        return f"dry-run: would create {record_type} {record_name} -> {content}"

    request_json("POST", api_url(f"/zones/{zone_id}/dns_records"), token, payload)
    return f"created: {record_type} {record_name} -> {content}"


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Automatically update a Cloudflare DNS A/AAAA record.",
    )
    parser.add_argument("--token", default=env("CF_API_TOKEN"), help="Cloudflare API token")
    parser.add_argument("--zone", default=env("CF_ZONE"), help="Cloudflare zone name, e.g. example.com")
    parser.add_argument("--zone-id", default=env("CF_ZONE_ID"), help="Cloudflare zone ID; skips zone lookup")
    parser.add_argument("--record", default=env("CF_RECORD"), help="Full record name, e.g. home.example.com")
    parser.add_argument(
        "--type",
        default=env("CF_RECORD_TYPE", "A"),
        choices=("A", "AAAA"),
        help="DNS record type",
    )
    parser.add_argument("--ip", default=env("CF_IP"), help="IP address to set; auto-detected when omitted")
    parser.add_argument("--ttl", type=int, default=int(env("CF_TTL", "1") or "1"), help="TTL in seconds; 1 means auto")
    parser.add_argument(
        "--proxied",
        action=argparse.BooleanOptionalAction,
        default=parse_bool(env("CF_PROXIED"), False),
        help="Enable or disable Cloudflare proxying",
    )
    parser.add_argument(
        "--create",
        action=argparse.BooleanOptionalAction,
        default=parse_bool(env("CF_CREATE"), False),
        help="Create the record if it does not exist",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=parse_bool(env("CF_DRY_RUN"), False),
        help="Print the planned change without writing to Cloudflare",
    )
    return parser


def main() -> int:
    try:
        load_env_file()
        parser = build_parser()
        args = parser.parse_args()

        missing = [
            name
            for name, value in {
                "--token or CF_API_TOKEN": args.token,
                "--zone or CF_ZONE": args.zone,
                "--record or CF_RECORD": args.record,
            }.items()
            if not value
        ]
        if missing:
            parser.error("missing required configuration: " + ", ".join(missing))

        public_ip = get_public_ip(args.type, args.ip)
        zone_id = get_zone_id(args.token, args.zone, args.zone_id)
        result = upsert_dns_record(
            token=args.token,
            zone_id=zone_id,
            record_name=args.record,
            record_type=args.type,
            content=public_ip,
            ttl=args.ttl,
            proxied=args.proxied,
            create=args.create,
            dry_run=args.dry_run,
        )
    except CloudflareError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
