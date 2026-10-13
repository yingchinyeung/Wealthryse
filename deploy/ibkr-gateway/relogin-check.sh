#!/usr/bin/env bash
# Host-side re-login rules for the IB Gateway container, in Hong Kong time.
# Run as root by systemd timers (deploy/ibkr-gateway/systemd/):
#
#   relogin-check.sh weekly   Monday 21:00 (ibkr-gateway-weekly-relogin.timer).
#                             Recreate the gateway container so IBC does a fresh
#                             login and IBKR sends the weekly IB Key push.
#   relogin-check.sh check    Every 30 minutes from 08:00 to 23:00
#                             (ibkr-gateway-relogin-check.timer). The gateway is
#                             signed out when the owner signs in elsewhere
#                             (EXISTING_SESSION_DETECTED_ACTION=secondary).
#                             Recreate it only when the collector's status file
#                             has said "disconnected" for at least 10 minutes,
#                             or the file is missing or at least 10 minutes old.
#                             Outside the window it does nothing.
#
# Every gateway start is a login and sends one push to the owner's phone, so:
# - a gateway that started less than 10 minutes ago is left alone (its login,
#   or the owner's approval, may still be in progress);
# - the weekly run skips a gateway that already did a fresh login in the last
#   30 minutes, for example after a reboot in the Monday 20:30 patch window;
# - both modes take the same lock, so the two 21:00 runs on a Monday cannot
#   both restart it.
#
# "Recreate" (up --force-recreate) rather than "restart" so every re-login
# starts from the pinned image with no state left from the last session.
set -euo pipefail

COMPOSE_DIR=/opt/ibkr-live
ENV_FILE=/etc/ibkr-live/ibkr-live.env
STATUS_FILE=/run/ibkr-live/status.json
LOCK_FILE=/run/lock/ibkr-gateway-relogin.lock
GATEWAY_SERVICE=ib-gateway
COLLECTOR_SERVICE=collector
STALE_AFTER_SECONDS=600          # 10 minutes
LOGIN_GRACE_SECONDS=600          # leave a gateway younger than this alone
WEEKLY_FRESH_LOGIN_SECONDS=1800  # weekly run skips a gateway younger than this
WINDOW_TIME_ZONE=Asia/Hong_Kong
WINDOW_START=0800                # inclusive, HHMM
WINDOW_END=2300                  # inclusive, HHMM

log() { printf 'relogin-check: %s\n' "$*"; }

compose() {
  docker compose --project-directory "$COMPOSE_DIR" --env-file "$ENV_FILE" \
    -f "$COMPOSE_DIR/docker-compose.yml" "$@"
}

# Seconds since the gateway container last started; very large when it does
# not exist or never started.
gateway_age() {
  local id started
  id=$(compose ps --all --quiet "$GATEWAY_SERVICE" 2>/dev/null | head -n 1 || true)
  if [ -z "$id" ]; then
    echo 999999999
    return
  fi
  started=$(docker inspect --format '{{.State.StartedAt}}' "$id")
  echo $(($(date +%s) - $(date -d "$started" +%s)))
}

# Print "<gateway_state> <observed_at epoch> <last_message_at epoch, 0 if null>",
# or "missing 0 0" when the status file is absent or unreadable.
read_status() {
  python3 - "$STATUS_FILE" <<'PY'
import datetime
import json
import sys


def epoch(value):
    if not value:
        return 0
    stamp = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return int(stamp.timestamp())


try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        status = json.load(handle)
    state = status.get("gateway_state")
    if state not in ("connected", "disconnected"):
        state = "unknown"
    print(state, epoch(status.get("observed_at")), epoch(status.get("last_message_at")))
except (OSError, ValueError, TypeError, AttributeError):
    print("missing 0 0")
PY
}

recreate_gateway() {
  log "recreating ${GATEWAY_SERVICE}: $1 (IBC logs in again; one IB Key push)"
  compose up --detach --no-deps --force-recreate "$GATEWAY_SERVICE"
}

run_weekly() {
  local age
  age=$(gateway_age)
  if ((age < WEEKLY_FRESH_LOGIN_SECONDS)); then
    log "gateway started ${age}s ago, so it already did a fresh login; skipping the weekly restart"
    return 0
  fi
  recreate_gateway "weekly Monday 21:00 fresh login"
}

run_check() {
  local hhmm age state observed last now since reason=""
  hhmm=$(TZ=$WINDOW_TIME_ZONE date +%H%M)
  if ((10#$hhmm < 10#$WINDOW_START || 10#$hhmm > 10#$WINDOW_END)); then
    log "outside ${WINDOW_START}-${WINDOW_END} ${WINDOW_TIME_ZONE} (now ${hhmm}); nothing to do"
    return 0
  fi
  age=$(gateway_age)
  if ((age < LOGIN_GRACE_SECONDS)); then
    log "gateway started ${age}s ago; its login may still be in progress; no action"
    return 0
  fi
  read -r state observed last < <(read_status)
  now=$(date +%s)
  if [ "$state" = missing ]; then
    reason="status file ${STATUS_FILE} is missing or unreadable"
  elif ((now - observed >= STALE_AFTER_SECONDS)); then
    reason="status file is $((now - observed))s old"
  elif [ "$state" = disconnected ]; then
    # Disconnected since the last gateway message, or since the gateway
    # started when there has been none since then.
    since=$((now - age))
    if ((last > since)); then
      since=$last
    fi
    if ((now - since >= STALE_AFTER_SECONDS)); then
      reason="collector reports disconnected for $((now - since))s"
    fi
  fi
  if [ -z "$reason" ]; then
    log "gateway_state=${state}; no action"
    return 0
  fi
  if [ "$state" = missing ] || ((now - observed >= STALE_AFTER_SECONDS)); then
    # A stale file can mean the collector is down; starting it is a no-op
    # when it is running and never touches the gateway.
    compose up --detach --no-deps "$COLLECTOR_SERVICE"
  fi
  recreate_gateway "$reason"
}

main() {
  case "${1:-}" in
    weekly | check) ;;
    *)
      printf 'usage: %s weekly|check\n' "$0" >&2
      exit 2
      ;;
  esac
  exec 9>"$LOCK_FILE"
  if ! flock --wait 300 9; then
    log "another re-login run still holds ${LOCK_FILE}; skipping"
    exit 0
  fi
  if [ "$1" = weekly ]; then
    run_weekly
  else
    run_check
  fi
}

main "$@"
