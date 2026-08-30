# Three permissions on the API token, and one resource scope that is easy to
# miss:
#
#   Zone    > WAF              > Edit   the custom rules and the rate-limit rule
#   Zone    > Cache Rules      > Edit   the cache rules
#   Account > Cloudflare Tunnel> Edit   the tunnel ingress config (tunnel.tf)
#
# The last one also needs the token's Account Resources to include the account.
# That is a separate section of the token editor from Zone Resources, and a
# token that leaves it empty reports zero accounts and 403s on every tunnel
# endpoint no matter which permissions it holds. Nothing here needs DNS.
#
# WAF, not Firewall Services, despite the name — this cost an afternoon once.
# The rulesets API authorises per phase, after it has parsed the request body:
# a token holding Firewall Services:Edit gets a bare 403 "request is not
# authorized" on create, with no hint of which permission it wanted. Both
# http_request_firewall_custom and http_ratelimit require "Zone WAF Write":
#   https://developers.cloudflare.com/terraform/additional-configurations/waf-custom-rules/
#   https://developers.cloudflare.com/terraform/additional-configurations/rate-limiting-rules/
provider "cloudflare" {
  api_token = var.cloudflare_api_token
}
