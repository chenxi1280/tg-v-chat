#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/docker-env.sh"

ensure_runtime_env

check_container() {
  local container_name="$1"
  local status health
  status="$(docker inspect "$container_name" --format '{{.State.Status}}' 2>/dev/null || true)"
  health="$(docker inspect "$container_name" --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>/dev/null || true)"

  if [[ "$status" != "running" || ( -n "$health" && "$health" != "healthy" ) ]]; then
    echo "BAD ${container_name}: status=${status:-missing} health=${health:-none}" >&2
    docker logs --tail 200 "$container_name" >&2 || true
    exit 1
  fi

  echo "OK ${container_name}: status=$status health=${health:-none}"
}

check_container tg-v-chat-bot
check_container tg-v-chat-listener
check_container tg-v-chat-worker

docker exec tg-v-chat-bot python -m tg_v_chat.healthcheck

echo "Post-deploy checks passed"
