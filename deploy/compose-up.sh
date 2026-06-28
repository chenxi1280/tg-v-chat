#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/docker-env.sh"

ensure_runtime_env

docker_login_ghcr() {
  if [[ "$TG_V_CHAT_IMAGE" != ghcr.io/* ]]; then
    return 0
  fi
  if [[ -z "${GHCR_USERNAME:-}" || -z "${GHCR_TOKEN:-}" ]]; then
    echo "GHCR_USERNAME and GHCR_TOKEN are required to pull GHCR images." >&2
    exit 1
  fi

  printf '%s\n' "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin >/dev/null
}

wait_for_container_ready() {
  local container_name="$1"
  local timeout_seconds="${2:-180}"
  local started_at
  started_at="$(date +%s)"

  while true; do
    local elapsed status health
    elapsed=$(($(date +%s) - started_at))
    status="$(docker inspect "$container_name" --format '{{.State.Status}}' 2>/dev/null || true)"
    health="$(docker inspect "$container_name" --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>/dev/null || true)"

    if [[ "$status" == "running" && ( -z "$health" || "$health" == "healthy" ) ]]; then
      echo "Container ready: ${container_name} status=$status health=${health:-none}"
      return 0
    fi
    if [[ "$status" == "exited" || "$status" == "dead" || "$health" == "unhealthy" ]]; then
      echo "Service failed: ${container_name} status=${status:-unknown} health=${health:-none}" >&2
      docker logs --tail 200 "$container_name" >&2 || true
      return 1
    fi
    if (( elapsed >= timeout_seconds )); then
      echo "Timed out waiting for ${container_name}: status=${status:-unknown} health=${health:-none}" >&2
      docker logs --tail 200 "$container_name" >&2 || true
      return 1
    fi

    sleep 5
  done
}

docker_login_ghcr

echo "==> Release directory: $APP_DIR"
echo "==> Compose file: $COMPOSE_FILE"
echo "==> Env file: $ENV_FILE"
echo "==> Pulling image"
compose pull bot listener worker

echo "==> Ensuring PostgreSQL database exists"
compose run --rm migrate python -m tg_v_chat.storage.ensure_database

echo "==> Running migrations"
compose run --rm migrate

echo "==> Starting services"
compose up -d --no-build --remove-orphans bot listener worker
wait_for_container_ready tg-v-chat-bot "${TG_V_CHAT_READY_TIMEOUT_SECONDS:-180}"
wait_for_container_ready tg-v-chat-listener "${TG_V_CHAT_READY_TIMEOUT_SECONDS:-180}"
wait_for_container_ready tg-v-chat-worker "${TG_V_CHAT_READY_TIMEOUT_SECONDS:-180}"

echo "==> Container status"
compose ps
