# Is the service answering? An uptime check on /readyz every minute from several places, and an
# e-mail to the on-call address when it fails for five minutes. (Not applied: see main.tf.)

resource "google_monitoring_uptime_check_config" "ready" {
  display_name = "${local.name} ready"
  timeout      = "10s"
  period       = "60s"

  http_check {
    path         = "/readyz"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }
  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = local.api_host
    }
  }
}

resource "google_monitoring_notification_channel" "oncall" {
  display_name = "Zoiko on-call"
  type         = "email"
  labels       = { email_address = var.alert_email }
}

resource "google_monitoring_alert_policy" "not_ready" {
  display_name = "${local.name}: not ready"
  combiner     = "OR"
  conditions {
    display_name = "Uptime check failing"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.label.check_id=\"${google_monitoring_uptime_check_config.ready.uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "300s"
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.project_id", "resource.label.host"]
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.oncall.id]
}

# Database backups (DEV-201): an e-mail when Cloud SQL logs an error about a backup. The filter must
# be checked against the instance's real logs once it exists; a backup that is silently MISSING
# (no run at all) is not caught by this and still needs the restore drill in the guide.
resource "google_monitoring_alert_policy" "backup_errors" {
  display_name = "${local.name}: database backup error"
  combiner     = "OR"
  conditions {
    display_name = "Cloud SQL logged an error about a backup"
    condition_matched_log {
      filter = join(" AND ", [
        "resource.type=\"cloudsql_database\"",
        "resource.labels.database_id=\"${var.project_id}:${google_sql_database_instance.main.name}\"",
        "severity>=ERROR",
        "(textPayload:\"backup\" OR protoPayload.methodName:\"backup\")",
      ])
    }
  }
  alert_strategy {
    notification_rate_limit {
      period = "3600s"
    }
  }
  notification_channels = [google_monitoring_notification_channel.oncall.id]
}
