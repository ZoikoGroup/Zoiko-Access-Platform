variable "project_id" {
  description = "The Google Cloud project to deploy into."
  type        = string
}

variable "region" {
  description = "Where everything lives. London keeps UK patient data in the UK (DEV-195)."
  type        = string
  default     = "europe-west2"
}

variable "cell_id" {
  description = "The cell's id, recorded on every audit row. Must not start with DEV- or TEST-."
  type        = string
  default     = "UK-01"
}

variable "build" {
  description = "The release id or commit this image was built from (ZOIKO_BUILD)."
  type        = string
}

variable "image" {
  description = "The production image, built from backend/Dockerfile.production and pushed to the registry."
  type        = string
}

variable "domain" {
  description = "The domain the apps live under: patient., staff., doctor., platform., partners. and api. are made from it."
  type        = string
}

variable "db_tier" {
  description = "The Cloud SQL machine size. A starting point, to be measured."
  type        = string
  default     = "db-custom-2-7680"
}

variable "web_max_instances" {
  description = "The most copies of the web service Cloud Run may start."
  type        = number
  default     = 4
}

variable "alert_email" {
  description = "Where the not-ready alert goes."
  type        = string
}

variable "email_host" {
  description = "The SMTP server of the mail provider (the provider itself is still undecided, DEV-016)."
  type        = string
}

variable "email_port" {
  type    = number
  default = 587
}

variable "email_user" {
  type = string
}

variable "email_from" {
  type = string
}

variable "manual_secrets_ready" {
  description = "Set to true once the e-mail and Twilio secrets have been given values by a person."
  type        = bool
  default     = false
}

variable "twilio_account_sid" {
  description = "Twilio account SID (not secret). Empty means no texts are sent."
  type        = string
  default     = ""
}

variable "twilio_from" {
  description = "The sending number (+44...) or messaging service (MG...)."
  type        = string
  default     = ""
}
