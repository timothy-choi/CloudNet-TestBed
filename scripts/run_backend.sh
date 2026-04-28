#!/usr/bin/env sh
set -eu

PORT="${1:-8010}"

cd backend
uvicorn app.main:app --reload --port "$PORT"
