# Zoiko Access on Google Cloud, London region (DEV-195, DEV-196).
#
# VALIDATED, NOT APPLIED: `terraform validate` passes (Terraform 1.9, google ~> 6.0, random ~> 3.6),
# but no plan has been run against a real project. Run `terraform plan` with a Google Cloud account
# and have the plan reviewed. Nothing here is created until someone runs `terraform apply`.
#
# What it makes: a private network; Cloud SQL PostgreSQL 16 with no public address, daily backups,
# point-in-time recovery and high availability; Secret Manager secrets (generated where possible);
# an image registry; the web service on Cloud Run; a migration job; the background worker as a job
# run every minute by Cloud Scheduler; an uptime check with an e-mail alert.

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 6.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  name   = "zoiko-${lower(var.cell_id)}"
  labels = { app = "zoiko-access", cell = lower(var.cell_id) }
  # Plain settings every container gets. Secrets are added separately, from Secret Manager.
  plain_env = {
    DJANGO_SETTINGS_MODULE = "config.settings.production"
    DJANGO_ALLOWED_HOSTS   = join(",", local.all_hosts)
    POSTGRES_DB            = "zoiko"
    POSTGRES_HOST          = google_sql_database_instance.main.private_ip_address
    POSTGRES_PORT          = "5432"
    POSTGRES_USER          = "zoiko_app"
    ZOIKO_CELL_ID          = var.cell_id
    ZOIKO_BUILD            = var.build
    ZOIKO_FIELD_KEY_ID     = "k1"
    EMAIL_HOST             = var.email_host
    EMAIL_PORT             = tostring(var.email_port)
    EMAIL_HOST_USER        = var.email_user
    EMAIL_USE_TLS          = "1"
    EMAIL_FROM             = var.email_from
    MAIL_PROVIDER          = "sendgrid" # DEV-350
    # SendGrid signs its delivery receipts; this is its PUBLIC key, from the SendGrid account (DEV-354).
    SENDGRID_WEBHOOK_PUBLIC_KEY = var.sendgrid_webhook_public_key
    ATTACHMENT_SCANNER          = "clamav" # DEV-345, DEV-351: files open only after this scan
    CLAMAV_HOST                 = google_compute_instance.clamav.network_interface[0].network_ip
    CLAMAV_PORT                 = "3310"
    TEXT_PROVIDER               = var.twilio_account_sid == "" ? "" : "twilio"
    TWILIO_ACCOUNT_SID          = var.twilio_account_sid
    TWILIO_FROM                 = var.twilio_from
    TWILIO_STATUS_URL           = "https://api.${var.domain}/api/v1/webhooks/twilio/status"
    PATIENT_APP_URL             = "https://${local.app_hosts["patient"]}"
  }
  # Cloud Run will not start with a secret that has no value, so the two secrets a person fills in
  # by hand are attached only once `manual_secrets_ready` is set to true.
  secret_env = merge({
    DJANGO_SECRET_KEY   = google_secret_manager_secret.s["django-secret-key"].secret_id
    ZOIKO_FIELD_KEYS    = google_secret_manager_secret.s["field-keys"].secret_id
    POSTGRES_PASSWORD   = google_secret_manager_secret.s["db-app-password"].secret_id
    ZOIKO_METRICS_TOKEN = google_secret_manager_secret.s["metrics-token"].secret_id
    }, var.manual_secrets_ready ? {
    EMAIL_HOST_PASSWORD = google_secret_manager_secret.s["email-password"].secret_id
    SENDGRID_API_KEY    = google_secret_manager_secret.s["sendgrid-api-key"].secret_id
    TWILIO_AUTH_TOKEN   = google_secret_manager_secret.s["twilio-auth-token"].secret_id
  } : {})
}

# ------------------------------------------------------------------ APIs
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com", "sqladmin.googleapis.com", "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com", "servicenetworking.googleapis.com",
    "cloudscheduler.googleapis.com", "monitoring.googleapis.com", "compute.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# ------------------------------------------------------------------ network (private only)
resource "google_compute_network" "main" {
  name                    = local.name
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "main" {
  name                     = local.name
  network                  = google_compute_network.main.id
  region                   = var.region
  ip_cidr_range            = "10.10.0.0/24"
  private_ip_google_access = true
}

resource "google_compute_global_address" "sql_range" {
  name          = "${local.name}-sql"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 20
  network       = google_compute_network.main.id
}

resource "google_service_networking_connection" "private" {
  network                 = google_compute_network.main.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.sql_range.name]
}

# ------------------------------------------------------------------ database
resource "google_sql_database_instance" "main" {
  name                = local.name
  database_version    = "POSTGRES_16"
  region              = var.region
  deletion_protection = true
  depends_on          = [google_service_networking_connection.private]

  settings {
    tier              = var.db_tier
    availability_type = "REGIONAL" # a standby in another zone of the same region
    disk_autoresize   = true
    user_labels       = local.labels

    ip_configuration {
      ipv4_enabled    = false # no public address at all
      private_network = google_compute_network.main.id
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled                        = true
      start_time                     = "02:00"
      location                       = var.region # backups stay in the UK region
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = 30
        retention_unit   = "COUNT"
      }
    }

    maintenance_window {
      day  = 7
      hour = 3
    }
  }
}

resource "google_sql_database" "zoiko" {
  name     = "zoiko"
  instance = google_sql_database_instance.main.name
}

# The table OWNER, used only to run migrations. The application logs in as `zoiko_app`, created by
# deploy/create_db_roles.sql (see docs/DEPLOYMENT-GCP.md): never as the owner, because PostgreSQL
# skips row-level security for table owners (DEV-083).
resource "google_sql_user" "owner" {
  name     = "zoiko_owner"
  instance = google_sql_database_instance.main.name
  password = random_password.db["db-owner-password"].result
}

# ------------------------------------------------------------------ secrets
resource "random_password" "db" {
  for_each = toset(["db-owner-password", "db-app-password", "metrics-token"])
  length   = 40
  special  = false
}

resource "random_password" "django" {
  length  = 64
  special = false
}

resource "random_bytes" "field_key" {
  length = 32
}

resource "google_secret_manager_secret" "s" {
  for_each = toset([
    "django-secret-key", "field-keys", "db-owner-password", "db-app-password", "metrics-token",
    "email-password", "twilio-auth-token", "sendgrid-api-key",
  ])
  secret_id = "${local.name}-${each.value}"
  labels    = local.labels
  replication {
    user_managed {
      replicas { location = var.region } # secrets stay in the UK region too
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "generated" {
  for_each = {
    "django-secret-key" = random_password.django.result
    "field-keys"        = "k1:${random_bytes.field_key.base64}"
    "db-owner-password" = random_password.db["db-owner-password"].result
    "db-app-password"   = random_password.db["db-app-password"].result
    "metrics-token"     = random_password.db["metrics-token"].result
  }
  secret      = google_secret_manager_secret.s[each.key].id
  secret_data = each.value
}
# `email-password` and `twilio-auth-token` get their values from a person, never from this file:
#   gcloud secrets versions add <name> --data-file=-

# ------------------------------------------------------------------ images and identity
resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "zoiko"
  format        = "DOCKER"
  labels        = local.labels
  depends_on    = [google_project_service.apis]
}

resource "google_service_account" "app" {
  account_id   = "${local.name}-app"
  display_name = "Zoiko Access application"
}

resource "google_secret_manager_secret_iam_member" "app_reads" {
  for_each  = google_secret_manager_secret.s
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app.email}"
}

resource "google_project_iam_member" "app_sql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.app.email}"
}

resource "google_service_account" "scheduler" {
  account_id   = "${local.name}-scheduler"
  display_name = "Starts the Zoiko worker job"
}

resource "google_project_iam_member" "scheduler_runs_jobs" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.scheduler.email}"
}
