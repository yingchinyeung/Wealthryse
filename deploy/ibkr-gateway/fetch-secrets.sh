#!/usr/bin/env bash
# Fetch the IB Gateway and live-collector secrets from the main Key Vault with
# the VM's user-assigned managed identity, then log Docker in to the main
# registry with the same identity. Runs as root from ibkr-live.service, which
# loads KEY_VAULT_URI, IDENTITY_CLIENT_ID and REGISTRY_LOGIN_SERVER from
# /etc/ibkr-live/ibkr-live.env (non-secret values written by cloud-init).
#
# Handling of secret values:
# - They go straight from the HTTPS response into files on tmpfs (/run). They
#   never reach disk, a command line, an environment variable, stdout or the
#   journal, and this script never prints them.
# - Each container has its own directory: gateway/ (ibkr-username,
#   ibkr-password) and collector/ (database-live-url). Each file is owned by
#   the one container user that reads it, mode 0400, under a root-only (0700)
#   parent, so no host account other than root can open them. Compose mounts
#   each file read-only into its container only.
# - Access tokens are handed to curl on stdin (-H @- and --data-binary @-),
#   never as arguments, so they do not show in the process list.
#
# Registry login: the IMDS token (ACR audience) is exchanged at the registry's
# /oauth2/exchange endpoint for an ACR refresh token, which is piped to
# `docker login --password-stdin`. This needs only curl and python3, both on
# the Ubuntu image, rather than the Azure CLI and its package repository.
# DOCKER_CONFIG points at tmpfs, so the refresh token (valid for about three
# hours, enough for the pulls at start-up) never reaches disk either.
set -euo pipefail
umask 077

: "${KEY_VAULT_URI:?KEY_VAULT_URI must be set (from /etc/ibkr-live/ibkr-live.env)}"
: "${IDENTITY_CLIENT_ID:?IDENTITY_CLIENT_ID must be set (from /etc/ibkr-live/ibkr-live.env)}"
: "${REGISTRY_LOGIN_SERVER:?REGISTRY_LOGIN_SERVER must be set (from /etc/ibkr-live/ibkr-live.env)}"

SECRETS_DIR=/run/ibkr-secrets
export DOCKER_CONFIG=${DOCKER_CONFIG:-/run/ibkr-docker}
GATEWAY_UID=1000     # "ibgateway" in the gnzsnz/ib-gateway image
COLLECTOR_UID=10001  # "ibkr" in the WealthRyse images (see Dockerfile)
IMDS_TOKEN_URL=http://169.254.169.254/metadata/identity/oauth2/token
KEY_VAULT_RESOURCE=https://vault.azure.net
REGISTRY_RESOURCE=https://containerregistry.azure.net
KEY_VAULT_API_VERSION=7.4
# Role assignments made with the VM can take minutes to reach Key Vault and
# the registry: retry for about five and a half minutes before failing (and
# ibkr-live.service then retries the whole start).
ATTEMPTS=10

log() { printf 'fetch-secrets: %s\n' "$*" >&2; }

# Print one string field of the JSON document on stdin, without a newline.
# On a bad response, fail with a short message that never includes the body.
json_field() {
  python3 -c '
import json, sys
try:
    sys.stdout.write(json.load(sys.stdin)[sys.argv[1]])
except (ValueError, KeyError, TypeError):
    sys.exit("fetch-secrets: response has no usable " + sys.argv[1])
' "$1"
}

# retry <description> <command...>
retry() {
  local what=$1 attempt
  shift
  for ((attempt = 1; attempt <= ATTEMPTS; attempt++)); do
    if "$@"; then
      return 0
    fi
    log "${what}: attempt ${attempt}/${ATTEMPTS} failed, retrying"
    sleep $((attempt * 6))
  done
  log "${what}: giving up"
  return 1
}

# imds_token <resource>: print an access token for the user-assigned identity.
imds_token() {
  curl -sS --fail --max-time 10 --get -H 'Metadata: true' \
    --data-urlencode 'api-version=2018-02-01' \
    --data-urlencode "resource=$1" \
    --data-urlencode "client_id=${IDENTITY_CLIENT_ID}" \
    "$IMDS_TOKEN_URL" | json_field access_token
}

# write_secret <secret name> <destination file> <owner uid>
# Uses $vault_token. Replaces the destination atomically, so a failed fetch
# leaves the previous file in place.
write_secret() {
  local name=$1 dest=$2 owner=$3 tmp
  tmp=$(mktemp "${dest}.XXXXXX")
  if printf 'Authorization: Bearer %s\n' "$vault_token" |
    curl -sS --fail --max-time 15 -H @- \
      "${KEY_VAULT_URI%/}/secrets/${name}?api-version=${KEY_VAULT_API_VERSION}" |
    json_field value >"$tmp" && [ -s "$tmp" ]; then
    chown "${owner}:${owner}" "$tmp"
    chmod 0400 "$tmp"
    mv -f "$tmp" "$dest"
    log "wrote ${name} for uid ${owner} (value not shown)"
    return 0
  fi
  rm -f "$tmp"
  return 1
}

registry_login() {
  local registry_token
  registry_token=$(imds_token "$REGISTRY_RESOURCE") || return 1
  printf 'grant_type=access_token&service=%s&access_token=%s' \
    "$REGISTRY_LOGIN_SERVER" "$registry_token" |
    curl -sS --fail --max-time 15 --data-binary @- \
      -H 'Content-Type: application/x-www-form-urlencoded' \
      "https://${REGISTRY_LOGIN_SERVER}/oauth2/exchange" |
    json_field refresh_token |
    docker login "$REGISTRY_LOGIN_SERVER" \
      --username 00000000-0000-0000-0000-000000000000 --password-stdin >/dev/null
}

main() {
  if [ "$(stat -f -c %T /run)" != tmpfs ]; then
    log "/run is not a tmpfs; refusing to write secrets"
    exit 1
  fi
  install -d -m 0700 -o root -g root "$SECRETS_DIR" "$DOCKER_CONFIG"
  install -d -m 0700 -o root -g root "$SECRETS_DIR/gateway" "$SECRETS_DIR/collector"

  vault_token=$(retry "Key Vault token from IMDS" imds_token "$KEY_VAULT_RESOURCE")
  retry "Key Vault secret ibkr-username" \
    write_secret ibkr-username "$SECRETS_DIR/gateway/ibkr-username" "$GATEWAY_UID"
  retry "Key Vault secret ibkr-password" \
    write_secret ibkr-password "$SECRETS_DIR/gateway/ibkr-password" "$GATEWAY_UID"
  retry "Key Vault secret database-live-url" \
    write_secret database-live-url "$SECRETS_DIR/collector/database-live-url" "$COLLECTOR_UID"
  unset vault_token

  retry "registry login to ${REGISTRY_LOGIN_SERVER}" registry_login
  log "secrets in place and registry login done"
}

main "$@"
