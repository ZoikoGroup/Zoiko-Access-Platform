# Backend

Python 3.12 + Django 5.2 + PostgreSQL 16. One deployable project (a "modular monolith"): one codebase,
split into business modules that are not allowed to reach into each other.

## Folder map: what lives where, and why

```
backend/
  manage.py          Django's command line (run server, migrations, checks)
  pyproject.toml     tool settings: lint rules, type checker, test runner, ARCHITECTURE RULES
  Dockerfile         how the backend image is built
  requirements/      base.txt = what runs in production, dev.txt = + testing/lint tools

  config/            Django WIRING only. No business logic here.
    settings/        base.py (shared), dev.py, test.py. Values come from environment variables.
    urls.py          which URL goes to which code

  foundation/        SHARED PLUMBING every module may use. Never contains business rules.
                     (ids, time, errors, outbox, idempotency are added here, step by step)

  modules/           THE BUSINESS. One folder per area; each owns its own data and rules.
    trust/           who people and organizations are, roles, delegation        (D01)
    policy/          may this actor do this, for this patient, for this purpose (D02)
    catalog/         providers, services, locations                              (D03)
    scheduling/      booking requests, availability, confirmed bookings          (D04)
    workflow/        the step-by-step journeys and who owns each step            (D05)
    comms/           messages and delivery tracking                              (D16)
    evidence/        audit trail and proof records                               (D17)

  tests/             automatic checks
  docs/              ARCHITECTURE.md and decision records
```

### How to read the structure

- `config/` = *how the app is started*. `foundation/` = *tools*. `modules/` = *the product*.
- Inside every module (filled in as we build it):

  | File | Job |
  |---|---|
  | `models.py` | the database tables this module owns |
  | `domain.py` | pure rules and state machines (no database, easy to test) |
  | `services.py` | use-cases; the only place that opens a database transaction |
  | `api.py` | **the front door**: the only file other modules may import from this one |
  | `http.py` | turns web requests into service calls (thin) |
  | `events.py` | events this module sends or listens to |
  | `tests/` | tests for this module |

- **The one big rule:** module A may import module B *only* through `modules/B/api.py`.
  The tool `lint-imports` fails the build if someone breaks it, so the structure cannot rot quietly.

## Checks (run from this folder)

```bash
docker compose run --rm backend ruff check .                      # lint + security rules
docker compose run --rm backend lint-imports                      # architecture boundaries
docker compose run --rm backend mypy foundation modules config    # types
docker compose run --rm backend pytest                            # tests
```

Without Docker (Python 3.10+, reachable Postgres): `python -m venv .venv`, activate it,
`pip install -r requirements/dev.txt`, set the variables from `../.env.example`, then run the
same tools directly (set `PYTHONPATH=.` when running from this folder).
