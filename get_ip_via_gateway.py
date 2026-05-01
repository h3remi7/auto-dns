#!/usr/bin/env python3
"""
Temporarily replace the default route, fetch the public IP, then restore routes.

This is useful on Linux hosts where the normal default gateway points to a
side-router, but public IP detection must go out through the main gateway.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request


DEFAULT_IPV4_URL = "https://ip.me"
DEFAULT_IPV6_URL = "https://ip.me"


class RouteError(RuntimeError):
    pass


def run_command(*args: str) -> str:
    try:
        completed = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RouteError(f"Command not found: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()
        stdout = exc.stdout.strip()
        message = stderr or stdout or "unknown error"
        raise RouteError(f"Command failed: {' '.join(args)}: {message}") from exc
    return completed.stdout.strip()


def list_default_routes() -> list[str]:
    output = run_command("ip", "route", "show", "default")
    routes = [line.strip() for line in output.splitlines() if line.strip()]
    if not routes:
        raise RouteError("No current default route found")
    return routes


def delete_route(route: str) -> None:
    run_command("ip", "route", "del", *shlex.split(route))


def add_route(route: str) -> None:
    run_command("ip", "route", "add", *shlex.split(route))


def replace_default_route(saved_routes: list[str], gateway: str, interface: str, metric: int) -> str:
    for route in saved_routes:
        delete_route(route)

    temporary_route = f"default via {gateway} dev {interface} metric {metric}"
    add_route(temporary_route)
    run_command("ip", "route", "flush", "cache")
    return temporary_route


def restore_default_routes(saved_routes: list[str], temporary_route: str) -> None:
    delete_route(temporary_route)
    for route in saved_routes:
        add_route(route)
    run_command("ip", "route", "flush", "cache")


def validate_ip(record_type: str, value: str) -> str:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError as exc:
        raise RouteError(f"Invalid IP address returned: {value}") from exc

    if record_type == "A" and ip.version != 4:
        raise RouteError(f"Expected IPv4 address, got: {value}")
    if record_type == "AAAA" and ip.version != 6:
        raise RouteError(f"Expected IPv6 address, got: {value}")
    return value


def fetch_public_ip(url: str, timeout: int, record_type: str) -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, headers={"User-Agent": "auto-dns/get-ip"})

    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read().decode("utf-8").strip()
    except urllib.error.URLError as exc:
        raise RouteError(f"Network error while fetching public IP: {exc.reason}") from exc

    return validate_ip(record_type, body)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch public IP after temporarily switching the default gateway.",
    )
    parser.add_argument("--gateway", required=True, help="Temporary gateway IP, e.g. 192.168.1.1")
    parser.add_argument("--interface", required=True, help="Interface for the temporary gateway, e.g. eth0")
    parser.add_argument(
        "--type",
        default="A",
        choices=("A", "AAAA"),
        help="Return IPv4 (A) or IPv6 (AAAA), default: A",
    )
    parser.add_argument("--url", help="IP check endpoint; defaults match --type")
    parser.add_argument("--timeout", type=int, default=15, help="HTTP timeout in seconds")
    parser.add_argument("--metric", type=int, default=5, help="Metric for the temporary default route")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("error: this script must run as root because it modifies routes", file=sys.stderr)
        return 1

    default_url = DEFAULT_IPV6_URL if args.type == "AAAA" else DEFAULT_IPV4_URL
    saved_routes = list_default_routes()
    temporary_route = replace_default_route(saved_routes, args.gateway, args.interface, args.metric)

    try:
        public_ip = fetch_public_ip(args.url or default_url, args.timeout, args.type)
    except RouteError as exc:
        try:
            restore_default_routes(saved_routes, temporary_route)
        except RouteError as restore_exc:
            print(f"error: {exc}", file=sys.stderr)
            print(f"error: failed to restore routes: {restore_exc}", file=sys.stderr)
            return 1
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        restore_default_routes(saved_routes, temporary_route)
    except RouteError as exc:
        print(public_ip)
        print(f"warning: public IP fetched but failed to restore routes: {exc}", file=sys.stderr)
        return 1

    print(public_ip)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
