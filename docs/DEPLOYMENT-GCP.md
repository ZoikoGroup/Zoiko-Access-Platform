# Running Zoiko Access on Google Cloud (London)

**Status: validated, not applied.** Google Cloud, Twilio and the United Kingdom were chosen on
2026-11-16 (DEV-195) and still need the lead to confirm them. The Terraform in `deploy/gcp/` passes
`terraform validate` (Terraform 1.9 in Docker, google provider ~> 6.0), but **no plan has been run
against a real project and nothing has been applied**. Someone with a Google Cloud account must run
`terraform plan` and have it reviewed before anything is created. The production image
(`backend/Dockerfile.production`) has not been built yet either, because the clean build keeps
failing on network time-outs here (backlog A7).

## What gets created (all in `europe-west2`, London)

| Piece | What it is | Why |
|---|---|---|
| Network | A private VPC with one subnet and a private link to Cloud SQL | The database has no public address at all |
| Database | Cloud SQL PostgreSQL 16, high availability (a standby in another zone), encrypted connections only | Row-level security and the audit chains need PostgreSQL |
| Backups | Daily at 02:00 UTC, 30 kept, point-in-time recovery over 7 days, stored in the same region | Recovery without moving UK data abroad |
| Secrets | Secret Manager, replicated only in London. The Django key, the field-encryption key, both database passwords and the metrics token are generated. The e-mail and Twilio secrets are filled in by a person | No secret in a file, an image or Terraform variables |
| Images | Artifact Registry `zoiko` | Where the production image is pushed |
| Web | Cloud Run service `zoiko-uk-01-web`: gunicorn, at least one copy always running, ready check on `/readyz` | The API for all five apps |
| Migrations | Cloud Run job `zoiko-uk-01-migrate`, run as the table owner | The app itself never logs in as the owner (DEV-083) |
| Worker | Cloud Run job `zoiko-uk-01-worker`, started every minute by Cloud Scheduler. It runs one pass of `relay_outbox` (events, e-mail, texts, reminders, digests) and one of `deliver_webhooks` | Both commands are safe to run as several copies at once |
| Front ends | The five apps (patient, staff, doctor, platform, partners), each a static build in its own Cloud Storage bucket behind Cloud CDN, at `patient.`, `staff.`, `doctor.`, `platform.` and `partners.` your domain | The apps hold no data, so they can be cached |
| Load balancer | One global HTTPS load balancer with a Google-managed certificate for every host. On every app host, `/api/*`, `/healthz` and `/readyz` go to the web service; `api.` your domain goes there too; plain http redirects to https. The web service accepts traffic only through it | The apps call the API at a relative `/api/v1` |
| Monitoring | An uptime check on `https://<host>/readyz` every minute, and an e-mail alert after 5 minutes of failure | Someone hears when it is down |

## First deployment, in order

1. **Account and project.** Create the project and turn billing on (a person with authority).
2. **Fill in the values.** Copy `deploy/gcp/terraform.tfvars.example` to `terraform.tfvars` and fill it in. Git ignores that file. No secrets go in it.
3. **Check, then create.** In `deploy/gcp`: `terraform init`, `terraform validate`, `terraform plan`. Have the plan reviewed, then run `terraform apply`. The first apply creates the registry, so the web service will fail until an image is pushed. That is expected: push the image in step 4, then apply again.
4. **Build and push the image** from `backend/`:
   `docker build -f Dockerfile.production -t <image_repository>/backend:<build> .`, then `docker push`.
5. **Create the application login.** The app connects as `zoiko_app`, which must not own the tables and must not bypass row-level security. On Cloud SQL, connect as `zoiko_owner` (password in Secret Manager) and run `deploy/create_db_roles.sql`, using the `db-app-password` secret as the app password. On Cloud SQL the "superuser" rules differ from a plain server, so check afterwards that `zoiko_app` has `rolbypassrls = false` and owns nothing. The app refuses to start otherwise (DEV-083).
6. **Migrate.** Run `gcloud run jobs execute zoiko-uk-01-migrate --region europe-west2 --wait`.
7. **Check the deployment settings.** Run `python manage.py check --deploy` in the image with the production values. It refuses development settings, placeholder secrets, local mail servers and `DEV-` cell ids (DEV-088).
8. **Fill the by-hand secrets.** Add the mail provider password and the Twilio auth token: `gcloud secrets versions add zoiko-uk-01-email-password --data-file=-`. Then set `manual_secrets_ready = true` (and `twilio_account_sid`, `twilio_from`) and apply again.
9. **Load the reference data.** Run `python manage.py load_traceability`. Draft the UK country pack, then have it approved by a legal and a clinical reviewer (it switches nothing on until then).
10. **Build and upload the apps.** In each of `frontend/operations`, `patient`, `clinician`, `platform` and `partners`, run `npm ci && npm run build`. Then run `gsutil -m rsync -r -d dist gs://<bucket>`, using the bucket from the `app_buckets` output (`operations` goes to the `staff` bucket and `clinician` to the `doctor` bucket). The development sign-in is not in a production build (checked).
11. **Point the domain.** Create an A record for each host in the `hosts` output, pointing at the `public_ip` output. The managed certificate turns active once the records resolve, which can take up to an hour. Then confirm the uptime check turns green.

## Every later release

Build and push the image with a new `build` tag, run the migration job, and update `image` and `build`
in `terraform.tfvars` and apply (or deploy a new revision with `gcloud run deploy`).

## Not covered yet, stated plainly

- **An app host shows its app for unknown paths with a 404 status** (the bucket's "not found" page is the app). The apps route with `?page=`, so this rarely matters.
- **A mail provider** is still undecided (DEV-016). Any SMTP provider works through the `email_*` values.
- **Logical exports and a restore drill on Cloud SQL.** Automated backups and point-in-time recovery are on, but a scheduled export and a timed restore rehearsal on the real database are not set up. `scripts/recovery_drill.py` only rehearses at development size.
- **A second region or failover.** One region, by design for UK data residency. Disaster recovery to another UK location is not planned yet.
- **Security review of the plan, a WAF (Cloud Armor), private ingress, budgets and alerts on cost**, and a deployment pipeline with signed images (GHE-18 section B).
- **Log routing and retention.** Logs go to Cloud Logging with the default retention. Secrets are redacted before any line is written (DEV-186).
