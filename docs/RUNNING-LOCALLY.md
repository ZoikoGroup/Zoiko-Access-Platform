# Running Zoiko Access on your own computer

Development only. Nothing here is for a real server (that is `docs/DEPLOYMENT.md`).

## What runs where

| Part | Address | Started by |
|---|---|---|
| Backend (the API) | http://127.0.0.1:8000 | Docker |
| Database (PostgreSQL) | 127.0.0.1:5432 | Docker |
| Worker (events, email, scheduled jobs) | none | Docker |
| Mail inbox (catches every email) | http://127.0.0.1:8025 | Docker |
| Staff console (clinics) | http://localhost:5173 | `frontend/operations` |
| Patient app | http://localhost:5174 | `frontend/patient` |
| Platform app (Zoiko staff) | http://localhost:5175 | `frontend/platform` |
| Doctor app | http://localhost:5176 | `frontend/clinician` |

## Start everything

1. **Docker** (backend, database, worker, mail inbox): from the project folder run `docker compose up -d`.
   Docker may have stopped overnight or after a restart: run it again. Check with `docker compose ps`
   and open http://127.0.0.1:8000/readyz (it should say `ready`).
2. **Each app**, in its own terminal, from its folder. Always pin the port:

   ```
   cd frontend/operations && npm run dev -- --strictPort --port 5173
   cd frontend/patient    && npm run dev -- --strictPort --port 5174
   cd frontend/platform   && npm run dev -- --strictPort --port 5175
   cd frontend/clinician  && npm run dev -- --strictPort --port 5176
   ```

   `--strictPort` makes an app refuse to start if its port is taken. Without it the app silently moves to
   another port, and then an address you know opens the wrong app (this caused a blank page once).
   The first time only, run `npm install` in each app folder.

## One page with every link

Run `python scripts/dev_links.py`. It writes `dev-links.html` (ignored by git) and opens it: every app, the mail inbox, the developer console and the API check, with a green or red dot for each, and an **Open signed in** button for each demo account (no typing). The button uses a development-only sign-in that is removed from production builds.

## Demo logins (development only; the demo password is in `scripts/README.md`)

| App | Organization | Login | Password |
|---|---|---|---|
| Staff console, clinic administrator | Demo Clinic | `clinicadmin@gmail.com` | `demo@123` |
| Staff console, receptionist | Demo Clinic | `reception@gmail.com` | `demo@123` |
| Patient app | Demo Clinic | `patient@gmail.com` | `demo@123` |
| Doctor app | Demo Clinic | `venkydoctor@gmail.com` | `demo@123` |
| Platform app, support | Zoiko Platform | `vikram@zoiko.example` | see `scripts/README.md` |
| Platform app, second support person | Zoiko Platform | `priya@zoiko.example` | see `scripts/README.md` |
| Platform app, safety officer | Zoiko Platform | `iyer@zoiko.example` | see `scripts/README.md` |

The seed scripts (`scripts/`) create these. To try the two-person suspension in the platform app, sign in as
`vikram` and ask to suspend a customer, then sign out and sign in as `priya` to approve it (the person who asked
cannot approve their own request). `python scripts/seed_platform_tenant.py` adds any of the platform logins
that are missing and is safe to run again.

## If a page is blank or wrong

1. Press `Ctrl+Shift+R` (a normal reload can keep an old broken copy), or open a private window.
2. The apps now show a **"Something went wrong on this screen"** page (or a red bar at the top) with the
   error text instead of a blank page. Copy that text. If the page is still fully blank, open the browser
   Console (F12) and copy the red line.
3. Check the address matches the app: the tab title is "Zoiko Access Operations" on 5173, "Zoiko Access
   Platform" on 5175, "Zoiko Access Doctor" on 5176.
4. If an app printed "Port ... is in use", an old copy is still running. Stop it (close its terminal), then start again.
5. **Do not delete `node_modules/.vite` while an app is running**: the running copy then serves broken files.
   Stop the app first.

## Looking at the database

Host `127.0.0.1`, port `5432`, database `zoiko_dev01`, user `zoiko`; the password is `POSTGRES_PASSWORD` in your
`.env`. Quick shell: `docker compose exec db psql -U zoiko -d zoiko_dev01`. In development the app connects as a
superuser, so the database's customer separation is not enforced here (`docs/DEPLOYMENT.md` explains why
production differs).

## Checks before you stop for the day

Run these from the project folder. Run **one test run at a time**: they share one test database.

```
# backend: tests (all of them take about 25 minutes; run a few files while working)
docker compose exec -e DJANGO_SETTINGS_MODULE=config.settings.test backend pytest tests/test_work_history.py -q

# backend: style, types, import rules, migrations that are missing
docker compose exec backend sh -c "ruff check . && mypy . && lint-imports && python manage.py makemigrations --check --dry-run"

# each app: types and a build
cd frontend/operations && npx tsc --noEmit && npx vite build
# recovery rehearsal: back up the database, restore into a scratch copy, compare, check the audit chains
python scripts/recovery_drill.py

# browser tests: robots that click through the real apps (stack and the four apps must be running)
python scripts/seed_e2e.py          # once: makes a separate "E2E Clinic"
cd frontend/e2e && npx playwright test
```

After adding a column to a model, create the migration and apply it at once
(`docker compose exec backend python manage.py makemigrations <app> -n <name>`, then `... migrate`), then restart the
worker (`docker compose restart worker`).

## Where things are written down

- `docs/decisions/REGISTER.md`: every decision and deviation (DEV-001 onward), with limits.
- `docs/DECISIONS-NEEDED.md` and `docs/LEAD-NOTE.md`: the questions only the business can answer.
- `docs/BACKLOG.md`: what is built, what is blocked, what is left.
- `docs/DEPLOYMENT.md`: the database and start-up rules for a real server.
