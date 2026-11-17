# The five front-end apps and one HTTPS load balancer in front of everything (DEV-197).
#
# Each app is a static build (vite) in its own Cloud Storage bucket, served through Cloud CDN at its
# own host name. The apps call the API at a relative /api/v1, so on every host the load balancer
# sends /api/* (and the health probes) to the Cloud Run web service; api.<domain> goes there too.
# One Google-managed certificate covers every host. (Validated with the rest; not applied.)

locals {
  apps = {
    patient  = "patient"  # frontend/patient
    staff    = "staff"    # frontend/operations
    doctor   = "doctor"   # frontend/clinician
    platform = "platform" # frontend/platform
    partners = "partners" # frontend/partners
  }
  app_hosts = { for key, sub in local.apps : key => "${sub}.${var.domain}" }
  api_host  = "api.${var.domain}"
  all_hosts = concat([local.api_host], values(local.app_hosts))
}

resource "google_storage_bucket" "app" {
  for_each                    = local.apps
  name                        = "${var.project_id}-${local.name}-${each.key}"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false
  labels                      = local.labels
  website {
    main_page_suffix = "index.html"
    not_found_page   = "index.html" # a single-page app: unknown paths load the app
  }
}

# The built files are public by design (they are the apps' own HTML, JS and CSS, no data).
resource "google_storage_bucket_iam_member" "public_read" {
  for_each = google_storage_bucket.app
  bucket   = each.value.name
  role     = "roles/storage.objectViewer"
  member   = "allUsers"
}

resource "google_compute_backend_bucket" "app" {
  for_each    = google_storage_bucket.app
  name        = "${local.name}-${each.key}"
  bucket_name = each.value.name
  enable_cdn  = true
}

# The API behind the load balancer, through a serverless network endpoint group.
resource "google_compute_region_network_endpoint_group" "api" {
  name                  = "${local.name}-api"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.web.name
  }
}

resource "google_compute_backend_service" "api" {
  name                  = "${local.name}-api"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"
  enable_cdn            = false # API answers are personal: never cached
  backend {
    group = google_compute_region_network_endpoint_group.api.id
  }
}

resource "google_compute_url_map" "main" {
  name            = local.name
  default_service = google_compute_backend_service.api.id

  host_rule {
    hosts        = [local.api_host]
    path_matcher = "api"
  }
  path_matcher {
    name            = "api"
    default_service = google_compute_backend_service.api.id
  }

  dynamic "host_rule" {
    for_each = local.app_hosts
    content {
      hosts        = [host_rule.value]
      path_matcher = host_rule.key
    }
  }
  dynamic "path_matcher" {
    for_each = local.app_hosts
    content {
      name            = path_matcher.key
      default_service = google_compute_backend_bucket.app[path_matcher.key].id
      path_rule {
        paths   = ["/api/*", "/healthz", "/readyz"]
        service = google_compute_backend_service.api.id
      }
    }
  }
}

resource "google_compute_managed_ssl_certificate" "main" {
  name = local.name
  managed {
    domains = local.all_hosts
  }
}

resource "google_compute_target_https_proxy" "main" {
  name             = local.name
  url_map          = google_compute_url_map.main.id
  ssl_certificates = [google_compute_managed_ssl_certificate.main.id]
}

resource "google_compute_global_address" "public" {
  name = "${local.name}-public"
}

resource "google_compute_global_forwarding_rule" "https" {
  name                  = "${local.name}-https"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  target                = google_compute_target_https_proxy.main.id
  ip_address            = google_compute_global_address.public.id
  port_range            = "443"
}

# Plain http only redirects to https.
resource "google_compute_url_map" "redirect" {
  name = "${local.name}-redirect"
  default_url_redirect {
    https_redirect         = true
    strip_query            = false
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
  }
}

resource "google_compute_target_http_proxy" "redirect" {
  name    = "${local.name}-redirect"
  url_map = google_compute_url_map.redirect.id
}

resource "google_compute_global_forwarding_rule" "http" {
  name                  = "${local.name}-http"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  target                = google_compute_target_http_proxy.redirect.id
  ip_address            = google_compute_global_address.public.id
  port_range            = "80"
}
