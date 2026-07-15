# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Terraform for a single **free-tier OCI Ampere A1** instance (2 OCPU / 8 GB, Oracle Linux 9),
private (no public IP), reachable only via the managed **OCI Bastion** service, with outbound
internet through a NAT gateway. Deployed and live in **uk-london-1**.

This is its **own nested git repo** (`git init`, no remote) inside `thewave/`, which is gitignored
in the parent `server-setup` repo. `terraform.tfvars` and state are gitignored and local — never
commit them.

## Tooling / environment

Neither tool is on the default PATH; prefix commands or export it:

```sh
export PATH="$HOME/.tfenv/bin:$HOME/.local/bin:$PATH"
```

- **Terraform** via tfenv, pinned by `.terraform-version` (`tfenv install` to match).
- **OCI CLI** installed as a uv tool (`uv tool install oci-cli`); auth is `~/.oci/config` (profile
  `DEFAULT`). `terraform.tfvars` (region, tenancy OCID) is derived from that config.

## Common commands

```sh
terraform fmt && terraform validate
terraform plan
terraform apply                       # creates real OCI resources

# Cost guardrails are independent of the instance — apply before enabling PAYG:
terraform apply -target=oci_limits_quota.guardrails \
                -target=oci_budget_budget.project -target=oci_budget_alert_rule.any_spend

./apply-until-capacity.sh             # retry apply across all ADs until A1 capacity is free (tmux)
./push-provision.sh                   # iterate provisioning on the LIVE box (see below)
terraform output connect_hint         # how to SSH in via the bastion
```

There is no test suite. "Verifying a change works" means driving the real thing: `terraform plan`
for config, and for provisioning, run `./push-provision.sh` and inspect the box
(`/var/log/thewave-provision.log`, `docker --version`, etc.).

## Architecture / the important mental model

**Provisioning is decoupled from Terraform.** All install/config lives in the idempotent
`provision.sh` (the single source of truth). `cloud-init.yaml.tftpl` is a thin launcher that
Terraform renders via `templatefile()` with the script embedded; cloud-init writes it to
`/opt/thewave/provision.sh` and runs it once on first boot.

- The instance has `lifecycle { ignore_changes = [metadata["user_data"]] }`. This is deliberate:
  editing `provision.sh` (or the launcher) shows **no change** in `terraform plan` and will **not**
  update the running box. To apply provisioning changes to the live instance, use
  **`./push-provision.sh`**, not `terraform apply`. New instances still bake in the current script.
- Extend provisioning by adding **idempotent** functions to `provision.sh` and calling them from
  `main()`. Keep every step safe to run N times.

**Changing `user_data` is the only ignored metadata — other instance changes still force
replacement.** Editing `ssh_authorized_keys`, shape, image, subnet, `availability_domain`, etc.
replaces the instance, which throws you back into the A1 capacity lottery. Avoid unless intended.

## Sharp edges (these cost real time — don't relearn them)

- **OCI Bastion rejects RSA SSH keys** on modern OpenSSH: it accepts the public key then fails the
  signature ("Permission denied (publickey)" after "Server accepts key"). **Use an ed25519 key.**
  Managed-SSH sessions inject the session key into the target, so the instance's baked-in key is
  irrelevant to connectivity. `push-provision.sh` generates an ephemeral ed25519 key per run.
- **Bastion session id:** `oci bastion session create-managed-ssh --wait-for-state SUCCEEDED`
  returns the *work-request* OCID, not the session. Instead capture `data.id` (the session OCID)
  and poll `data."lifecycle-state"` until `ACTIVE`.
- **`moreutils` needs the CodeReady Builder repo** (`ol9_codeready_builder`) for `perl(IPC::Run)`.
  Because dnf installs atomically, one unresolved dep fails the whole transaction (this silently
  broke the original cloud-init and left docker uninstalled). `provision.sh` enables EPEL **+ CRB**.
- **A1 "Out of host capacity"** on apply is a capacity issue, not a config bug. London has 3 ADs;
  `apply-until-capacity.sh` cycles all of them. Pay-As-You-Go removes the capacity deprioritization.

## Cost guardrails (`quota.tf`)

Keeps the account inside Always-Free even on PAYG. The compartment quota does
`zero compute-core quotas` then re-allows **only** free-tier A1 — and must set **both**
`standard-a1-core-count` and `standard-a1-core-regional-count` (launch checks both). Block storage
is capped at the free 200 GB. compute-core quotas **cannot** cap flex memory, so A1 memory overage
is covered by the budget alert (fires on the first real spend) rather than the quota.
