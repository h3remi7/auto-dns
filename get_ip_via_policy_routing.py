#!/usr/bin/env python3
"""
Fetch the public IP through a dedicated policy-routing rule.

Unlike swapping the system default gateway, this script only affects the socket
created by this process: it marks the socket with SO_MARK, adds a temporary
policy-routing rule for that mark, sends the request through a dedicated route
table, then removes the temporary rule again.
"""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import os
import shlex
import socket
import ssl
import subprocess
import sys
import urllib.parse


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
DEFAULT_MARK = 0x66
DEFAULT_TABLE = 50001
DEFAULT_PRIORITY = 10000


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


def parse_number(value: str) -> int:
    try:
        parsed = int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid integer value: {value}") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"Value must be positive: {value}")
    return parsed


def validate_gateway(record_type: str, gateway: str) -> None:
    try:
        ip = ipaddress.ip_address(gateway)
    except ValueError as exc:
        raise RouteError(f"Invalid gateway IP: {gateway}") from exc

    if record_type == "A" and ip.version != 4:
        raise RouteError(f"A mode requires an IPv4 gateway, got: {gateway}")
    if record_type == "AAAA" and ip.version != 6:
        raise RouteError(f"AAAA mode requires an IPv6 gateway, got: {gateway}")


def ip_args(record_type: str, *args: str) -> tuple[str, ...]:
    family_flag = "-6" if record_type == "AAAA" else "-4"
    return ("ip", family_flag, *args)


def list_table_routes(record_type: str, table: int) -> list[str]:
    try:
        output = run_command(*ip_args(record_type, "route", "show", "table", str(table)))
    except RouteError as exc:
        if "FIB table does not exist" in str(exc):
            return []
        raise
    return [line.strip() for line in output.splitlines() if line.strip()]


def replace_default_route(
    record_type: str,
    table: int,
    gateway: str,
    interface: str,
    onlink: bool,
) -> None:
    route_args = [
        *ip_args(
            record_type,
            "route",
            "replace",
            "default",
            "via",
            gateway,
            "dev",
            interface,
            "table",
            str(table),
        )
    ]
    if onlink:
        route_args.append("onlink")

    run_command(
        *route_args
    )


def restore_table_routes(record_type: str, table: int, saved_routes: list[str]) -> None:
    current_routes = list_table_routes(record_type, table)
    if current_routes:
        run_command(*ip_args(record_type, "route", "flush", "table", str(table)))

    for route in saved_routes:
        run_command(
            *ip_args(
                record_type,
                "route",
                "add",
                *shlex.split(route),
                "table",
                str(table),
            )
        )


def add_rule(record_type: str, mark: int, table: int, priority: int) -> None:
    run_command(
        *ip_args(
            record_type,
            "rule",
            "add",
            "fwmark",
            hex(mark),
            "lookup",
            str(table),
            "priority",
            str(priority),
        )
    )


def delete_rule(record_type: str, mark: int, table: int, priority: int) -> None:
    run_command(
        *ip_args(
            record_type,
            "rule",
            "del",
            "fwmark",
            hex(mark),
            "lookup",
            str(table),
            "priority",
            str(priority),
        )
    )


def create_marked_connection(
    host: str,
    port: int,
    timeout: int,
    family: int,
    mark: int,
) -> socket.socket:
    if not hasattr(socket, "SO_MARK"):
        raise RouteError("Python socket.SO_MARK is not available on this system")

    last_error: OSError | None = None
    for result in socket.getaddrinfo(host, port, family, socket.SOCK_STREAM):
        addr_family, socktype, proto, _, sockaddr = result
        candidate = socket.socket(addr_family, socktype, proto)
        try:
            candidate.settimeout(timeout)
            candidate.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, mark)
            candidate.connect(sockaddr)
        except OSError as exc:
            candidate.close()
            last_error = exc
            continue
        return candidate

    raise RouteError(f"Could not connect to {host}:{port}: {last_error}")


class MarkedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args: object, mark: int, family: int, **kwargs: object) -> None:
        self._mark = mark
        self._family = family
        super().__init__(*args, **kwargs)

    def connect(self) -> None:
        if self.host is None or self.port is None:
            raise RouteError("Missing HTTP host or port")
        self.sock = create_marked_connection(
            self.host,
            self.port,
            int(self.timeout or 0),
            self._family,
            self._mark,
        )


class MarkedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args: object, mark: int, family: int, **kwargs: object) -> None:
        self._mark = mark
        self._family = family
        super().__init__(*args, **kwargs)

    def connect(self) -> None:
        if self.host is None or self.port is None:
            raise RouteError("Missing HTTPS host or port")

        raw_socket = create_marked_connection(
            self.host,
            self.port,
            int(self.timeout or 0),
            self._family,
            self._mark,
        )
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


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


def fetch_public_ip(url: str, timeout: int, record_type: str, mark: int) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RouteError(f"Unsupported URL: {url}")

    family = socket.AF_INET6 if record_type == "AAAA" else socket.AF_INET
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    connection_class: type[http.client.HTTPConnection]
    if parsed.scheme == "https":
        connection_class = MarkedHTTPSConnection
    else:
        connection_class = MarkedHTTPConnection

    connection = connection_class(
        parsed.hostname,
        port,
        timeout=timeout,
        mark=mark,
        family=family,
    )
    try:
        connection.request("GET", path, headers={"User-Agent": "auto-dns/get-ip"})
        response = connection.getresponse()
        body = response.read().decode("utf-8").strip()
    except OSError as exc:
        raise RouteError(f"Network error while fetching public IP: {exc}") from exc
    finally:
        connection.close()

    if response.status >= 400:
        raise RouteError(f"IP service returned HTTP {response.status}: {body}")
    return validate_ip(record_type, body)


def get_public_ip(url: str | None, timeout: int, record_type: str, mark: int) -> str:
    if url:
        return fetch_public_ip(url, timeout, record_type, mark)

    services = IPV6_SERVICES if record_type == "AAAA" else IPV4_SERVICES
    errors: list[str] = []

    for service in services:
        try:
            return fetch_public_ip(service, timeout, record_type, mark)
        except RouteError as exc:
            errors.append(f"{service}: {exc}")

    raise RouteError("Could not detect public IP. Tried: " + " | ".join(errors))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch public IP through a dedicated policy-routing rule.",
    )
    parser.add_argument("--gateway", required=True, help="Gateway IP for the dedicated route")
    parser.add_argument("--interface", required=True, help="Interface used for the dedicated route")
    parser.add_argument(
        "--type",
        default="A",
        choices=("A", "AAAA"),
        help="Return IPv4 (A) or IPv6 (AAAA), default: A",
    )
    parser.add_argument("--url", help="IP check endpoint; defaults match --type")
    parser.add_argument("--timeout", type=int, default=15, help="HTTP timeout in seconds")
    parser.add_argument("--mark", type=parse_number, default=DEFAULT_MARK, help="fwmark for the request socket")
    parser.add_argument("--table", type=parse_number, default=DEFAULT_TABLE, help="Temporary routing table number")
    parser.add_argument(
        "--onlink",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Add the temporary default route with onlink, default: enabled",
    )
    parser.add_argument(
        "--priority",
        type=parse_number,
        default=DEFAULT_PRIORITY,
        help="Priority for the temporary ip rule",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("error: this script must run as root because it uses ip rule and SO_MARK", file=sys.stderr)
        return 1

    saved_routes: list[str] = []
    route_installed = False
    rule_installed = False

    try:
        validate_gateway(args.type, args.gateway)
        saved_routes = list_table_routes(args.type, args.table)
        replace_default_route(args.type, args.table, args.gateway, args.interface, args.onlink)
        route_installed = True
        add_rule(args.type, args.mark, args.table, args.priority)
        rule_installed = True
    except RouteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if rule_installed:
            try:
                delete_rule(args.type, args.mark, args.table, args.priority)
            except RouteError as cleanup_exc:
                print(f"error: failed to remove temporary rule: {cleanup_exc}", file=sys.stderr)
        if route_installed:
            try:
                restore_table_routes(args.type, args.table, saved_routes)
            except RouteError as cleanup_exc:
                print(f"error: failed to restore routing table: {cleanup_exc}", file=sys.stderr)
        return 1

    public_ip: str | None = None
    request_error: RouteError | None = None
    cleanup_errors: list[str] = []

    try:
        public_ip = get_public_ip(args.url, args.timeout, args.type, args.mark)
    except RouteError as exc:
        request_error = exc

    try:
        delete_rule(args.type, args.mark, args.table, args.priority)
    except RouteError as exc:
        cleanup_errors.append(f"failed to remove temporary rule: {exc}")

    try:
        restore_table_routes(args.type, args.table, saved_routes)
    except RouteError as exc:
        cleanup_errors.append(f"failed to restore routing table: {exc}")

    if request_error:
        print(f"error: {request_error}", file=sys.stderr)
        for cleanup_error in cleanup_errors:
            print(f"error: {cleanup_error}", file=sys.stderr)
        return 1

    if cleanup_errors:
        for cleanup_error in cleanup_errors:
            print(f"error: fetched public IP but {cleanup_error}", file=sys.stderr)
        return 1

    if public_ip is None:
        print("error: public IP lookup returned no result", file=sys.stderr)
        return 1

    print(public_ip)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
