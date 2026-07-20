# ---------------------------------------------------------------------------
# Authentication / provider
# ---------------------------------------------------------------------------

variable "region" {
  description = "OCI region for all resources (your tenancy's home region), e.g. uk-london-1."
  type        = string
}

variable "oci_profile" {
  description = "Profile name in ~/.oci/config to authenticate with."
  type        = string
  default     = "DEFAULT"
}

variable "tenancy_ocid" {
  description = "Tenancy OCID. Used as the parent of the project compartment and for AD lookup."
  type        = string
}

# Only needed if you switch provider.tf to explicit API-key auth.
variable "user_ocid" {
  description = "User OCID (only for explicit-key auth)."
  type        = string
  default     = ""
}

variable "fingerprint" {
  description = "API key fingerprint (only for explicit-key auth)."
  type        = string
  default     = ""
}

variable "private_key_path" {
  description = "Path to the API private key (only for explicit-key auth)."
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

variable "project_name" {
  description = "Name prefix applied to all resources."
  type        = string
  default     = "thewave"
}

# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------

variable "instance_ocpus" {
  description = "OCPUs for the Ampere A1.Flex instance (Always-Free allows up to 4)."
  type        = number
  default     = 2
}

variable "instance_memory_gbs" {
  description = "Memory (GB) for the Ampere A1.Flex instance (Always-Free allows up to 24)."
  type        = number
  default     = 8
}

variable "boot_volume_gbs" {
  description = "Boot volume size in GB (Always-Free block storage totals 200 GB)."
  type        = number
  default     = 50
}

variable "availability_domain" {
  description = "AD to launch the instance in. Empty = first AD. apply-until-capacity.sh cycles this across all ADs to find free A1 capacity, then pins the winning AD in terraform.tfvars."
  type        = string
  default     = ""
}

variable "ssh_public_key_path" {
  description = "Path to the SSH public key injected into the instance's authorized_keys."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

# ---------------------------------------------------------------------------
# Cost guardrails
# ---------------------------------------------------------------------------

variable "alert_email" {
  description = "Email address that receives budget spend alerts."
  type        = string
  default     = "marc@vq5.net"
}

variable "monthly_budget_amount" {
  description = "Monthly budget in the account's billing currency (GBP/USD). The alert fires on the first real spend against it."
  type        = number
  default     = 1
}

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------

variable "vcn_cidr" {
  description = "CIDR block for the VCN."
  type        = string
  default     = "10.0.0.0/16"
}

variable "subnet_cidr" {
  description = "CIDR block for the private subnet."
  type        = string
  default     = "10.0.1.0/24"
}

variable "bastion_client_cidr" {
  description = "CIDRs allowed to open sessions on the bastion. SSH remains key-only."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}
