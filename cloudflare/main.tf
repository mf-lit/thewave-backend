# Terminates requests to unrecognised paths at Cloudflare's edge, before they
# reach the tunnel. Both APIs are called only by clients we own, which always
# use known-good paths — anything else (credential scanners, exploit probes)
# is safe to reject here rather than let it hit the origin container.
#
# Path matching is deliberately coarse (hostname + path prefix/exact match,
# no per-route regex): the apps already validate route shape and return their
# own 400/404s, so this only needs to catch traffic that isn't even trying to
# hit a real route.
resource "cloudflare_ruleset" "block_invalid_api_paths" {
  zone_id     = var.cloudflare_zone_id
  name        = "block-invalid-api-paths"
  description = "Reject requests to paths outside each internal API's known routes"
  kind        = "zone"
  phase       = "http_request_firewall_custom"

  rules {
    description = "notifications-api: only /health and /clients/* are real routes"
    expression  = <<-EOT
      (http.host in {${join(" ", [for h in var.notifications_hostnames : "\"${h}\""])}})
      and not (
        http.request.uri.path eq "/health"
        or starts_with(http.request.uri.path, "/clients/")
      )
    EOT
    action      = var.rule_action
    enabled     = true
  }

  rules {
    description = "upstream-api: only /calendar, /water-temperature, /wave-weather are real routes"
    expression  = <<-EOT
      (http.host in {${join(" ", [for h in var.upstream_api_hostnames : "\"${h}\""])}})
      and not (
        http.request.uri.path in {"/calendar" "/water-temperature" "/wave-weather"}
      )
    EOT
    action      = var.rule_action
    enabled     = true
  }

  # The webapp host serves arbitrary static paths (the Flutter bundle, its
  # assets, canvaskit), so only its API surface is worth constraining. nginx
  # already 404s anything else under /api/; this keeps it off the tunnel.
  rules {
    description = "webapp: only /api/calendar and /api/wave-weather are proxied"
    expression  = <<-EOT
      (http.host eq "${var.webapp_hostname}")
      and starts_with(http.request.uri.path, "/api/")
      and not (
        http.request.uri.path in {"/api/calendar" "/api/wave-weather"}
      )
    EOT
    action      = var.rule_action
    enabled     = true
  }

  # nginx already rejects an /api/ request with no x-client-id, but that
  # rejection cannot be allowed to depend on the origin, because the edge
  # cache in webapp_cache below keys on URL and query string alone. A cached
  # 200 fetched by a real client would otherwise be replayed to a caller that
  # sent no header at all, and the origin would never see the request.
  #
  # Enforcing the header here instead - the firewall phase runs before cache -
  # means a request that fails this check is terminated at the edge and can
  # never be served from the cache.
  #
  # This duplicates the origin check rather than replacing it: the origin must
  # still stand on its own for anything reaching it another way.
  #
  # Presence only, not shape. Checking the UUID pattern needs the "matches"
  # operator, which Cloudflare gates behind Business ("not entitled: the use
  # of operator Matches is not allowed"). nginx still validates the shape, so
  # what survives is a caller that invents a well-formed-looking value: it
  # gets past this rule and can be served a cached 200 without the origin
  # seeing it. Closing that gap needs a Business plan, or an Enterprise cache
  # key that includes the header.
  rules {
    description = "webapp /api/*: require an x-client-id header to be present"
    expression  = <<-EOT
      (http.host eq "${var.webapp_hostname}")
      and starts_with(http.request.uri.path, "/api/")
      and not any(http.request.headers.names[*] eq "x-client-id")
    EOT
    action      = var.rule_action
    enabled     = true
  }
}

# Cloudflare does not leave an origin's caching alone by default, and the web
# app is the one host here that serves static files:
#
#   - a .js with no Cache-Control is cached by extension, and the zone's
#     Browser Cache TTL setting is stamped onto it. That is how a redeploy left
#     a 4-hour-stale main.dart.js in visitors' browsers.
#   - nginx now sends explicit headers for everything (deploy/nginx.conf.template
#     in the app repo): no-cache on the bundle, immutable on /canvaskit/ only,
#     because Flutter content-hashes none of its output.
#
# These rules say: believe those headers. Without them the origin's intent is
# advisory at best.
#
# Both rules live in one resource because Cloudflare allows a single zone
# entrypoint ruleset per phase. A second kind="zone" resource on
# http_request_cache_settings would address the same object, and the two would
# clobber each other on every apply.
#
# Their expressions are mutually exclusive - /api/ against not /api/ - so
# exactly one matches any given request. That is deliberate: it means nothing
# here depends on how Cloudflare resolves precedence between overlapping cache
# rules, which is easy to get wrong and invisible when you do.
#
# This resource is the only cache configuration for the host. An earlier note
# here warned that the dashboard already held an equivalent rule by hand, and
# that applying would leave you with both - that was checked before the first
# apply and was not true: the zone had no caching rule and no page rules. What
# probably lay behind it is the zone-wide Browser Cache TTL setting, which is
# not per-host and which a cache rule overrides for the requests it matches.
resource "cloudflare_ruleset" "webapp_cache" {
  zone_id     = var.cloudflare_zone_id
  name        = "webapp-cache"
  description = "Honour the webapp origin's Cache-Control, and cache its open API endpoints"
  kind        = "zone"
  phase       = "http_request_cache_settings"

  # The webapp proxies two upstream-api endpoints without a key - the browser
  # cannot hold one, so nginx injects it server-side. That makes those two paths
  # open to anyone, which is acceptable for a published schedule but not
  # something to leave unmetered: the origin is a single box behind a tunnel,
  # running gunicorn with one worker.
  #
  # Caching is the main defence. Repeat traffic is answered at the PoP and never
  # reaches the tunnel at all. nginx sends max-age=300 on both endpoints, which
  # sits inside upstream-api's own 540s cache, so the edge never serves anything
  # staler than the origin would have.
  rules {
    description = "webapp /api/*: cache, honouring the origin's max-age"
    expression  = <<-EOT
      (http.host eq "${var.webapp_hostname}")
      and starts_with(http.request.uri.path, "/api/")
    EOT
    action      = "set_cache_settings"
    enabled     = true

    action_parameters {
      cache = true

      edge_ttl {
        mode = "respect_origin"

        # Never cache a rejection. nginx's 403 and 429 depend on request
        # headers that are not in the cache key, so caching one would pin it
        # to the URL and serve it to every later caller - including the
        # legitimate ones whose headers were fine.
        status_code_ttl {
          status_code_range {
            from = 400
            to   = 499
          }
          value = -1
        }
      }

      browser_ttl {
        mode = "respect_origin"
      }

      # These two GETs vary only by query string, and Cloudflare's default
      # cache key already includes the full query string - so the effective
      # key is right without customising it. A custom_key narrowing that to
      # just dateFrom and numberOfDays would be tidier (unknown extra params
      # would stop fragmenting the cache), but cache key customisation is an
      # Enterprise feature and this zone is on Free: apply fails with "not
      # entitled to use the custom cache key override". Nothing appends extra
      # params today, so the difference is theoretical.
    }
  }

  # Everything that is not the API surface: the Flutter bundle, its assets and
  # canvaskit - plain path lookups, cached on the origin's own headers.
  rules {
    description = "webapp static bundle: respect origin cache headers"
    expression  = <<-EOT
      (http.host eq "${var.webapp_hostname}")
      and not starts_with(http.request.uri.path, "/api/")
    EOT
    action      = "set_cache_settings"
    enabled     = true

    action_parameters {
      cache = true

      edge_ttl {
        mode = "respect_origin"
      }

      browser_ttl {
        mode = "respect_origin"
      }
    }
  }
}

# A backstop for whatever the cache does not absorb - a scraper walking dates
# it has never requested produces a cache miss every time, which is exactly the
# traffic that reaches the origin. nginx rate-limits too, but doing it at the
# edge keeps it off the tunnel.
#
# Every parameter here is pinned by the Free plan rather than chosen: one rule
# per zone, a 10s counting period, a 10s mitigation timeout, and ip.src as the
# only counting characteristic. Cloudflare rejects any other value outright.
resource "cloudflare_ruleset" "webapp_api_ratelimit" {
  zone_id     = var.cloudflare_zone_id
  name        = "webapp-api-ratelimit"
  description = "Rate-limit the webapp's open API endpoints"
  kind        = "zone"
  phase       = "http_ratelimit"

  rules {
    description = "webapp /api/*: cap per-IP request rate"
    expression  = <<-EOT
      (http.host eq "${var.webapp_hostname}")
      and starts_with(http.request.uri.path, "/api/")
    EOT
    action      = var.rule_action
    enabled     = true

    ratelimit {
      # cf.colo.id is not optional - Cloudflare counts at the PoP, and rejects
      # the rule without it (error 20155). The budget is therefore per IP per
      # PoP, not global: a client that reaches two PoPs gets two budgets. Not
      # a concern for a single client, which sticks to one.
      characteristics     = ["ip.src", "cf.colo.id"]
      period              = 10
      requests_per_period = var.api_rate_limit_per_10s
      mitigation_timeout  = 10

      # requests_to_origin = true would count only what the cache did not
      # already answer, which is the traffic this rule actually cares about.
      # It is a paid-plan feature, so edge cache hits count too and the
      # threshold bites sooner than the origin load alone would suggest.
    }
  }
}
