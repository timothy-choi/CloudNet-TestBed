#!/usr/bin/env bash
set -euo pipefail

API_BASE_URL="${CLOUDNET_API_BASE_URL:-http://127.0.0.1:8010}"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

get_json() {
  local path="$1"
  curl -fsS "${API_BASE_URL}${path}"
}

require_command curl
require_command jq

echo "Checking CloudNet API at ${API_BASE_URL}"

health="$(get_json "/health")"
status="$(jq -r '.status // empty' <<<"${health}")"
if [[ "${status}" != "ok" ]]; then
  echo "API health check failed: ${health}" >&2
  exit 1
fi
echo "API health: ok"

openstack_health="$(get_json "/openstack/health")"
connected="$(jq -r '.connected // false' <<<"${openstack_health}")"
if [[ "${connected}" != "true" ]]; then
  echo "OpenStack health check failed: ${openstack_health}" >&2
  exit 1
fi
echo "OpenStack health: connected"

echo "CloudNet API is ready"
