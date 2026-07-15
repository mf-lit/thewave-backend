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
  p7zip p7zip-plugins gcc make zip moreutils
  # Tailscale mesh VPN (join the tailnet manually with `tailscale up --ssh`)
  tailscale
)

ensure_packages() {
  dnf -y install "${PACKAGES[@]}"
  # Swap the preinstalled curl-minimal for full curl (no-op once done).
  dnf -y --allowerasing install curl
}

# ---------------------------------------------------------------------------
# Docker daemon + non-root access for the default 'opc' user
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
# Claude Code CLI, installed for the login user (opc) via the official native
# installer (self-contained binary, no Node needed). Idempotent: skip if it's
# already present — the binary self-updates, so re-running only reinstalls.
# The installer drops it in ~opc/.local/bin and adds that to opc's shell PATH.
# ---------------------------------------------------------------------------
ensure_claude() {
  # Make ~/.local/bin reachable on login (the installer warns but won't edit an
  # existing .bashrc). grep-guard keeps it to a single line across re-runs.
  local rc=/home/opc/.bashrc
  sudo -u opc grep -qxF 'export PATH="$HOME/.local/bin:$PATH"' "$rc" 2>/dev/null \
    || echo 'export PATH="$HOME/.local/bin:$PATH"' | sudo -u opc tee -a "$rc" >/dev/null

  sudo -u opc test -x /home/opc/.local/bin/claude && return 0
  sudo -u opc bash -c 'curl -fsSL https://claude.ai/install.sh | bash'
}

# ---------------------------------------------------------------------------
# Add new install/config steps below as idempotent functions, then call them
# from main(). Keep each one safe to re-run.
# ---------------------------------------------------------------------------

main() {
  ensure_repos
  ensure_packages
  ensure_docker
  ensure_compose_shim
  ensure_tailscale
  ensure_claude
  echo "=== provision.sh complete $(date -u +%FT%TZ) ==="
}

main "$@"
