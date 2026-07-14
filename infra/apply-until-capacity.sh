#!/usr/bin/env bash
# Retry `terraform apply` until the free-tier Ampere A1 instance gets host
# capacity. Capacity is per-availability-domain, so this cycles through ALL
# ADs in the home region (London has 3) — roughly tripling the odds of
# catching a free slot versus only ever trying AD-1.
#
# Everything else is already created, so each attempt is just a quick instance
# launch. On success the winning AD is pinned into terraform.tfvars so later
# plain `terraform apply` runs stay stable (the AD forces replacement if it
# ever drifts back to the default).
#
# Run it under tmux so it survives disconnects:
#   tmux new -s oci
#   ./apply-until-capacity.sh
#   (detach with Ctrl-b d ; reattach with `tmux attach -t oci`)
#
# Override the per-attempt gap with:  INTERVAL=60 ./apply-until-capacity.sh
set -u

export PATH="$HOME/.tfenv/bin:$HOME/.local/bin:$PATH"
cd "$(dirname "$0")" || exit 2

INTERVAL="${INTERVAL:-90}"   # seconds between attempts (keep >=60 to avoid 429s)
TFVARS="terraform.tfvars"

# Discover every availability domain in the home region.
read -ra ADS <<< "$(oci iam availability-domain list --query "join(' ', data[].name)" --raw-output 2>/dev/null)"
if [ "${#ADS[@]}" -eq 0 ]; then
  echo "!!! could not list availability domains (check 'oci' auth / PATH)"; exit 2
fi
echo ">>> cycling ${#ADS[@]} availability domain(s): ${ADS[*]}"

# Persist the winning AD so future applies don't try to move the instance.
pin_ad() {
  local ad="$1"
  if grep -q '^availability_domain' "$TFVARS" 2>/dev/null; then
    sed -i "s|^availability_domain.*|availability_domain = \"$ad\"|" "$TFVARS"
  else
    echo "availability_domain = \"$ad\"" >> "$TFVARS"
  fi
}

n=0
while true; do
  for ad in "${ADS[@]}"; do
    n=$((n + 1))
    echo ">>> attempt $n in $ad at $(date '+%Y-%m-%d %H:%M:%S')"

    if terraform apply -input=false -auto-approve -var="availability_domain=$ad"; then
      pin_ad "$ad"
      echo
      echo ">>> SUCCESS in $ad after $n attempt(s) — pinned in $TFVARS"
      terraform output
      exit 0
    fi

    echo ">>> not yet (out of capacity in $ad); next attempt in ${INTERVAL}s ..."
    sleep "$INTERVAL"
  done
done
