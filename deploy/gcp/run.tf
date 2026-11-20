# The web service, the migration job and the worker job (see main.tf for the warning: not applied).

resource "google_cloud_run_v2_service" "web" {
  name                = "${local.name}-web"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER" # only through the load balancer
  deletion_protection = true
  labels              = local.labels

  template {
    service_account = google_service_account.app.email
    scaling {
      min_instance_count = 1
      max_instance_count = var.web_max_instances
    }
    vpc_access {
      network_interfaces {
        network    = google_compute_network.main.id
        subnetwork = google_compute_subnetwork.main.id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }
    containers {
      image = var.image
      ports {
        container_port = 8000
      }
      resources {
        limits = { cpu = "1", memory = "1Gi" }
      }
      dynamic "env" {
        for_each = local.plain_env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
      startup_probe {
        http_get {
          path = "/readyz"
        }
        period_seconds    = 5
        failure_threshold = 12
      }
      liveness_probe {
        http_get {
          path = "/healthz"
        }
        period_seconds = 30
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.app_reads, google_secret_manager_secret_version.generated]
}

# Migrations run as the table owner, once per release, before the new web revision takes traffic.
resource "google_cloud_run_v2_job" "migrate" {
  name                = "${local.name}-migrate"
  location            = var.region
  deletion_protection = true
  labels              = local.labels

  template {
    task_count = 1
    template {
      service_account = google_service_account.app.email
      max_retries     = 0
      timeout         = "900s"
      vpc_access {
        network_interfaces {
          network    = google_compute_network.main.id
          subnetwork = google_compute_subnetwork.main.id
        }
        egress = "PRIVATE_RANGES_ONLY"
      }
      containers {
        image   = var.image
        command = ["python", "manage.py", "migrate", "--noinput"]
        dynamic "env" {
          for_each = merge(local.plain_env, { POSTGRES_USER = "zoiko_owner" })
          content {
            name  = env.key
            value = env.value
          }
        }
        dynamic "env" {
          for_each = merge(local.secret_env, {
            POSTGRES_PASSWORD = google_secret_manager_secret.s["db-owner-password"].secret_id
          })
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = env.value
                version = "latest"
              }
            }
          }
        }
      }
    }
  }
}

# The worker: one pass of events, e-mail, texts, scheduled jobs and webhooks, started every minute.
# Both commands are safe to run as several copies at once (they skip rows another copy holds).
resource "google_cloud_run_v2_job" "worker" {
  name                = "${local.name}-worker"
  location            = var.region
  deletion_protection = true
  labels              = local.labels

  template {
    task_count = 1
    template {
      service_account = google_service_account.app.email
      max_retries     = 0
      timeout         = "300s"
      vpc_access {
        network_interfaces {
          network    = google_compute_network.main.id
          subnetwork = google_compute_subnetwork.main.id
        }
        egress = "ALL_TRAFFIC" # it must reach the mail server, Twilio and webhooks
      }
      containers {
        image   = var.image
        command = ["sh", "-c", "python manage.py relay_outbox --once && python manage.py deliver_webhooks --once"]
        dynamic "env" {
          for_each = local.plain_env
          content {
            name  = env.key
            value = env.value
          }
        }
        dynamic "env" {
          for_each = local.secret_env
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = env.value
                version = "latest"
              }
            }
          }
        }
      }
    }
  }
}

resource "google_cloud_scheduler_job" "worker_every_minute" {
  name      = "${local.name}-worker"
  region    = var.region
  schedule  = "* * * * *"
  time_zone = "Etc/UTC"

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.worker.name}:run"
    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }
  depends_on = [google_project_service.apis]
}
