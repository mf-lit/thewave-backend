data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

# Newest Oracle Linux 9 image that supports the Ampere A1.Flex shape.
data "oci_core_images" "ol9" {
  compartment_id           = oci_identity_compartment.project.id
  operating_system         = "Oracle Linux"
  operating_system_version = "9"
  shape                    = "VM.Standard.A1.Flex"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

resource "oci_core_instance" "main" {
  compartment_id      = oci_identity_compartment.project.id
  availability_domain = var.availability_domain != "" ? var.availability_domain : data.oci_identity_availability_domains.ads.availability_domains[0].name
  display_name        = "${var.project_name}-ampere"
  shape               = "VM.Standard.A1.Flex"

  shape_config {
    ocpus         = var.instance_ocpus
    memory_in_gbs = var.instance_memory_gbs
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.private.id
    assign_public_ip = false # private only — reachable through the bastion
  }

  source_details {
    source_type             = "image"
    source_id               = data.oci_core_images.ol9.images[0].id
    boot_volume_size_in_gbs = var.boot_volume_gbs
  }

  metadata = {
    ssh_authorized_keys = file(pathexpand(var.ssh_public_key_path))
    user_data           = base64encode(file("${path.module}/cloud-init.yaml"))
  }

  # Enable the Oracle Cloud Agent Bastion plugin so the managed bastion can
  # broker SSH sessions to this (public-IP-less) instance.
  agent_config {
    are_all_plugins_disabled = false
    is_management_disabled   = false
    is_monitoring_disabled   = false

    plugins_config {
      name          = "Bastion"
      desired_state = "ENABLED"
    }
  }
}
