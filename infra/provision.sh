#!/usr/bin/env bash
# Idempotent provisioning for the thewave OCI instance (Oracle Linux 9).
#
# Safe to run any number of times — every step ensures a desired end state, so
# the outcome is the same whether it runs once or a hundred times. Run as root.
#
#   sudo /opt/thewave/provision.sh
#
# cloud-init runs this once on first boot. To iterate WITHOUT rebooting or
# replacing the instance, edit this file in the repo and push it to the box
# with ./push-provision.sh, then it re-runs here. This is the single source of
# truth for what's installed/configured — cloud-init is just the launcher.
set -euo pipefail

LOG=/var/log/thewave-provision.log
exec > >(tee -a "$LOG") 2>&1
echo "=== provision.sh starting $(date -u +%FT%TZ) ==="

if [ "$(id -u)" -ne 0 ]; then
  echo "must run as root (try: sudo $0)" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Package repositories
# ---------------------------------------------------------------------------
ensure_repos() {
  # EPEL (Oracle's mirror) for pv, pwgen, whois, p7zip, moreutils, plus the
  # CodeReady Builder repo whose packages several EPEL packages depend on
  # (e.g. moreutils -> perl(IPC::Run)). Without CRB the dnf transaction fails
  # to resolve and — because dnf installs atomically — nothing installs.
  dnf -y install oracle-epel-release-el9 dnf-plugins-core
  dnf config-manager --set-enabled ol9_codeready_builder

  # Docker CE. $basearch is expanded by dnf, so keep the heredoc quoted.
  cat > /etc/yum.repos.d/docker-ce.repo <<'EOF'
[docker-ce-stable]
name=Docker CE Stable - $basearch
baseurl=https://download.docker.com/linux/centos/9/$basearch/stable
enabled=1
gpgcheck=1
gpgkey=https://download.docker.com/linux/centos/gpg
EOF

  # Tailscale (official OL9 repo). config-manager is idempotent — re-adding the
  # same repo just overwrites the file.
  dnf config-manager --add-repo https://pkgs.tailscale.com/stable/oracle/9/tailscale.repo
}

# ---------------------------------------------------------------------------
# Packages (Ubuntu/apt names mapped to OL9/dnf; see README)
# ---------------------------------------------------------------------------
PACKAGES=(
  # Docker (docker-compose provided by the v2 plugin + a shim below)
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  # Requested utilities
  vim-enhanced git ca-certificates gnupg2 pv pwgen whois jq
  p7zip p7zip-plugins gcc make zip unzip moreutils tmux restic sqlite
  # Tailscale mesh VPN (join the tailnet manually with `tailscale up --ssh`)
  tailscale
)

ensure_packages() {
  dnf -y install "${PACKAGES[@]}"
  # Swap the preinstalled curl-minimal for full curl (no-op once done).
  dnf -y --allowerasing install curl
}

# ---------------------------------------------------------------------------
# Docker daemon + non-root access. opc keeps it for break-glass use;
# PRIMARY_USER is granted the same in ensure_primary_user below.
# ---------------------------------------------------------------------------
ensure_docker() {
  systemctl enable --now docker
  id -nG opc | grep -qw docker || usermod -aG docker opc
}

# ---------------------------------------------------------------------------
# Classic `docker-compose` command as a shim over `docker compose`
# ---------------------------------------------------------------------------
ensure_compose_shim() {
  install -m 0755 /dev/stdin /usr/local/bin/docker-compose <<'EOF'
#!/bin/sh
exec docker compose "$@"
EOF
}

# ---------------------------------------------------------------------------
# Tailscale daemon. Enabling tailscaled is safe to re-run; it does NOT join
# the tailnet — that's a manual, interactive step you run once:
#
#   sudo tailscale up --ssh
#
# and complete the browser login it prints. --ssh lets you reach this box
# directly over the tailnet instead of via the OCI bastion.
# ---------------------------------------------------------------------------
ensure_tailscale() {
  systemctl enable --now tailscaled
}

# ---------------------------------------------------------------------------
# Claude Code CLI, installed for PRIMARY_USER via the official native installer
# (self-contained binary, no Node needed). Idempotent: skip if it's already
# present — the binary self-updates, so re-running only reinstalls. The
# installer drops it in ~/.local/bin and adds that to the user's shell PATH.
#
# Requires ensure_primary_user to have run. The first `claude` still needs an
# interactive login as that user — auth lives in their ~/.claude and can't be
# provisioned here.
# ---------------------------------------------------------------------------
ensure_claude() {
  # Make ~/.local/bin reachable on login (the installer warns but won't edit an
  # existing .bashrc). grep-guard keeps it to a single line across re-runs.
  local home=/home/$PRIMARY_USER
  local rc=$home/.bashrc
  sudo -u "$PRIMARY_USER" grep -qxF 'export PATH="$HOME/.local/bin:$PATH"' "$rc" 2>/dev/null \
    || echo 'export PATH="$HOME/.local/bin:$PATH"' | sudo -u "$PRIMARY_USER" tee -a "$rc" >/dev/null

  sudo -u "$PRIMARY_USER" test -x "$home/.local/bin/claude" && return 0
  sudo -u "$PRIMARY_USER" bash -c 'curl -fsSL https://claude.ai/install.sh | bash'
}

# ---------------------------------------------------------------------------
# uv, for PRIMARY_USER — needed to install the OCI CLI as a uv tool (see
# ensure_oci_cli below). Installer drops the binary in ~/.local/bin, already
# on PATH from ensure_claude.
# ---------------------------------------------------------------------------
ensure_uv() {
  local home=/home/$PRIMARY_USER
  sudo -u "$PRIMARY_USER" test -x "$home/.local/bin/uv" && return 0
  sudo -u "$PRIMARY_USER" bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
}

# ---------------------------------------------------------------------------
# tfenv + Terraform, for PRIMARY_USER, matching the "Tooling / environment"
# section of CLAUDE.md. Installs tfenv itself via git clone (updated in place
# on re-runs), then — if this repo is checked out at THEWAVE_INFRA_DIR — pins
# the version from its .terraform-version file. tfenv install is itself
# idempotent (no-op if that version is already installed).
# ---------------------------------------------------------------------------
THEWAVE_INFRA_DIR=/thewave/infra

ensure_tfenv() {
  local home=/home/$PRIMARY_USER
  local rc=$home/.bashrc

  if sudo -u "$PRIMARY_USER" test -d "$home/.tfenv"; then
    sudo -u "$PRIMARY_USER" git -C "$home/.tfenv" pull --ff-only
  else
    sudo -u "$PRIMARY_USER" git clone --depth=1 https://github.com/tfutils/tfenv.git "$home/.tfenv"
  fi

  sudo -u "$PRIMARY_USER" grep -qxF 'export PATH="$HOME/.tfenv/bin:$PATH"' "$rc" 2>/dev/null \
    || echo 'export PATH="$HOME/.tfenv/bin:$PATH"' | sudo -u "$PRIMARY_USER" tee -a "$rc" >/dev/null

  if sudo -u "$PRIMARY_USER" test -f "$THEWAVE_INFRA_DIR/.terraform-version"; then
    sudo -u "$PRIMARY_USER" bash -c "cd '$THEWAVE_INFRA_DIR' && '$home/.tfenv/bin/tfenv' install"
  fi
}

# ---------------------------------------------------------------------------
# OCI CLI, for PRIMARY_USER, installed as a uv tool per CLAUDE.md. Requires
# ensure_uv to have run. Auth (~/.oci/config, API key) is a secret and can't
# be provisioned here — set it up interactively as PRIMARY_USER afterwards.
# ---------------------------------------------------------------------------
ensure_oci_cli() {
  local home=/home/$PRIMARY_USER
  sudo -u "$PRIMARY_USER" bash -c "\"$home/.local/bin/uv\" tool install oci-cli"
}

# ---------------------------------------------------------------------------
# Primary login user — the account for day-to-day work, and the one that owns
# the Claude Code install below.
#
# 'opc' (the OL9 cloud-init default) is deliberately NOT retired. It's the only
# user guaranteed to exist before this script has run, it's where Terraform's
# metadata.ssh_authorized_keys lands, and the bastion targets it — so it stays
# as the bootstrap and break-glass account for when provisioning fails and
# PRIMARY_USER doesn't exist yet.
#
# wheel gives passwordless sudo, matching the OL9 default for opc. The docker
# group is created by the docker-ce package, so ensure_packages runs first.
#
# The key is embedded here rather than copied from opc's authorized_keys — the
# bastion injects short-lived managed-SSH session keys into that file, and
# copying would bake expired session keys into this account.
# ---------------------------------------------------------------------------
PRIMARY_USER=marc
PRIMARY_USER_SSH_KEY='ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC8O7HNu58AvTYz7fw6ZaRkpP5nnIofdczKIS5fKhzswqTEa5ovIFl/5QDegU/E5F3noVxoFZ4lp/BjSCUFEBQZfok9R/+fxfDMKk2DcWuPpWmhw6/bl47WLCBM3qxKUD59pSqMbKqZ05lO0EOszhW33yfuLjPhMBE2YtyAhBcMzmrkTNG9+9FLG4obSQLx/G4Zbg3hUEkp/tzzXyaJmc6hyl+M16WmOVI72wV1egEDVHIyMvzUVOmgJzC08jXJhqf0DGZPz7/1KPTN9OtZtQf4ArbhQrnZimyvsi0T1csltrvIErNVaBYl/nmoR0rZhOo6a6JNdh0f5GyLfADRA2A1 marc'

ensure_primary_user() {
  id -u "$PRIMARY_USER" >/dev/null 2>&1 || useradd -m -s /bin/bash "$PRIMARY_USER"

  # Group membership is additive and checked, so re-runs are no-ops.
  id -nG "$PRIMARY_USER" | grep -qw wheel || usermod -aG wheel "$PRIMARY_USER"
  id -nG "$PRIMARY_USER" | grep -qw docker || usermod -aG docker "$PRIMARY_USER"

  # Unquoted heredoc: $PRIMARY_USER must expand.
  install -m 0440 /dev/stdin "/etc/sudoers.d/90-$PRIMARY_USER" <<EOF
$PRIMARY_USER ALL=(ALL) NOPASSWD:ALL
EOF

  local home=/home/$PRIMARY_USER
  install -d -m 0700 -o "$PRIMARY_USER" -g "$PRIMARY_USER" "$home/.ssh"
  # Rewritten wholesale each run: the key above is the source of truth, so a
  # key added by hand on the box will be reverted on the next push.
  printf '%s\n' "$PRIMARY_USER_SSH_KEY" > "$home/.ssh/authorized_keys"
  chown "$PRIMARY_USER:$PRIMARY_USER" "$home/.ssh/authorized_keys"
  chmod 0600 "$home/.ssh/authorized_keys"
}

# ---------------------------------------------------------------------------
# Restic backup/check cron jobs (see restic/restic.cron), copied verbatim into
# /etc/cron.d so cronie picks it up automatically — no daemon reload needed.
# ---------------------------------------------------------------------------
ensure_restic_cron() {
  install -m 0644 "$THEWAVE_INFRA_DIR/restic/restic.cron" /etc/cron.d/restic
}

# ---------------------------------------------------------------------------
# PRIMARY_USER's crontab (price scraper, ML forecast/retrain, dashboard
# snapshot), loaded verbatim from infra/crontab. Unlike restic.cron above,
# this has no per-line user field, so it's a user crontab (`crontab -u`), not
# a /etc/cron.d drop-in. `crontab -u` replaces the whole table each run, so
# this file is the single source of truth — edits made directly on the box
# via `crontab -e` would be reverted on the next push.
# ---------------------------------------------------------------------------
ensure_primary_user_crontab() {
  crontab -u "$PRIMARY_USER" "$THEWAVE_INFRA_DIR/crontab"
}

# ---------------------------------------------------------------------------
# Add new install/config steps below as idempotent functions, then call them
# from main(). Keep each one safe to re-run.
# ---------------------------------------------------------------------------

main() {
  ensure_repos
  ensure_packages
  ensure_docker
  ensure_primary_user
  ensure_compose_shim
  ensure_tailscale
  ensure_claude
  ensure_uv
  ensure_tfenv
  ensure_oci_cli
  ensure_restic_cron
  ensure_primary_user_crontab
  echo "=== provision.sh complete $(date -u +%FT%TZ) ==="
}

main "$@"
