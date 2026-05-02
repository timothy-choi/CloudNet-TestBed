#!/usr/bin/env bash
set -euo pipefail

API_BASE_URL="${CLOUDNET_API_BASE_URL:-http://127.0.0.1:8010}"
TOPOLOGY_NAME="demo-mock-control-plane-$(date +%s)"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

step() {
  echo
  echo "==> $1"
}

api_get() {
  local path="$1"
  curl -fsS "${API_BASE_URL}${path}"
}

api_post() {
  local path="$1"
  local body="{}"
  if [[ $# -ge 2 ]]; then
    body="$2"
  fi
  curl -fsS \
    -X POST "${API_BASE_URL}${path}" \
    -H "Content-Type: application/json" \
    --data "${body}"
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

require_command curl
require_command jq

echo "CloudNet mock reliability experiment"
echo "API: ${API_BASE_URL}"
echo "Topology: ${TOPOLOGY_NAME}"

step "Checking API"
api_get "/health" | jq .
api_get "/provider/health" | jq .

step "Creating topology"
tmp_topology="$(mktemp)"
cat > "${tmp_topology}" <<EOF
{
  "name": "${TOPOLOGY_NAME}",
  "nodes": [
    {"name": "frontend", "type": "host"},
    {"name": "backend", "type": "host"},
    {"name": "db", "type": "host"}
  ],
  "links": [
    {"from": "frontend", "to": "backend", "subnet": "10.130.1.0/24"},
    {"from": "backend", "to": "db", "subnet": "10.130.2.0/24"}
  ],
  "firewall_rules": [
    {
      "name": "allow-frontend-backend-ping",
      "protocol": "icmp",
      "from": "frontend",
      "to": "backend"
    },
    {
      "name": "allow-backend-db-ping",
      "protocol": "icmp",
      "from": "backend",
      "to": "db"
    }
  ]
}
EOF

create_response="$(curl -fsS -X POST "${API_BASE_URL}/topologies" \
  -H "Content-Type: application/json" \
  --data-binary @"${tmp_topology}")"
rm -f "${tmp_topology}"

topology_id="$(jq -r '.id // empty' <<<"${create_response}")"
if [[ -z "${topology_id}" || "${topology_id}" == "null" ]]; then
  echo "Failed to extract topology ID" >&2
  echo "${create_response}" | jq . >&2
  exit 1
fi
echo "Created topology ID: ${topology_id}"

step "Planning"
api_get "/topologies/${topology_id}/plan" | jq '{topology_id, provider, plan}'

step "Deploying"
deploy_response="$(api_post "/topologies/${topology_id}/deploy")"
expect_status "${deploy_response}" "ACTIVE"
echo "${deploy_response}" | jq .

step "Validating baseline connectivity"
validation_response="$(api_post "/topologies/${topology_id}/validate")"
expect_status "${validation_response}" "PASSED"
echo "${validation_response}" | jq .

step "Injecting backend node-down"
failure_response="$(api_post "/topologies/${topology_id}/failures/node-down" '{"node":"backend"}')"
expect_status "${failure_response}" "SUCCESS"
echo "${failure_response}" | jq .

step "Validating drifted connectivity"
failed_validation="$(api_post "/topologies/${topology_id}/validate")"
expect_status "${failed_validation}" "FAILED"
echo "${failed_validation}" | jq .

step "Detecting drift"
drift_response="$(api_get "/topologies/${topology_id}/drift")"
echo "${drift_response}" | jq .
drift_detected="$(jq -r '.drift_detected' <<<"${drift_response}")"
if [[ "${drift_detected}" != "true" ]]; then
  echo "Expected drift_detected=true" >&2
  exit 1
fi

step "Reconciling"
reconcile_response="$(api_post "/topologies/${topology_id}/reconcile")"
expect_status "${reconcile_response}" "RECONCILED"
echo "${reconcile_response}" | jq .

step "Validating after reconcile"
reconciled_validation="$(api_post "/topologies/${topology_id}/validate")"
expect_status "${reconciled_validation}" "PASSED"
echo "${reconciled_validation}" | jq .

step "Event timeline"
events_response="$(api_get "/topologies/${topology_id}/events")"
echo "${events_response}" | jq '.events[] | {timestamp, type, status, message, metadata}'

timeline="$(jq -r '
  .events
  | map(
      if .type == "PLAN" and .status == "SUCCESS" then "PLAN"
      elif .type == "DEPLOY_COMPLETE" and .status == "SUCCESS" then "DEPLOY"
      elif .type == "VALIDATION" and .status == "SUCCESS" then "VALIDATE(PASS)"
      elif .type == "VALIDATION" and .status == "FAILED" then "VALIDATE(FAIL)"
      elif .type == "FAILURE_INJECTED" then "FAILURE"
      elif .type == "DRIFT_DETECTED" then "DRIFT"
      elif .type == "RECONCILE" and .message == "Reconcile complete" then "RECONCILE"
      else empty end
    )
  | join(" -> ")
' <<<"${events_response}")"
echo "Timeline: ${timeline}"

step "Demo summary"
cat <<SUMMARY
Experiment: mock failure and recovery
Topology ID: ${topology_id}
Baseline validation: PASSED
After node-down: FAILED
Drift detected: true
Reconcile: RECONCILED
After reconcile: PASSED
SUMMARY
