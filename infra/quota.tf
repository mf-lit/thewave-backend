# Cost guardrails — keep this project inside the Always-Free envelope even on a
# Pay-As-You-Go account.
#
#   1. Compartment quota (hard block): deny ALL compute, then re-allow only
#      free-tier Ampere A1 up to 4 OCPU, and cap block storage at the free
#      200 GB. Because it starts from a family-wide `zero`, new/paid shapes
#      Oracle adds later stay blocked automatically.
#   2. Budget alert (tripwire): emails on the first real spend, catching
#      anything the quota can't (see below): memory overage, load balancers,
#      databases, egress, etc.
#
# Note: compute-core quotas cap OCPU cores but NOT memory — A1.Flex allows up
# to 64 GB/OCPU, so 4 cores could technically request paid RAM beyond the free
# 24 GB. There is no memory quota name for compute-core (verified against the
# API), so that edge case is covered by the budget alert rather than the quota.
#
# Quotas live in the tenancy (root) compartment but target the project
# compartment by name.

resource "oci_limits_quota" "guardrails" {
  compartment_id = var.tenancy_ocid
  name           = "${var.project_name}-always-free-only"
  description    = "Restrict ${var.project_name} to Always-Free resources"

  statements = [
    # Compute: block everything, then allow only free-tier A1 cores (max 4).
    "zero compute-core quotas in compartment ${oci_identity_compartment.project.name}",
    "set compute-core quota standard-a1-core-count to 4 in compartment ${oci_identity_compartment.project.name}",
    # Block storage: cap total (boot + volumes) at the free 200 GB.
    "set block-storage quota total-storage-gb to 200 in compartment ${oci_identity_compartment.project.name}",
  ]
}

# Budget scoped to the project compartment.
resource "oci_budget_budget" "project" {
  compartment_id = var.tenancy_ocid # budgets are created in the tenancy root
  target_type    = "COMPARTMENT"
  targets        = [oci_identity_compartment.project.id]
  amount         = var.monthly_budget_amount
  reset_period   = "MONTHLY"
  display_name   = "${var.project_name}-budget"
  description    = "Spend tripwire for the ${var.project_name} compartment"
}

# Fire an email as soon as actual spend crosses ~1% of the budget (i.e. the
# first real charge). Anything non-free trips this.
resource "oci_budget_alert_rule" "any_spend" {
  budget_id      = oci_budget_budget.project.id
  display_name   = "${var.project_name}-any-spend"
  type           = "ACTUAL"
  threshold_type = "PERCENTAGE"
  threshold      = 1
  recipients     = var.alert_email
  message        = "Spend detected in the ${var.project_name} OCI compartment — something outside Always-Free is running."
}
