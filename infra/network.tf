# All OCI service CIDR labels in this region (used by the Service Gateway).
data "oci_core_services" "all" {
  filter {
    name   = "name"
    values = ["All .* Services In Oracle Services Network"]
    regex  = true
  }
}

# ---------------------------------------------------------------------------
# VCN
# ---------------------------------------------------------------------------
resource "oci_core_vcn" "main" {
  compartment_id = oci_identity_compartment.project.id
  cidr_blocks    = [var.vcn_cidr]
  display_name   = "${var.project_name}-vcn"
  dns_label      = "thewave"
}

# ---------------------------------------------------------------------------
# Gateways
# ---------------------------------------------------------------------------

# Outbound internet access for the private subnet.
resource "oci_core_nat_gateway" "nat" {
  compartment_id = oci_identity_compartment.project.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${var.project_name}-nat"
}

# Private path to OCI services (Object Storage, YUM, etc.) — keeps that traffic
# off the NAT path.
resource "oci_core_service_gateway" "svc" {
  compartment_id = oci_identity_compartment.project.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${var.project_name}-svc"

  services {
    service_id = data.oci_core_services.all.services[0].id
  }
}

# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
resource "oci_core_route_table" "private" {
  compartment_id = oci_identity_compartment.project.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${var.project_name}-private-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_nat_gateway.nat.id
  }

  route_rules {
    destination       = data.oci_core_services.all.services[0].cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.svc.id
  }
}

# ---------------------------------------------------------------------------
# Security list — no public ingress; SSH only from within the subnet (the
# bastion is provisioned into this subnet). Egress is open.
# ---------------------------------------------------------------------------
resource "oci_core_security_list" "private" {
  compartment_id = oci_identity_compartment.project.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${var.project_name}-private-sl"

  egress_security_rules {
    destination      = "0.0.0.0/0"
    destination_type = "CIDR_BLOCK"
    protocol         = "all"
  }

  # SSH from the bastion (which lives in this subnet).
  ingress_security_rules {
    source      = var.subnet_cidr
    source_type = "CIDR_BLOCK"
    protocol    = "6" # TCP
    tcp_options {
      min = 22
      max = 22
    }
  }
}

# ---------------------------------------------------------------------------
# Private subnet — no public IPs allowed.
# ---------------------------------------------------------------------------
resource "oci_core_subnet" "private" {
  compartment_id             = oci_identity_compartment.project.id
  vcn_id                     = oci_core_vcn.main.id
  cidr_block                 = var.subnet_cidr
  display_name               = "${var.project_name}-private-subnet"
  dns_label                  = "private"
  route_table_id             = oci_core_route_table.private.id
  security_list_ids          = [oci_core_security_list.private.id]
  prohibit_public_ip_on_vnic = true
}
