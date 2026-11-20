output "web_url" {
  description = "The Cloud Run address of the API (put the public host name in front of it)."
  value       = google_cloud_run_v2_service.web.uri
}

output "image_repository" {
  description = "Push the production image here."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "database_private_ip" {
  value = google_sql_database_instance.main.private_ip_address
}

output "secrets_to_fill_by_hand" {
  description = "These secrets exist but have no value until a person adds one."
  value = [
    google_secret_manager_secret.s["email-password"].secret_id,
    google_secret_manager_secret.s["twilio-auth-token"].secret_id,
    google_secret_manager_secret.s["sendgrid-api-key"].secret_id,
  ]
}

output "public_ip" {
  description = "Point an A record for every host below at this address."
  value       = google_compute_global_address.public.address
}

output "hosts" {
  value = local.all_hosts
}

output "app_buckets" {
  description = "Upload each app's built files (dist/) to its bucket."
  value       = { for key, bucket in google_storage_bucket.app : key => bucket.name }
}
