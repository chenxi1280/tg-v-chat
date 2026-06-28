#!/usr/bin/env bash

set -euo pipefail

USER_NAME="${USER_NAME:-root}"
HOST="${HOST:-}"
BASE_DIR="${BASE_DIR:-/data/tg-v-chat}"
REF_NAME="${REF_NAME:-HEAD}"
EXPECTED_BRANCHES="${EXPECTED_BRANCHES:-release}"
RELEASE_SSH_ATTEMPTS="${RELEASE_SSH_ATTEMPTS:-3}"
RELEASE_SSH_RETRY_DELAY="${RELEASE_SSH_RETRY_DELAY:-10}"
SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-60}"
REMOTE_INSTALL_TIMEOUT_SECONDS="${REMOTE_INSTALL_TIMEOUT_SECONDS:-900}"
SSH_OPTS=(-o "BatchMode=yes" -o "ConnectTimeout=${SSH_CONNECT_TIMEOUT}")

usage() {
  cat <<'EOF'
Usage:
  bash deploy/release.sh --host <host> [options]

Options:
  --host HOST             Target SSH host, required
  --user USER             SSH user, default root
  --base-dir DIR          Remote base directory, default /data/tg-v-chat
  --ref REF               Git ref to release, default HEAD
  --branch-list "..."     Allowed release branches, default "release"
  --ssh-opt OPT           Extra ssh/scp option, can be repeated
  -h, --help              Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --user) USER_NAME="$2"; shift 2 ;;
    --base-dir) BASE_DIR="$2"; shift 2 ;;
    --ref) REF_NAME="$2"; shift 2 ;;
    --branch-list) EXPECTED_BRANCHES="$2"; shift 2 ;;
    --ssh-opt) SSH_OPTS+=("$2"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

require_command() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing command: $cmd" >&2
    exit 1
  fi
}

require_positive_integer() {
  local name="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
    echo "${name} must be a positive integer, got: ${value}" >&2
    exit 1
  fi
}

run_with_retries() {
  local label="$1"
  shift
  local attempt status

  for ((attempt = 1; attempt <= RELEASE_SSH_ATTEMPTS; attempt++)); do
    echo "==> ${label} (attempt ${attempt}/${RELEASE_SSH_ATTEMPTS})"
    if "$@"; then
      return 0
    else
      status=$?
    fi

    if (( attempt == RELEASE_SSH_ATTEMPTS )); then
      echo "${label} failed after ${RELEASE_SSH_ATTEMPTS} attempt(s)" >&2
      return "$status"
    fi
    sleep "$RELEASE_SSH_RETRY_DELAY"
  done
}

assert_release_branch() {
  local current_branch="$1"
  local branch
  for branch in $EXPECTED_BRANCHES; do
    if [[ "$current_branch" == "$branch" ]]; then
      return 0
    fi
  done

  echo "Refusing to release from branch '${current_branch}'. Allowed: ${EXPECTED_BRANCHES}" >&2
  exit 1
}

if [[ -z "$HOST" ]]; then
  usage >&2
  exit 1
fi

require_command git
require_command ssh
require_command scp
require_command mktemp
require_command tar
require_positive_integer RELEASE_SSH_ATTEMPTS "$RELEASE_SSH_ATTEMPTS"
require_positive_integer RELEASE_SSH_RETRY_DELAY "$RELEASE_SSH_RETRY_DELAY"
require_positive_integer SSH_CONNECT_TIMEOUT "$SSH_CONNECT_TIMEOUT"
require_positive_integer REMOTE_INSTALL_TIMEOUT_SECONDS "$REMOTE_INSTALL_TIMEOUT_SECONDS"

assert_release_branch "$(git branch --show-current)"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree is dirty. Commit changes before release." >&2
  exit 1
fi

short_sha="$(git rev-parse --short "$REF_NAME")"
full_sha="$(git rev-parse "$REF_NAME")"
release_id="$(date '+%Y%m%d%H%M%S')_${short_sha}"
archive_path="$(mktemp "/tmp/tg-v-chat-release-${release_id}.XXXXXX.tar.gz")"
image_env_path="$(mktemp "/tmp/tg-v-chat-image-env-${release_id}.XXXXXX.env")"
remote_archive="${BASE_DIR}/incoming/${release_id}.tar.gz"
remote_tmp_archive="/tmp/tg-v-chat-release-${release_id}.tar.gz"
remote_image_env="/tmp/tg-v-chat-release-${release_id}.image.env"
remote_release_dir="${BASE_DIR}/releases/${release_id}"

trap 'rm -f "$archive_path" "$image_env_path"' EXIT

cat >"$image_env_path" <<EOF
TG_V_CHAT_IMAGE=${TG_V_CHAT_IMAGE:?TG_V_CHAT_IMAGE is required}
RELEASE_ID=${release_id}
RELEASE_SHA=${full_sha}
EOF

echo "==> Checking SSH connectivity to ${USER_NAME}@${HOST}"
run_with_retries "Checking SSH connectivity" ssh "${SSH_OPTS[@]}" "${USER_NAME}@${HOST}" "true"

echo "==> Creating release archive for ${REF_NAME} (${short_sha})"
git archive --format=tar.gz --output "$archive_path" "$REF_NAME"

echo "==> Uploading release archive"
run_with_retries "Uploading release archive" \
  scp "${SSH_OPTS[@]}" "$archive_path" "${USER_NAME}@${HOST}:${remote_tmp_archive}"
run_with_retries "Uploading image env" \
  scp "${SSH_OPTS[@]}" "$image_env_path" "${USER_NAME}@${HOST}:${remote_image_env}"

echo "==> Installing release ${release_id} on ${HOST}"
run_with_retries "Installing remote release" timeout "$REMOTE_INSTALL_TIMEOUT_SECONDS" \
  ssh "${SSH_OPTS[@]}" "${USER_NAME}@${HOST}" "\
set -euo pipefail && \
mkdir -p '${BASE_DIR}/incoming' '${BASE_DIR}/releases' && \
existing_image_env='' && \
if [[ -f '${remote_release_dir}/.image.env' ]]; then \
  existing_image_env=\"\$(mktemp '/tmp/tg-v-chat-existing-image-env.XXXXXX')\" && \
  cp '${remote_release_dir}/.image.env' \"\${existing_image_env}\"; \
fi && \
if [[ -f '${remote_tmp_archive}' ]]; then mv -f '${remote_tmp_archive}' '${remote_archive}'; fi && \
if [[ ! -f '${remote_archive}' ]]; then echo 'Missing release archive: ${remote_archive}' >&2; exit 1; fi && \
rm -rf '${remote_release_dir}' && \
mkdir -p '${remote_release_dir}' && \
if [[ -f '${remote_archive}' ]]; then tar -xzf '${remote_archive}' -C '${remote_release_dir}'; fi && \
if [[ -f '${remote_image_env}' ]]; then mv -f '${remote_image_env}' '${remote_release_dir}/.image.env'; fi && \
if [[ ! -f '${remote_release_dir}/.image.env' && -n \"\${existing_image_env}\" ]]; then mv -f \"\${existing_image_env}\" '${remote_release_dir}/.image.env'; fi && \
if [[ ! -f '${remote_release_dir}/.image.env' ]]; then echo 'Missing image env: ${remote_release_dir}/.image.env' >&2; exit 1; fi && \
GHCR_USERNAME=$(printf '%q' "${GHCR_USERNAME:-}") \
GHCR_TOKEN=$(printf '%q' "${GHCR_TOKEN:-}") \
POST_DEPLOY_CHECKS_ENABLED=$(printf '%q' "${POST_DEPLOY_CHECKS_ENABLED:-}") \
BASE_DIR='${BASE_DIR}' \
RELEASE_ID='${release_id}' \
bash '${remote_release_dir}/deploy/server-install-release.sh'"

echo "Release ${release_id} deployed"
