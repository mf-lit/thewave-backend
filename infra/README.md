# thewave — OCI Always-Free Ampere infrastructure

Terraform for a single **Always-Free Ampere A1** instance (2 OCPU / 8 GB, Oracle Linux 9) on a
**private** subnet with outbound internet via a NAT gateway. The **only** inbound path is SSH,
brokered by the managed **OCI Bastion** service — nothing is exposed to the internet directly.

```
Internet ──(egress)──► NAT GW ──► private subnet ──► Ampere A1 (no public IP)
                                        ▲
You ──► OCI Bastion service ────────────┘  (managed SSH session, key-only, ≤3h)
```

## Prerequisites (brand-new tenancy)

1. **tfenv + Terraform**
   ```sh
   git clone https://github.com/tfutils/tfenv ~/.tfenv
   export PATH="$HOME/.tfenv/bin:$PATH"   # add to your shell profile
   tfenv install                          # reads .terraform-version
   ```
2. **OCI CLI** (for API-key setup + bastion sessions)
   ```sh
   bash -c "$(curl -L https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)"
   ```
3. **Authenticate**
   ```sh
   oci setup config     # generates ~/.oci/config + an API keypair
   ```
   Upload the printed public key under **Identity → My profile → API keys** in the OCI Console.
4. **SSH key** — ensure `~/.ssh/id_ed25519.pub` exists (or point `ssh_public_key_path` elsewhere).

## Deploy

```sh
cp terraform.tfvars.example terraform.tfvars   # fill in region + tenancy_ocid
terraform init
terraform plan
terraform apply
```

> **Ampere capacity:** the free A1 pool is region/AD-dependent. An `Out of host capacity` error on
> apply is a capacity issue, not a config bug — re-run `apply`, or try another availability domain /
> region.

## Connect

`terraform output connect_hint` prints the exact commands.

> **Use an ed25519 key.** OCI Bastion rejects RSA keys on modern OpenSSH (the bastion accepts the
> public key but the signature fails), so generate one for OCI access: `ssh-keygen -t ed25519 -f
> ~/.ssh/oci_thewave`. Managed-SSH sessions inject this key into the target, so the instance's
> baked-in key is not involved and no instance change is needed.

```sh
# 1) Open a session and capture its OCID (data.id is the session, not the work request)
SID=$(oci bastion session create-managed-ssh \
  --bastion-id "$(terraform output -raw bastion_id)" \
  --target-resource-id "$(terraform output -raw instance_id)" \
  --target-os-username opc --ssh-public-key-file ~/.ssh/oci_thewave.pub \
  --session-ttl 10800 --query 'data.id' --raw-output)

# 2) Wait until ACTIVE, then 3) SSH in
oci bastion session get --session-id "$SID" --query 'data."lifecycle-state"' --raw-output
ssh -i ~/.ssh/oci_thewave \
  -o ProxyCommand="ssh -i ~/.ssh/oci_thewave -W %h:%p -p 22 $SID@host.bastion.$(terraform output -raw region).oci.oraclecloud.com" \
  opc@"$(terraform output -raw instance_private_ip)"
```

## Cost guardrails (`quota.tf`)

To stay inside Always-Free even on a Pay-As-You-Go account:

- **Compartment quota** — denies *all* compute, then re-allows only free-tier A1
  (max 4 OCPU) and caps block storage at the free 200 GB. Starting from a
  family-wide `zero` means paid shapes Oracle adds later stay blocked too.
- **Budget + alert** — a small monthly budget (`monthly_budget_amount`, default 1)
  that emails `alert_email` on the first real spend — the catch-all for anything
  the quota can't cap (notably A1 *memory* overage, load balancers, databases).

These are independent of the instance, so apply them before upgrading to PAYG:

```sh
terraform apply -target=oci_limits_quota.guardrails \
                -target=oci_budget_budget.project \
                -target=oci_budget_alert_rule.any_spend
```

## Provisioning (`provision.sh` + cloud-init launcher)

All install/config logic lives in **`provision.sh`** — an idempotent shell script (safe to run any
number of times). `cloud-init.yaml.tftpl` is just a thin launcher that Terraform renders with the
script embedded, so cloud-init writes `provision.sh` to `/opt/thewave/` and runs it once on first
boot. `provision.sh` is the single source of truth; there is only one copy.

What it installs (Ubuntu/apt names mapped to OL9/dnf):

- **Docker CE** (`docker-ce`, CLI, `containerd.io`, buildx + compose plugins) plus a `docker-compose`
  shim over `docker compose`. Docker is enabled and `opc` is added to the `docker` group.
- **EPEL + CodeReady Builder (CRB)** enabled for `pv`, `pwgen`, `whois`, `p7zip`, `moreutils`
  (`moreutils` needs a CRB dependency — without CRB the whole atomic dnf transaction fails).
- `vim-enhanced`, `git`, `ca-certificates`, `gnupg2` (gpg-agent), `jq`, `gcc`, `make`, `zip`.
- `curl` swapped in for the preinstalled `curl-minimal`.

### Iterating without rebooting/replacing

Edit `provision.sh`, then push it to the running box and re-run it:

```sh
./push-provision.sh          # opens a bastion session, copies + runs provision.sh
```

The instance ignores `user_data` changes (`lifecycle.ignore_changes`), so editing the script never
threatens the live instance or triggers a capacity-lottery replacement — while a genuinely new
instance still bakes in the current script. Add new steps as idempotent functions in `provision.sh`.

## Verify

- On the instance: `curl -sS ifconfig.me` → returns a public IP (NAT egress works).
- `sudo dnf check-update` → package metadata downloads (Service Gateway / NAT).
- `terraform output instance_private_ip` is a `10.0.1.x` address; the instance has **no** public IP.
- `docker run --rm hello-world` as `opc` (no sudo) → Docker installed and group applied.
- Provisioning log on the box: `/var/log/thewave-provision.log`.

## Teardown

```sh
terraform destroy
```

## Layout

| File | Purpose |
|------|---------|
| `versions.tf` | Terraform + `oracle/oci` provider constraints |
| `provider.tf` | Provider auth (config-file profile by default) |
| `variables.tf` / `terraform.tfvars.example` | Inputs |
| `compartment.tf` | Dedicated project compartment |
| `network.tf` | VCN, NAT + Service gateways, route table, security list, private subnet |
| `compute.tf` | Ampere A1.Flex instance + OL9 image lookup |
| `provision.sh` | Idempotent install/config script (single source of truth) |
| `cloud-init.yaml.tftpl` | Thin launcher: embeds + runs provision.sh on first boot |
| `push-provision.sh` | Copies provision.sh to the running box via the bastion and runs it |
| `bastion.tf` | OCI Bastion service |
| `quota.tf` | Cost guardrails — Always-Free quota + budget alert |
| `outputs.tf` | IPs, OCIDs, connect hint |
