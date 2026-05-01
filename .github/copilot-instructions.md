# Copilot instructions for this repository

## Build, test, and lint commands

- **Build:** No build step is defined (single-file Python script, no packaging config).
- **Tests:** No automated test suite is configured in this repository.
- **Lint:** No linter configuration is checked in.

Operational commands that are used in practice:

```bash
# Show CLI options
python3 auto_dns.py --help

# Safe execution path (no DNS write)
python3 auto_dns.py --token "$CF_API_TOKEN" --zone "$CF_ZONE" --record "$CF_RECORD" --dry-run
```

There is currently no single-test command because no test framework/files are present.

## High-level architecture

- The project is centered on `auto_dns.py` and uses only Python standard library modules (`argparse`, `urllib`, `ipaddress`, `json`).
- Runtime flow in `main()`:
  1. Load `.env` values (`load_env_file`) without overwriting already-exported environment variables.
  2. Parse CLI args (`build_parser`) where flag values override environment defaults.
  3. Resolve target IP (`get_public_ip`) using record type (`A` -> IPv4 services, `AAAA` -> IPv6 services) with fallback endpoints.
  4. Resolve zone (`get_zone_id`) unless `--zone-id` / `CF_ZONE_ID` is provided.
  5. Read record (`find_dns_record`) then upsert (`upsert_dns_record`) via Cloudflare API (`GET/POST/PATCH`).
- Cloudflare API calls are centralized in `request_json()`, with uniform error propagation through `CloudflareError`.
- Deployment examples live under `systemd/` and rely on an environment file (`EnvironmentFile=/etc/auto-dns.env`) plus running `auto_dns.py` as a oneshot service.

## Key conventions in this codebase

- **Configuration precedence is intentional:** CLI flags > pre-existing environment variables > `.env` file values.
- **Boolean UX pattern:** `argparse.BooleanOptionalAction` is used for paired flags (`--proxied/--no-proxied`, `--create/--no-create`), with env parsing handled by `parse_bool`.
- **Error surface convention:** operational failures raise `CloudflareError` and are reported as `error: ...` on stderr; avoid introducing silent returns.
- **Idempotent update behavior:** when existing record `content` and `proxied` already match, return `unchanged` and skip API writes.
- **Result strings are part of CLI contract:** `created:`, `updated:`, `unchanged:`, and `dry-run:` messages are used as direct command output.
