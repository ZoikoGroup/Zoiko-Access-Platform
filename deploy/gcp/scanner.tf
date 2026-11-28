# The virus scanner for uploaded files (DEV-345, DEV-351): ClamAV on a small private VM in the
# cell's own network. Cloud Run cannot serve clamd's plain TCP protocol, so it runs here instead.
# No public address; only the app's subnet may reach port 3310. Container-Optimised OS runs the
# official image and restarts it; clamd keeps its signatures up to date by itself.

resource "google_service_account" "scanner" {
  account_id   = "${local.name}-scanner"
  display_name = "Zoiko virus scanner (no permissions)"
}

resource "google_compute_instance" "clamav" {
  name                      = "${local.name}-clamav"
  machine_type              = var.scanner_machine_type
  zone                      = "${var.region}-b"
  labels                    = local.labels
  tags                      = ["zoiko-clamav"]
  allow_stopping_for_update = true

  boot_disk {
    initialize_params {
      image = "cos-cloud/cos-stable"
      size  = 20
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.main.id
    # no access_config: no public address
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  service_account {
    email  = google_service_account.scanner.email
    scopes = ["https://www.googleapis.com/auth/logging.write"]
  }

  metadata = {
    block-project-ssh-keys = "true"
    user-data              = <<-EOT
      #cloud-config
      runcmd:
        - docker run -d --restart=always --name clamav -p 3310:3310 clamav/clamav:1.4
    EOT
  }

  depends_on = [google_project_service.apis]
}

resource "google_compute_firewall" "clamav_from_app" {
  name          = "${local.name}-clamav-in"
  network       = google_compute_network.main.id
  direction     = "INGRESS"
  source_ranges = [google_compute_subnetwork.main.ip_cidr_range]
  target_tags   = ["zoiko-clamav"]
  allow {
    protocol = "tcp"
    ports    = ["3310"]
  }
}
