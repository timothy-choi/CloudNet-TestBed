#!/usr/bin/env bash
set -euo pipefail

API_BASE_URL="${CLOUDNET_API_BASE_URL:-http://127.0.0.1:8010}"
TOPOLOGY_NAME="demo-aws-control-plane-$(date +%s)"
VPC_ID=""

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
  local body="${2:-{}}"
  curl -fsS \
    -X POST "${API_BASE_URL}${path}" \
    -H "Content-Type: application/json" \
    --data "${body}"
}

api_delete() {
  local path="$1"
  curl -fsS -X DELETE "${API_BASE_URL}${path}"
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

wait_for_validation_status() {
  local topology_id="$1"
  local expected="$2"
  local attempts="$3"
  local validation=""
  local actual=""

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

wait_for_aws_resources() {
  local topology_id="$1"
  local attempts=30
  local resources
  local vpc_count
  local subnet_count
  local instance_count

  for attempt in $(seq 1 "${attempts}"); do
    resources="$(api_get "/topologies/${topology_id}/resources")"
    vpc_count="$(jq '[.resources[] | select(.type == "aws_vpc")] | length' <<<"${resources}")"
    subnet_count="$(jq '[.resources[] | select(.type == "aws_subnet")] | length' <<<"${resources}")"
    instance_count="$(jq '[.resources[] | select(.type == "aws_instance")] | length' <<<"${resources}")"
    VPC_ID="$(jq -r '.resources[] | select(.type == "aws_vpc") | .openstack_id' <<<"${resources}" | head -n 1)"

    if [[ "${vpc_count}" -ge 1 && "${subnet_count}" -ge 2 && "${instance_count}" -ge 3 ]]; then
      echo "Resources ready: ${vpc_count} VPC, ${subnet_count} subnets, ${instance_count} instances"
      return 0
    fi

    echo "Waiting for AWS resources (${attempt}/${attempts})..."
    sleep 5
  done

  echo "Timed out waiting for deployed AWS resources" >&2
  exit 1
}

cleanup_if_requested() {
  if [[ "${CLOUDNET_DEMO_CLEANUP:-false}" != "true" ]]; then
    return 0
  fi
  if [[ -z "${VPC_ID}" || "${VPC_ID}" == "null" ]]; then
    echo "Cleanup requested, but no VPC ID was discovered" >&2
    return 1
  fi

  step "Cleaning up AWS VPC ${VPC_ID}"
  cleanup_response="$(api_delete "/provider/networks/${VPC_ID}")"
  echo "${cleanup_response}" | jq .
}

require_command curl
require_command jq

echo "CloudNet AWS control plane demo"
echo "API: ${API_BASE_URL}"
echo "Topology: ${TOPOLOGY_NAME}"

step "Checking API and provider"
api_get "/health" | jq .
api_get "/provider/health" | jq .

step "Creating secure three-tier topology"
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
    {"from": "frontend", "to": "backend", "subnet": "10.120.1.0/24"},
    {"from": "backend", "to": "db", "subnet": "10.120.2.0/24"}
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

step "Planning topology without deploying"
plan_response="$(api_get "/topologies/${topology_id}/plan")"
echo "${plan_response}" | jq '{topology_id, provider, plan: {vpc: .plan.vpc, subnets: .plan.subnets, instances: .plan.instances, firewall_rules: .plan.firewall_rules}, warnings}'

step "Deploying topology"
deploy_response="$(api_post "/topologies/${topology_id}/deploy")"
expect_status "${deploy_response}" "ACTIVE"
echo "${deploy_response}" | jq '{topology_id, status, warnings, resources}'
wait_for_aws_resources "${topology_id}"
echo "Discovered VPC ID: ${VPC_ID}"

step "Validating baseline connectivity (expected PASSED)"
baseline_validation="$(wait_for_validation_status "${topology_id}" "PASSED" 12)"
echo "${baseline_validation}" | jq .

step "Injecting node-down failure on backend"
failure_response="$(api_post "/topologies/${topology_id}/failures/node-down" '{"node":"backend"}')"
expect_status "${failure_response}" "SUCCESS"
echo "${failure_response}" | jq .

step "Validating after failure (expected FAILED)"
failed_validation="$(wait_for_validation_status "${topology_id}" "FAILED" 6)"
echo "${failed_validation}" | jq .

step "Reconciling desired state to actual state"
reconcile_response="$(api_post "/topologies/${topology_id}/reconcile")"
expect_status "${reconcile_response}" "RECONCILED"
echo "${reconcile_response}" | jq .

step "Validating after reconcile (expected PASSED)"
reconciled_validation="$(wait_for_validation_status "${topology_id}" "PASSED" 12)"
echo "${reconciled_validation}" | jq .

step "Demo summary"
cat <<SUMMARY
Topology ID: ${topology_id}
VPC ID: ${VPC_ID}
Plan: compiled without AWS changes
Deploy: ACTIVE
Baseline validation: PASSED
After backend node-down: FAILED
Reconcile: RECONCILED
After reconcile: PASSED
SUMMARY

cleanup_if_requested
