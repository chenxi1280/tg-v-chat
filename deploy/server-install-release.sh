#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

BASE_DIR="${BASE_DIR:-/data/tg-v-chat}"
RELEASE_ID="${RELEASE_ID:-$(basename "$RELEASE_DIR")}"
SHARED_DIR="${SHARED_DIR:-${BASE_DIR}/shared}"
CURRENT_LINK="${CURRENT_LINK:-${BASE_DIR}/current}"
RELEASES_DIR="${RELEASES_DIR:-${BASE_DIR}/releases}"
INCOMING_DIR="${INCOMING_DIR:-${BASE_DIR}/incoming}"
BACKUP_DIR="${BACKUP_DIR:-${BASE_DIR}/backups}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"

require_command() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing command: $cmd" >&2
    exit 1
  fi
}

prepare_shared_layout() {
  mkdir -p "$RELEASES_DIR" "$SHARED_DIR" "$INCOMING_DIR" "$BACKUP_DIR"
}

bootstrap_shared_env() {
  local shared_env="${SHARED_DIR}/.env"
  if [[ -f "$shared_env" ]]; then
    return 0
  fi

  cp "${RELEASE_DIR}/.env.example" "$shared_env"
  echo "Created ${shared_env} from .env.example. Fill production values and rerun deploy." >&2
  exit 1
}

prune_old_releases() {
  mapfile -t release_paths < <(find "$RELEASES_DIR" -mindepth 1 -maxdepth 1 -type d | sort)
  local total="${#release_paths[@]}"
  local current_target=""

  if (( total <= KEEP_RELEASES )); then
    return 0
  fi
  if [[ -L "$CURRENT_LINK" ]]; then
    current_target="$(readlink -f "$CURRENT_LINK")"
  fi

  local remove_count=$(( total - KEEP_RELEASES ))
  local idx=0
  while (( idx < remove_count )); do
    if [[ "${release_paths[$idx]}" != "$current_target" ]]; then
      rm -rf "${release_paths[$idx]}"
    fi
    idx=$((idx + 1))
  done
}

post_deploy_checks_enabled() {
  case "${POST_DEPLOY_CHECKS_ENABLED:-true}" in
    false|False|FALSE|0|no|No|NO|off|Off|OFF)
      return 1
      ;;
    *)
      return 0
      ;;
  esac
}

run_post_deploy_checks() {
  if ! post_deploy_checks_enabled; then
    echo "==> Post-deploy checks skipped"
    return 0
  fi

  APP_DIR="${RELEASE_DIR}" BASE_DIR="${BASE_DIR}" SHARED_DIR="${SHARED_DIR}" \
    ENV_FILE="${SHARED_DIR}/.env" bash "${RELEASE_DIR}/deploy/check-runtime.sh"
}

require_command docker
prepare_shared_layout
bootstrap_shared_env

if [[ ! -f "${RELEASE_DIR}/docker-compose.server.yml" ]]; then
  echo "Release directory is invalid: ${RELEASE_DIR}" >&2
  exit 1
fi

echo "==> Deploying release ${RELEASE_ID}"
APP_DIR="${RELEASE_DIR}" BASE_DIR="${BASE_DIR}" SHARED_DIR="${SHARED_DIR}" \
  ENV_FILE="${SHARED_DIR}/.env" bash "${RELEASE_DIR}/deploy/compose-up.sh"

ln -sfn "$RELEASE_DIR" "${CURRENT_LINK}.tmp"
mv -Tf "${CURRENT_LINK}.tmp" "$CURRENT_LINK"

run_post_deploy_checks
prune_old_releases

echo "Release ${RELEASE_ID} is live"
echo "current -> $(readlink -f "$CURRENT_LINK")"
