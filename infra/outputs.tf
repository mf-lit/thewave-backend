output "region" {
  description = "Region the resources live in (used by push-provision.sh)."
  value       = var.region
}

output "compartment_id" {
  description = "OCID of the project compartment."
  value       = oci_identity_compartment.project.id
}

output "instance_id" {
  description = "OCID of the Ampere instance (used as the bastion session target)."
  value       = oci_core_instance.main.id
}

output "instance_private_ip" {
  description = "Private IP of the instance."
  value       = oci_core_instance.main.private_ip
}

output "bastion_id" {
  description = "OCID of the bastion."
  value       = oci_bastion_bastion.main.id
}

# Ready-to-run command that opens a managed-SSH session to the instance.
# Fill in the local SSH keys; the public key is added to the session, the
# private key authenticates. Then follow the ssh command it prints.
output "connect_hint" {
  description = "How to open a bastion session and SSH in."
  value       = <<-EOT
    # Use an ed25519 key — OCI Bastion rejects RSA keys on modern OpenSSH.
    #   ssh-keygen -t ed25519 -f ~/.ssh/oci_thewave   # once

    # 'marc' is the primary user, created by provision.sh. If provisioning has
    # never completed on this box, marc won't exist yet — swap both occurrences
    # below for 'opc', the cloud-init default / break-glass account.

    # 1) Open a managed-SSH session and capture its OCID (session, not work-req):
    SID=$(oci bastion session create-managed-ssh \
      --bastion-id ${oci_bastion_bastion.main.id} \
      --target-resource-id ${oci_core_instance.main.id} \
      --target-os-username marc \
      --ssh-public-key-file ~/.ssh/oci_thewave.pub \
      --session-ttl 10800 --query 'data.id' --raw-output)

    # 2) Wait until it reports ACTIVE:
    oci bastion session get --session-id "$SID" --query 'data."lifecycle-state"' --raw-output

    # 3) SSH in through the bastion:
    ssh -i ~/.ssh/oci_thewave \
      -o ProxyCommand="ssh -i ~/.ssh/oci_thewave -W %h:%p -p 22 $SID@host.bastion.${var.region}.oci.oraclecloud.com" \
      marc@${oci_core_instance.main.private_ip}
  EOT
}
