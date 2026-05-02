#!/usr/bin/env bash
set -euo pipefail

log_dir="${LOG_DIR:-/logs}"
log_file="${LOG_FILE:-${log_dir}/auto-dns.log}"
mkdir -p "$log_dir"

{
  printf '[%s] start\n' "$(date -Iseconds)"

: "${CF_API_TOKEN:?CF_API_TOKEN is required}"
: "${CF_ZONE:?CF_ZONE is required}"
: "${CF_RECORD:?CF_RECORD is required}"

record_type="${CF_RECORD_TYPE:-A}"
timeout="${IP_CHECK_TIMEOUT:-15}"

if [[ -n "${GATEWAY:-}" ]]; then
  : "${GATEWAY_INTERFACE:?GATEWAY_INTERFACE is required when GATEWAY is set}"

  ip_args=(
    --gateway "$GATEWAY"
    --interface "$GATEWAY_INTERFACE"
    --type "$record_type"
    --timeout "$timeout"
  )

  if [[ -n "${IP_CHECK_URL:-}" ]]; then
    ip_args+=(--url "$IP_CHECK_URL")
  fi

  if [[ -n "${ROUTE_MARK:-}" ]]; then
    ip_args+=(--mark "$ROUTE_MARK")
  fi

  if [[ -n "${ROUTE_TABLE:-}" ]]; then
    ip_args+=(--table "$ROUTE_TABLE")
  fi

  if [[ -n "${ROUTE_PRIORITY:-}" ]]; then
    ip_args+=(--priority "$ROUTE_PRIORITY")
  fi

  if [[ "${ROUTE_ONLINK:-true}" == "false" ]]; then
    ip_args+=(--no-onlink)
  fi

  current_ip="$(python3 /app/get_ip_via_policy_routing.py "${ip_args[@]}")"
  python3 /app/auto_dns.py --ip "$current_ip"
else
  python3 /app/auto_dns.py
fi

  printf '[%s] done\n' "$(date -Iseconds)"
} >> "$log_file" 2>&1
