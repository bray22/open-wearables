#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT_DIR"

compose() {
    docker compose -f docker-compose.yml -f docker-compose.elevate.yml "$@"
}

cleanup() {
    compose down
    supabase stop
}

trap cleanup EXIT INT TERM

supabase start
compose up --build
