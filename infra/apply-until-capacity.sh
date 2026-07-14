#!/usr/bin/env bash
# Retry `terraform apply` until the free-tier Ampere A1 instance gets host
# capacity in the home region. Everything else is already created, so each
# attempt is just a quick instance launch.
#
# Run it under tmux so it survives disconnects:
#   tmux new -s oci
#   ./apply-until-capacity.sh
#   (detach with Ctrl-b d ; reattach with `tmux attach -t oci`)
#
# Override the retry gap with:  INTERVAL=300 ./apply-until-capacity.sh
set -u

export PATH="$HOME/.tfenv/bin:$HOME/.local/bin:$PATH"
cd "$(dirname "$0")" || exit 2

INTERVAL="${INTERVAL:-180}"   # seconds between attempts

n=0
while true; do
  n=$((n + 1))
  echo ">>> attempt $n at $(date '+%Y-%m-%d %H:%M:%S')"

  if terraform apply -input=false -auto-approve; then
    echo
    echo ">>> SUCCESS after $n attempt(s)"
    terraform output
    exit 0
  fi

  echo ">>> not yet (out of host capacity); retrying in ${INTERVAL}s ..."
  sleep "$INTERVAL"
done
