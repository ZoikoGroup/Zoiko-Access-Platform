# Setting up the database for a real server

Status: tested by rehearsal on a scratch PostgreSQL 16 database (below). **Not yet tried on a managed
cloud database** (for example RDS or Cloud SQL), where the "superuser" and "owner" rules differ. This is
only the database part of a deployment: servers, TLS, secrets storage, backups and the mail provider are
not covered (they wait on decisions DEV-013 and DEV-016).

## Why two logins

Zoiko Access keeps every customer's data in one database and relies on PostgreSQL's **row-level
security** (DEV-018) as the second fence between customers. PostgreSQL ignores that fence for
superusers and for table owners, so the running application must NOT connect as either.

| Login | Used by | Can do |
|---|---|---|
| `zoiko_owner` | you, when running migrations | owns the tables; creates and changes them |
| `zoiko_app` | the web server and the worker | only what `foundation/dbgrants.py` lists: read, add, change; delete only the delivery plumbing; **add only** (never change or delete) on the audit, booking-history, work-history and release-log tables |

If the app is started on a superuser, or a login with `BYPASSRLS`, with `DEBUG` off, **it refuses to start**
and says why (DEV-083). In development (`DEBUG` on) it only warns.

## Development-only settings are refused too

With `DEBUG` off, the web server and the worker also **refuse to start while any development-only setting
remains** (DEV-088). The same list is reported by `python manage.py check --deploy`, so run that on the server
before starting anything:

| It refuses when... | Fix |
|---|---|
| `DEBUG` or the unsigned sign-in `DEV_AUTH_STUB` is on | use the production settings module, not `config.settings.dev` |
| passwords may be shorter than 10 characters | do not set `PASSWORD_MIN_LENGTH` below 10 |
| the secret key, the database password or the encryption keys still contain a placeholder word (`dev-only`, `replace-me`, `replace-with`, `change-me`, `changeme`, `test-only`), are empty, or the secret key is under 40 characters | set real secrets (from your secret store, never from `.env.example`) |
| `DJANGO_ALLOWED_HOSTS` is empty, `*`, or only local addresses | list the real host names |
| `ZOIKO_CELL_ID` starts with `DEV-` or `TEST-` | use the real cell id |
| `ZOIKO_BUILD` is empty or `dev` | set it to the release id or commit the server was built from (every audit row records it) |
| email goes to `localhost`, `mail`, `mailpit`, `mailhog` or port 1025 | set the real mail server (provider still undecided, DEV-016) |


## First-time setup

1. **Create an empty database** (any name; below it is `zoiko`).
2. **Create the two logins**, once, as a PostgreSQL superuser:

   ```
   psql -h <host> -U <superuser> -d postgres \
        -v dbname=zoiko -v owner_password='<long random>' -v app_password='<long random>' \
        -f deploy/create_db_roles.sql
   ```

   Use two different long random passwords. Optional `-v owner_role=... -v app_role=...` changes the names.
3. **Run migrations as the owner.** Set the connection variables to the owner's, run, then switch back:

   ```
   POSTGRES_DB=zoiko POSTGRES_USER=zoiko_owner POSTGRES_PASSWORD=<owner password> \
   POSTGRES_HOST=<host> DJANGO_SETTINGS_MODULE=<your production settings> \
   python manage.py migrate
   ```
4. **Give the app login its rights**, still as the owner:

   ```
   ... same variables as step 3 ...
   python manage.py db_grants --role zoiko_app --apply
   python manage.py db_grants --role zoiko_app --check      # must say "exactly as listed"
   ```
5. **Check, then start the web server and the worker as `zoiko_app`**. First `python manage.py check --deploy` must list no `zoiko.E` errors. Then: set `POSTGRES_USER=zoiko_app` and its password in
   their environment (never the owner's). If the login is wrong they stop at start-up with an explanation.

## Every release

```
migrate        (as zoiko_owner)
db_grants --apply, then --check        (as zoiko_owner)
restart the web server and the worker  (as zoiko_app)
```

**Do not skip the grants step.** A table added by a release has no rights for the app until it is applied:
the app fails on it, loudly, instead of quietly using something nobody reviewed. `db_grants` starts from a
clean slate each time, so a right added by hand is taken away again.

## What was rehearsed (2026-11-04, PostgreSQL 16, scratch database)

- The script created both logins (neither a superuser nor `BYPASSRLS`, neither able to create databases or
  roles) and made the owner the database and schema owner.
- `migrate` as the owner built all tables. `db_grants --check` before applying listed 105 missing rights;
  after `--apply` it said "exactly as listed".
- As `zoiko_app`: the web entry point and the worker started; `migrate` was refused ("permission denied").
  The app created two customers and wrote rows for each; **each customer saw only its own rows**; with no
  customer selected it saw **no rows**; asking for another customer's rows by name returned **zero**.
- As `zoiko_app` these were all refused: changing or deleting audit rows, deleting an organization, dropping
  or creating a table, truncating a table.

## Rotating the field-encryption key

Patient details, messages, insurance numbers and staff names are stored encrypted. To change the key:

1. Generate a new 32-byte key and add it to `ZOIKO_FIELD_KEYS` next to the old one, for example
   `k1:<old>,k2:<new>`. Make it the current one with `ZOIKO_FIELD_KEY_ID=k2`. Restart the web server and
   the worker. Old values still work; new values use `k2`.
2. Count what is left on the old key: `python manage.py rotate_field_keys --dry-run`.
3. Move it all: `python manage.py rotate_field_keys` (add `--batch 500` for bigger batches). It is safe to
   run twice or to stop and run again. A value that cannot be read is counted and left exactly as it was.
4. Run the dry run again until it says 0 would move and 0 unreadable. Only then remove `k1` from
   `ZOIKO_FIELD_KEYS` and restart. Old database backups can only be read while the old key is kept
   somewhere safe, so keep it as long as you may need to restore one.

The command prints counts only, never a value. Where the master key lives (a managed key service) is
still open (DEV-016).

## Monitoring and webhooks (DEV-127, DEV-130)

- **Webhook sender:** run `python manage.py deliver_webhooks` as its own long-running process next to the web server and the outbox worker (the `webhooks` service in `docker-compose.yml`). Without it, queued webhook deliveries are never sent. It needs outbound HTTPS to the partners' addresses; it refuses addresses that resolve to private networks.
- **Partner messages:** `POST /api/v1/partner-events` is open to the internet by design (the signature is the credential). Put the usual edge protections in front of it (TLS, a body-size limit of 4 KB or more, and rate limiting by address); the application also limits each partner to 60 messages a minute.
- **Workflow timers:** the outbox worker also runs the workflow timer check every 30 seconds (`orchestration.timers`); if the worker is not running, timers never fire and no escalation opens. Clinical workflow definitions stay unpublishable until `ZOIKO_WORKFLOW_CLINICAL=true` is set, which needs the local clinical approvals first.
- **Metrics:** set `ZOIKO_METRICS_TOKEN` (24+ characters, from the secret store) to let a monitoring system read `GET /metrics` with `Authorization: Bearer <token>`. Leave it unset and the endpoint does not exist. The numbers are per web process and reset on restart; point the monitor at every process and keep the history there.

## Limits and things still to do

- Managed cloud databases: not tried. If your provider has no true superuser, step 2 may need small changes.
- Passwords are passed on a command line here for clarity; use your secrets manager in practice.
- Rotating the two database passwords, backups and restores, and moving between servers are not written.
- The check that the app login is restricted runs when the process starts; it does not notice a login being
  changed while the server is running.
