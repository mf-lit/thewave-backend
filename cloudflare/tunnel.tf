# The ingress rules for the cloudflared tunnel, which is what actually routes
# each public hostname to a container. The tunnel is remotely-managed: the
# container runs `tunnel run` with a token and no local config file, so these
# rules live in Cloudflare and are the only copy of them.
#
# Only the configuration is managed here, not the tunnel object. Terraform's
# tunnel resource requires the tunnel secret - the same secret carried inside
# CLOUDFLARE_TUNNEL_TOKEN in the compose .env - and the API never returns it on
# read, which invites a permanent diff and, eventually, a replacement. Replacing
# the tunnel would invalidate the token the running container authenticates
# with and take every hostname below down at once. Managing the config alone
# needs no secret and cannot recreate anything.
#
# Reading and writing these needs Account > Cloudflare Tunnel > Edit, and the
# token's Account Resources must include the account - a token scoped only to
# the zone reports zero accounts and 403s here, whatever its permissions say.
#
# Order matters: cloudflared takes the first matching rule, so the catch-all
# must stay last. Terraform sends the whole list on every apply, so an edit
# here rewrites the live ingress wholesale rather than patching it.
resource "cloudflare_zero_trust_tunnel_cloudflared_config" "wave" {
  account_id = var.cloudflare_account_id
  tunnel_id  = var.cloudflare_tunnel_id

  config {
    ingress_rule {
      hostname = "wave-notifications.vq5.net"
      service  = "http://thewave-notifications:5001"
    }

    ingress_rule {
      hostname = "wave-api.vq5.net"
      service  = "http://thewave-upstream-api:5000"
    }

    ingress_rule {
      hostname = "waveform.vq5.net"
      service  = "http://thewave-webapp:80"
    }

    # Required terminator: anything not matched above gets a 404 from
    # cloudflared rather than reaching a container.
    ingress_rule {
      service = "http_status:404"
    }
  }
}
