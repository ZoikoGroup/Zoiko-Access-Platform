# Zoiko Access

Global healthcare access and coordination platform. All design here is PROPOSED, not a launch claim.
Source specifications (GHE-01..23) live outside this repo.

## Top-level map

```
zoikoaccess/
  backend/             all server code (Python / Django)      -> see backend/README.md
  frontend/            all web/mobile clients (later)         -> see frontend/README.md
  docs/                design documents
    schema/            data-model design pack (tables, relationships, enums, role access)
    decisions/         where we differ from, or fill gaps in, the spec (and why)
  docker-compose.yml   local stack for one cell: Postgres + backend
  .env.example         dev placeholders. Copy to .env. NEVER commit .env
  .github/workflows/   CI (active when this folder is its own git repo)
```

Backend architecture and decisions: [backend/docs/ARCHITECTURE.md](backend/docs/ARCHITECTURE.md).
Data model: [docs/schema/README.md](docs/schema/README.md).

## Run it (Docker)

```bash
cp .env.example .env                  # edit if you like; never commit .env
docker compose up --build             # Postgres + backend on http://127.0.0.1:8000
curl http://127.0.0.1:8000/healthz    # {"status":"ok"}
curl http://127.0.0.1:8000/readyz     # {"status":"ready"} once Postgres is up
```

Checks and the no-Docker setup are in [backend/README.md](backend/README.md).

## Rules to remember

- Never commit `.env` or any real secret. Never print environment variables or health data in logs.
- Use specific `git add <file>` paths, never `git add -A`.
- Every change ships with tests, including tests for what must be **denied**.

## Event delivery

`docker compose up` also starts a `worker` service (`python manage.py relay_outbox`) that delivers queued events to their subscribers. Run one pass by hand with `docker compose exec backend python manage.py relay_outbox --once`. See docs/decisions/REGISTER.md (DEV-034).

## Email in development

`docker compose up` also starts Mailpit, a mail catcher. Every email the system sends (verification and password-reset links) appears at http://127.0.0.1:8025 and is never delivered to a real address. The worker sends queued mail; check `docker compose logs worker`. Production needs a real provider (see DEV-036).
