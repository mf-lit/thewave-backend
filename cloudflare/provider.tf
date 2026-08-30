# Scope the API token to this zone only — it needs no account-wide or DNS
# access. Two permissions are required, not one:
#
#   Zone > WAF         > Edit   the custom rules and the rate-limit rule
#   Zone > Cache Rules > Edit   the cache rules
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
