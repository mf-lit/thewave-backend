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
    # 1) Open a managed-SSH session (valid up to 3h):
    oci bastion session create-managed-ssh \
      --bastion-id ${oci_bastion_bastion.main.id} \
      --target-resource-id ${oci_core_instance.main.id} \
      --target-os-username opc \
      --ssh-public-key-file ${var.ssh_public_key_path} \
      --session-ttl 10800 --wait-for-state SUCCEEDED

    # 2) Grab the session's SSH command:
    #    oci bastion session get --session-id <OCID> \
    #      --query 'data."ssh-metadata".command' --raw-output
    #    Replace <privateKey> in it with your private key path, then run it.
    #    It resolves to:
    #    ssh -i <private_key> -o ProxyCommand="ssh -i <private_key> -W ${oci_core_instance.main.private_ip}:22 -p 22 <session-ocid>@host.bastion.${var.region}.oci.oraclecloud.com" opc@${oci_core_instance.main.private_ip}
  EOT
}
