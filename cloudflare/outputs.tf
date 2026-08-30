output "ruleset_id" {
  description = "ID of the custom firewall ruleset (view its match log under Security > Events)."
  value       = cloudflare_ruleset.block_invalid_api_paths.id
}

output "webapp_cache_ruleset_id" {
  description = "ID of the cache-settings ruleset for the web app host, covering both its static bundle and its API paths."
  value       = cloudflare_ruleset.webapp_cache.id
}

output "webapp_api_ratelimit_ruleset_id" {
  description = "ID of the rate-limit ruleset for the web app's API paths."
  value       = cloudflare_ruleset.webapp_api_ratelimit.id
}
