#!/usr/bin/env bash
set -euo pipefail

API_BASE_URL="${CLOUDNET_API_BASE_URL:-http://127.0.0.1:8010}"
TOPOLOGY_NAME="demo-failure-recovery-$(date +%s)"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

api_post() {
  local path="$1"
  local body="{}"
  if [[ $# -ge 2 ]]; then
    body="$2"
  fi
  curl -sS \
    -X POST "${API_BASE_URL}${path}" \
    -H "Content-Type: application/json" \
    --data "${body}"
}

api_get() {
  local path="$1"
  curl -fsS "${API_BASE_URL}${path}"
}

step() {
  echo
  echo "==> $1"
}

expect_status() {
  local response="$1"
  local expected="$2"
  local actual
  actual="$(jq -r '.status // empty' <<<"${response}")"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "Expected status ${expected}, got ${actual}" >&2
    echo "${response}" | jq . >&2
    exit 1
  fi
}

ensure_no_api_error() {
  local response="$1"
  local context="$2"
  local detail
  detail="$(jq -r '.detail // empty' <<<"${response}" 2>/dev/null || true)"
  if [[ -n "${detail}" ]]; then
    echo "API error during ${context}:" >&2
    echo "${response}" | jq . >&2
    exit 1
  fi
}

wait_for_resources() {
  local topology_id="$1"
  local attempts=30

  for attempt in $(seq 1 "${attempts}"); do
    resources="$(api_get "/topologies/${topology_id}/resources")"
    server_count="$(jq '[.resources[] | select(.type == "nova_server" or .type == "provider_instance")] | length' <<<"${resources}")"
    network_count="$(jq '[.resources[] | select(.type == "neutron_network" or .type == "provider_network")] | length' <<<"${resources}")"
    subnet_count="$(jq '[.resources[] | select(.type == "neutron_subnet" or .type == "provider_subnet")] | length' <<<"${resources}")"

    if [[ "${server_count}" -ge 2 && "${network_count}" -ge 1 && "${subnet_count}" -ge 1 ]]; then
      echo "Resources ready: ${server_count} servers, ${network_count} network, ${subnet_count} subnet"
      return 0
    fi

    echo "Waiting for resources (${attempt}/${attempts})..."
    sleep 5
  done

  echo "Timed out waiting for deployed resources" >&2
  exit 1
}

wait_for_validation_status() {
  local topology_id="$1"
  local expected="$2"
  local attempts="$3"

  for attempt in $(seq 1 "${attempts}"); do
    validation="$(api_post "/topologies/${topology_id}/validate")"
    actual="$(jq -r '.status // empty' <<<"${validation}")"
    echo "Validation attempt ${attempt}/${attempts}: ${actual}" >&2
    if [[ "${actual}" == "${expected}" ]]; then
      echo "${validation}"
      return 0
    fi
    sleep 10
  done

  echo "Timed out waiting for validation status ${expected}" >&2
  echo "${validation}" | jq . >&2
  exit 1
}

require_command curl
require_command jq

echo "CloudNet failure recovery demo"
echo "API: ${API_BASE_URL}"

step "Creating topology"

tmp_topology="$(mktemp)"
cat > "${tmp_topology}" <<EOF
{
  "name": "${TOPOLOGY_NAME}",
  "nodes": [
    {"name": "client-a", "type": "host"},
    {"name": "client-b", "type": "host"}
  ],
  "links": [
    {"from": "client-a", "to": "client-b", "subnet": "10.50.1.0/24"}
  ]
}
EOF

create_response="$(curl -sS -X POST "${API_BASE_URL}/topologies" \
  -H "Content-Type: application/json" \
  --data-binary @"${tmp_topology}")"

rm -f "${tmp_topology}"

ensure_no_api_error "${create_response}" "topology creation"

topology_id="$(jq -r '.id // empty' <<<"${create_response}")"
if [[ -z "${topology_id}" || "${topology_id}" == "null" ]]; then
  echo "Failed to extract topology id" >&2
  echo "${create_response}" | jq . >&2
  exit 1
fi

echo "Created topology ${topology_id} (${TOPOLOGY_NAME})"

step "Deploying topology"
deploy_response="$(api_post "/topologies/${topology_id}/deploy")"
expect_status "${deploy_response}" "ACTIVE"
wait_for_resources "${topology_id}"

step "Validating baseline connectivity"
baseline_validation="$(wait_for_validation_status "${topology_id}" "PASSED" 6)"
echo "${baseline_validation}" | jq .

step "Injecting node-down failure on client-b"
failure_response="$(api_post "/topologies/${topology_id}/failures/node-down" '{"node":"client-b"}')"
expect_status "${failure_response}" "SUCCESS"
echo "${failure_response}" | jq .

step "Validating connectivity fails"
failed_validation="$(wait_for_validation_status "${topology_id}" "FAILED" 6)"
echo "${failed_validation}" | jq .

step "Recovering client-b"
recovery_response="$(api_post "/topologies/${topology_id}/recover/node" '{"node":"client-b"}')"
expect_status "${recovery_response}" "SUCCESS"
echo "${recovery_response}" | jq .

step "Validating recovered connectivity"
recovered_validation="$(wait_for_validation_status "${topology_id}" "PASSED" 12)"
echo "${recovered_validation}" | jq .

step "Demo summary"
cat <<SUMMARY
Topology ID: ${topology_id}
Topology name: ${TOPOLOGY_NAME}
Baseline validation: PASSED
After node-down: FAILED
After recovery: PASSED
SUMMARY
