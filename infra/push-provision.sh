#!/usr/bin/env bash
# Push provision.sh to the running instance and run it. The dev loop for
# iterating on provisioning without rebooting or replacing the box: edit
# provision.sh, then run this.
#
#   ./push-provision.sh          # Tailscale if reachable, else bastion
#   ./push-provision.sh --fresh  # force a brand-new bastion session
#
# Connection preference:
#   1. Tailscale SSH directly to host `thewave-ampere` (no bastion, no key —
#      tailscaled authenticates). Uses the MagicDNS name if it resolves, else
#      the tailnet IP from `tailscale ip -4 thewave-ampere`.
#   2. Fall back to the OCI bastion when Tailscale isn't up, the box isn't a
#      reachable peer, or the SSH probe fails.
#
# The two transports log in as DIFFERENT users, deliberately. Tailscale uses
# the primary user (marc). The bastion stays on opc because this script is what
# creates marc — targeting marc there would be circular, and would fail exactly
# on a fresh or half-provisioned box, which is when the bastion path matters.
# Both users have passwordless sudo, which the run step below needs.
#
# Bastion sessions use an ed25519 key (OCI Bastion rejects RSA on modern
# OpenSSH). A managed-SSH session is bound to the exact public key injected at
# creation, so an existing session can only be reused if we still hold its
# private key. We cache the key + session OCID in .push-session/ (gitignored)
# and reuse it while it's ACTIVE with enough TTL left, instead of paying for a
# fresh 30-minute session on every push.
set -euo pipefail

export PATH="$HOME/.tfenv/bin:$HOME/.local/bin:$PATH"
cd "$(dirname "$0")"

TS_NAME=thewave-ampere  # tailnet machine name of the instance
TS_USER=marc            # primary user; Tailscale transport logs in as this
BASTION_USER=opc        # bootstrap/break-glass user; see the note above
SESSION_TTL=1800        # seconds; TTL of a newly created bastion session
MIN_REMAINING=180       # recreate if a cached session has less than this left
CACHE=.push-session     # holds key, key.pub, sid (all gitignored)

[ "${1:-}" = --fresh ] && rm -rf "$CACHE"

# Retry a command with linear backoff. Smooths over the brief window right
# after a bastion session goes ACTIVE where the key isn't injected yet and the
# first connection fails with "Permission denied (publickey)".
retry() {
  local n=1 max=5 delay=3
  until "$@"; do
    if [ "$n" -ge "$max" ]; then
      echo "!!! command failed after $max attempts: $*" >&2
      return 1
    fi
    echo ">>> attempt $n failed; retrying in ${delay}s ..." >&2
    sleep "$delay"
    n=$((n + 1))
    delay=$((delay + 3))
  done
}

# Echo a usable SSH target for the instance over Tailscale — the MagicDNS name
# if it resolves locally, else the tailnet IPv4 — and return 0 only if the box
# actually answers an SSH probe. Returns 1 (caller falls back to the bastion)
# when Tailscale is down, the box isn't a peer, or it's unreachable.
tailscale_target() {
  command -v tailscale >/dev/null 2>&1 || return 1
  local ip target
  ip=$(tailscale ip -4 "$TS_NAME" 2>/dev/null) || return 1
  [ -n "$ip" ] || return 1
  if getent hosts "$TS_NAME" >/dev/null 2>&1; then
    target=$TS_NAME
  else
    target=$ip
  fi
  # BatchMode so an unreachable host times out instead of hanging on a prompt.
  ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 -o BatchMode=yes \
    "${TS_USER}@${target}" true 2>/dev/null || return 1
  echo "$target"
}

# Return 0 if the cached bastion session exists, is ACTIVE, and has more than
# MIN_REMAINING seconds of TTL left — i.e. it's usable as-is.
cached_session_usable() {
  [ -f "$CACHE/sid" ] && [ -f "$CACHE/key" ] || return 1
  local sid state created ttl remaining
  sid=$(cat "$CACHE/sid")
  read -r state created ttl < <(
    oci bastion session get --session-id "$sid" \
      --query 'join(" ", [data."lifecycle-state", data."time-created", to_string(data."session-ttl-in-seconds")])' \
      --raw-output 2>/dev/null
  ) || return 1
  [ "$state" = ACTIVE ] || return 1
  remaining=$(( ttl - ( $(date -u +%s) - $(date -u -d "$created" +%s) ) ))
  [ "$remaining" -gt "$MIN_REMAINING" ]
}

# ---------------------------------------------------------------------------
# Pick a connection: Tailscale direct, else the OCI bastion.
# ---------------------------------------------------------------------------
if TARGET=$(tailscale_target); then
  echo ">>> reaching $TS_NAME directly over Tailscale ($TARGET) ..."
  DEST="${TS_USER}@${TARGET}"
  SSH_CMD=(ssh -o StrictHostKeyChecking=accept-new)
  SCP_CMD=(scp -o StrictHostKeyChecking=accept-new)
else
  echo ">>> Tailscale unavailable; falling back to the OCI bastion ..."
  BASTION_ID=$(terraform output -raw bastion_id)
  INSTANCE_ID=$(terraform output -raw instance_id)
  IP=$(terraform output -raw instance_private_ip)
  REGION=$(terraform output -raw region)

  if cached_session_usable; then
    SID=$(cat "$CACHE/sid")
    echo ">>> reusing cached bastion session $SID"
  else
    echo ">>> creating managed-SSH bastion session ..."
    rm -rf "$CACHE"
    mkdir -p "$CACHE"
    ssh-keygen -t ed25519 -N '' -f "$CACHE/key" -C thewave-push >/dev/null
    SID=$(oci bastion session create-managed-ssh \
      --bastion-id "$BASTION_ID" \
      --target-resource-id "$INSTANCE_ID" \
      --target-os-username "$BASTION_USER" \
      --ssh-public-key-file "$CACHE/key.pub" \
      --session-ttl "$SESSION_TTL" \
      --query 'data.id' --raw-output)
    echo "$SID" > "$CACHE/sid"

    echo ">>> waiting for session to become ACTIVE ..."
    for _ in $(seq 1 40); do
      st=$(oci bastion session get --session-id "$SID" --query 'data."lifecycle-state"' --raw-output 2>/dev/null || true)
      [ "$st" = ACTIVE ] && break
      [ "$st" = FAILED ] && { echo "!!! session entered FAILED state" >&2; rm -rf "$CACHE"; exit 1; }
      sleep 5
    done
  fi

  PROXY="ssh -i $CACHE/key -o StrictHostKeyChecking=accept-new -W %h:%p -p 22 ${SID}@host.bastion.${REGION}.oci.oraclecloud.com"
  OPTS=(-i "$CACHE/key" -o StrictHostKeyChecking=accept-new -o "ProxyCommand=$PROXY")
  DEST="${BASTION_USER}@${IP}"
  SSH_CMD=(ssh "${OPTS[@]}")
  SCP_CMD=(scp "${OPTS[@]}")
fi

echo ">>> copying provision.sh to the instance ..."
# Staged in the connecting user's home, NOT /tmp: /tmp is sticky (1777), so a
# copy left there by one transport's user (opc via the bastion) can't be
# overwritten by the other's (marc via Tailscale) — it fails with EACCES.
retry "${SCP_CMD[@]}" provision.sh "${DEST}:provision.sh"

echo ">>> running provision.sh on the instance ..."
# provision.sh is idempotent, so retrying the whole run is safe if a mid-run
# connection drop leaves it partially applied.
retry "${SSH_CMD[@]}" "$DEST" \
  'sudo install -m 0755 -D "$HOME/provision.sh" /opt/thewave/provision.sh && sudo /opt/thewave/provision.sh'

echo ">>> done."
