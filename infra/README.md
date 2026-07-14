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

`terraform output connect_hint` prints the exact commands. In short:

```sh
# Open a managed-SSH session (valid up to 3h)
oci bastion session create-managed-ssh \
  --bastion-id "$(terraform output -raw bastion_id)" \
  --target-resource-id "$(terraform output -raw instance_id)" \
  --target-os-username opc \
  --ssh-public-key-file ~/.ssh/id_ed25519.pub \
  --session-ttl 10800 --wait-for-state SUCCEEDED

# Fetch the session's ready-made ssh command and connect
#   oci bastion session get --session-id <OCID> \
#     --query 'data."ssh-metadata".command' --raw-output
# Replace <privateKey> with ~/.ssh/id_ed25519, then run it.
```

## Verify

- On the instance: `curl -sS ifconfig.me` → returns a public IP (NAT egress works).
- `sudo dnf check-update` → package metadata downloads (Service Gateway / NAT).
- `terraform output instance_private_ip` is a `10.0.1.x` address; the instance has **no** public IP.

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
| `bastion.tf` | OCI Bastion service |
| `outputs.tf` | IPs, OCIDs, connect hint |
