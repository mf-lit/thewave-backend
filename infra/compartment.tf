# Dedicated compartment for the project — keeps the tenancy tidy and makes
# teardown clean. Everything else lives inside it.
resource "oci_identity_compartment" "project" {
  compartment_id = var.tenancy_ocid
  name           = var.project_name
  description    = "${var.project_name} — Always-Free Ampere infrastructure"
  enable_delete  = true
}
