#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_BASE_URL="${CLOUDNET_API_BASE_URL:-http://127.0.0.1:8010}"

echo "CloudNet scenario experiment (expects API at ${API_BASE_URL})"
echo "Recommended: CLOUDNET_PROVIDER=mock make dev"
echo ""
echo "This demo runs a full scenario and prints compact step lines plus overall status and total time."
echo ""

exec python3 "${ROOT}/cli/cloudnet.py" run "${ROOT}/examples/backend-failure.yaml"
