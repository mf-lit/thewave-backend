# Managed OCI Bastion service — the only inbound path. Individual SSH sessions
# are ephemeral (max 3h) and created on demand at connect time; see README.
resource "oci_bastion_bastion" "main" {
  bastion_type                 = "standard"
  compartment_id               = oci_identity_compartment.project.id
  target_subnet_id             = oci_core_subnet.private.id
  client_cidr_block_allow_list = var.bastion_client_cidr
  name                         = "${var.project_name}-bastion"
}
