#!/usr/bin/env bash

set -euo pipefail

BASE_DIR="${BASE_DIR:-/data/tg-v-chat}"
CURRENT_APP_DIR="${BASE_DIR}/current"
SHARED_DIR="${SHARED_DIR:-${BASE_DIR}/shared}"
APP_DIR="${APP_DIR:-$CURRENT_APP_DIR}"
COMPOSE_FILE="${COMPOSE_FILE:-${APP_DIR}/docker-compose.server.yml}"
ENV_FILE="${ENV_FILE:-${SHARED_DIR}/.env}"
IMAGE_ENV_FILE="${IMAGE_ENV_FILE:-${APP_DIR}/.image.env}"

require_command() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing command: $cmd" >&2
    exit 1
  fi
}

load_base_env() {
  require_command docker
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing env file: $ENV_FILE" >&2
    exit 1
  fi

  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  if [[ -f "$IMAGE_ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$IMAGE_ENV_FILE"
  fi
  set +a
}

ensure_runtime_env() {
  load_base_env

  local required=(
    TG_V_CHAT_IMAGE
    TG_V_CHAT_DATABASE_URL
    TG_V_CHAT_SESSION_KEY
    TG_V_CHAT_BOT_TOKEN
    TG_V_CHAT_PRIMARY_API_ID
    TG_V_CHAT_PRIMARY_API_HASH
    TG_V_CHAT_STANDBY_1_API_ID
    TG_V_CHAT_STANDBY_1_API_HASH
    TG_V_CHAT_STANDBY_2_API_ID
    TG_V_CHAT_STANDBY_2_API_HASH
  )

  local missing=()
  local key
  for key in "${required[@]}"; do
    if [[ -z "${!key:-}" ]]; then
      missing+=("$key")
    fi
  done

  if (( ${#missing[@]} > 0 )); then
    echo "Missing runtime env vars: ${missing[*]}" >&2
    exit 1
  fi

  case "$TG_V_CHAT_DATABASE_URL" in
    postgresql://*|postgresql+psycopg://*) ;;
    *)
      echo "TG_V_CHAT_DATABASE_URL must be PostgreSQL." >&2
      exit 1
      ;;
  esac
}

compose() {
  (cd "$APP_DIR" && docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@")
}
