#!/bin/bash
set -euo pipefail

# Configuration via environment variables
WIREPUSHER_ID="${WIREPUSHER_ID:?WIREPUSHER_ID is required}"
WIREPUSHER_TYPE="${WIREPUSHER_TYPE:-log-alert}"
LOG_PATTERN="${LOG_PATTERN:-error|exception|fatal|panic}"
CONTAINERS="${CONTAINERS:-}"  # empty = all containers
COOLDOWN="${COOLDOWN:-60}"    # seconds between duplicate alerts
IGNORE_PATTERNS=(
  "FCM token not found"
)

declare -A last_alert

send_alert() {
  local container="$1"
  local msg="${2:0:500}"

  curl -sf -G "https://wirepusher.com/send" \
    --data-urlencode "id=$WIREPUSHER_ID" \
    --data-urlencode "title=🚨 $container" \
    --data-urlencode "message=$msg" \
    --data-urlencode "type=$WIREPUSHER_TYPE" \
    > /dev/null 2>&1 || echo "[log-alerter] Failed to send alert"
}

should_alert() {
  local key="$1"
  local now
  now=$(date +%s)

  if [[ -z "${last_alert[$key]+x}" ]] || (( now - last_alert[$key] >= COOLDOWN )); then
    last_alert[$key]=$now
    return 0
  fi
  return 1
}

watch_container() {
  local name="$1"
  local id="$2"
  echo "[log-alerter] Watching: $name"
  docker logs --follow --tail=0 --timestamps "$id" 2>&1 | while read -r line; do
    if echo "$line" | grep -qiE "$LOG_PATTERN"; then
      ignored=false
      for pat in "${IGNORE_PATTERNS[@]}"; do
        if [[ "$line" == *"$pat"* ]]; then
          ignored=true
          break
        fi
      done
      if $ignored; then continue; fi
      # Strip timestamps, dates, URLs, and query params so similar errors share one cooldown key
      normalized=$(echo "$line" | sed -E \
        -e 's/[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}[.,]?[0-9]*/TIMESTAMP/g' \
        -e 's/dateFrom=[0-9-]+/dateFrom=X/g' \
        -e 's/numberOfDays=[0-9]+/numberOfDays=X/g' \
        -e 's|\?[^ ]*||g')
      key="$name:$(echo "$normalized" | md5sum | cut -d' ' -f1)"
      if should_alert "$key"; then
        echo "[log-alerter] Alert [$name]: $line"
        send_alert "$name" "$line"
      fi
    fi
  done &
}

echo "[log-alerter] Starting log watcher..."
echo "[log-alerter] WirePusher ID: ${WIREPUSHER_ID:0:4}..."
echo "[log-alerter] Pattern: $LOG_PATTERN"
echo "[log-alerter] Cooldown: ${COOLDOWN}s"

if [[ -n "$CONTAINERS" ]]; then
  for container in $CONTAINERS; do
    watch_container "$container" "$container"
  done
  wait
else
  echo "[log-alerter] Watching all containers"

  # Watch currently running containers
  for id in $(docker ps -q); do
    name=$(docker inspect --format '{{.Name}}' "$id" | sed 's/\///')
    watch_container "$name" "$id"
  done

  # Watch for new containers starting
  docker events --filter 'event=start' --format '{{.Actor.Attributes.name}} {{.ID}}' | while read -r name id; do
    watch_container "$name" "$id"
  done
fi
