variable "cloudflare_api_token" {
  description = "Scoped Cloudflare API token, restricted to the zone below. Needs Zone > WAF > Edit and Zone > Cache Rules > Edit; see provider.tf."
  type        = string
  sensitive   = true
}

variable "cloudflare_zone_id" {
  description = "Zone ID for vq5.net (Cloudflare dashboard > vq5.net > Overview, right sidebar)."
  type        = string
}

variable "cloudflare_account_id" {
  description = "Account ID owning the tunnel (Zero Trust > Settings, or the \"a\" field of CLOUDFLARE_TUNNEL_TOKEN in the compose .env)."
  type        = string
}

variable "cloudflare_tunnel_id" {
  description = "ID of the cloudflared tunnel whose ingress rules this manages (the \"t\" field of the same token). The tunnel object itself is deliberately not a Terraform resource; see tunnel.tf."
  type        = string
}

variable "notifications_hostnames" {
  description = "Public hostnames the tunnel routes to notifications-api. A list because there was briefly a second origin; kept as one so another can be added without reshaping the rule expression."
  type        = list(string)
  default     = ["wave-notifications.vq5.net"]
}

variable "messages_hostnames" {
  description = "Public hostnames the tunnel routes to messages-api. A list for the same reason as notifications_hostnames above. Note that /admin/* is deliberately absent from the rule these feed: the admin surface is served only over the docker network."
  type        = list(string)
  default     = ["wave-messages.vq5.net"]
}

variable "upstream_api_hostnames" {
  description = "Public hostnames the tunnel routes to upstream-api. A list for the same reason as notifications_hostnames above."
  type        = list(string)
  default     = ["wave-api.vq5.net"]
}

variable "webapp_hostname" {
  description = "Public hostname the tunnel routes to the Flutter web app (nginx). Only its /api/* surface is constrained here; the rest of the host serves arbitrary static paths."
  type        = string
  default     = "waveform.vq5.net"
}

variable "rule_action" {
  description = "Action for all three rules. Not \"log\": that action is Enterprise-only, and this zone is on Free. There is therefore no observe-then-enforce warm-up available here - the rules enforce from the first apply. They only match paths outside each API's known routes, which our own clients never request, so there is little to observe anyway."
  type        = string
  default     = "block"

  validation {
    condition     = contains(["block", "managed_challenge"], var.rule_action)
    error_message = "rule_action must be \"block\" or \"managed_challenge\". Cloudflare rejects \"log\" on plans below Enterprise."
  }
}

variable "api_rate_limit_per_10s" {
  description = "Requests per 10 seconds per IP allowed to the webapp's /api/* paths before the rate-limit rule fires. Ten seconds because Free plans support no other counting period, and counting is per IP per PoP because Cloudflare requires cf.colo.id as a characteristic. Deliberately not expressed per minute: a 10s window is a burst limit, and dividing a per-minute figure by six would describe it wrongly. Generous for a person browsing the schedule (a page load costs two requests, each date change one); tight for a script."
  type        = number
  default     = 7
}
