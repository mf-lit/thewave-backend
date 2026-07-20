# Authentication uses the OCI CLI config file (~/.oci/config) by default — the
# cleanest option for a brand-new tenancy set up via `oci setup config`.
#
# To use explicit API-key auth instead, comment out `config_file_profile` below
# and uncomment the tenancy_ocid / user_ocid / fingerprint / private_key_path
# arguments (their variables are declared in variables.tf).
provider "oci" {
  region              = var.region
  config_file_profile = var.oci_profile

  # tenancy_ocid = var.tenancy_ocid
  # user_ocid    = var.user_ocid
  # fingerprint  = var.fingerprint
  # private_key_path = var.private_key_path
}
