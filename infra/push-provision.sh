#!/usr/bin/env bash
# Push provision.sh to the running instance over the OCI bastion and run it.
# The dev loop for iterating on provisioning without rebooting or replacing the
# box: edit provision.sh, then run this.
#
#   ./push-provision.sh
#
# Uses a throwaway ed25519 key per run. (OCI Bastion rejects RSA keys on modern
# OpenSSH, and an ephemeral key needs no management — the bastion injects it
# into the target for the session.)
set -euo pipefail

export PATH="$HOME/.tfenv/bin:$HOME/.local/bin:$PATH"
cd "$(dirname "$0")"

BASTION_ID=$(terraform output -raw bastion_id)
INSTANCE_ID=$(terraform output -raw instance_id)
IP=$(terraform output -raw instance_private_ip)
REGION=$(terraform output -raw region)

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
ssh-keygen -t ed25519 -N '' -f "$TMP/key" -C thewave-push >/dev/null

echo ">>> creating managed-SSH bastion session ..."
SID=$(oci bastion session create-managed-ssh \
  --bastion-id "$BASTION_ID" \
  --target-resource-id "$INSTANCE_ID" \
  --target-os-username opc \
  --ssh-public-key-file "$TMP/key.pub" \
  --session-ttl 1800 \
  --query 'data.id' --raw-output)

echo ">>> waiting for session to become ACTIVE ..."
for _ in $(seq 1 40); do
  st=$(oci bastion session get --session-id "$SID" --query 'data."lifecycle-state"' --raw-output 2>/dev/null || true)
  [ "$st" = ACTIVE ] && break
  [ "$st" = FAILED ] && { echo "!!! session entered FAILED state" >&2; exit 1; }
  sleep 5
done

PROXY="ssh -i $TMP/key -o StrictHostKeyChecking=accept-new -W %h:%p -p 22 ${SID}@host.bastion.${REGION}.oci.oraclecloud.com"
OPTS=(-i "$TMP/key" -o StrictHostKeyChecking=accept-new -o "ProxyCommand=$PROXY")

echo ">>> copying provision.sh to the instance ..."
scp "${OPTS[@]}" provision.sh "opc@${IP}:/tmp/provision.sh"

echo ">>> running provision.sh on the instance ..."
ssh "${OPTS[@]}" "opc@${IP}" \
  'sudo install -m 0755 -D /tmp/provision.sh /opt/thewave/provision.sh && sudo /opt/thewave/provision.sh'

echo ">>> done."
