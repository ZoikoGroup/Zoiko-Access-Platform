# 03: Database unavailable

**Signs:** `GET /readyz` does not answer "ready"; every page fails; the logs show database
connection errors.

## Check
1. Is the database server running? (`docker compose ps db`, or the cloud console.)
2. Is the disk full? Is the connection limit reached?
3. Can the application's restricted login connect? (`python manage.py db_grants --check`.)

## Act
- Restart the database service only if it has stopped. Do not restore from backup unless data is
  damaged: that is a separate, two-person procedure (DEPLOYMENT.md).
- **Disk full:** free space or grow the disk. Do NOT delete tables or the audit log.
- Open an incident: every patient and clinic is affected. Tell customers through the status
  channel.

## Done when
`/readyz` is ready, the outbox and the mail queue are draining, and the incident records how long
the service was down.
