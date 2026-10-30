# Frontend

One app per audience (docs/schema/07-roles-access-matrix.md), because each role sees different data.
Rule: the frontend may hide buttons for convenience, but the backend always enforces permissions.

```
frontend/
  platform/     Zoiko's own staff: health of every customer, numbers only (port 5175)
  clinician/    Doctors: my schedule and my patients (port 5176)
  operations/   staff and operations console (dashboard, patients, organizations, services, appointments, compliance)
  patient/      patients and guardians            (R01, R02)    dashboard, Find Care, Appointments
  clinician/    doctors: schedule and patients    (R03)         built (port 5176)
  shared/       design system, API client         (extract when a second app needs it)
```

## Run the operations console

Needs the backend running (`docker compose up -d` from the project root).

```
cd frontend/operations
npm install
npm run dev          # http://localhost:5173
npm run typecheck
npm run build
```

Vite forwards `/api`, `/healthz` and `/readyz` to the backend on 127.0.0.1:8000
(override with `BACKEND_URL`). Open `http://localhost:5173/?tenant=<id>` to jump to a tenant.

## Run the patient app

```
cd frontend/patient
npm install
npm run dev          # http://localhost:5174
```

It signs in with the backend's development login (no password). For a direct link while
developing: `http://localhost:5174/?tenant=<tenant id>&as=<patient login id>&page=find`.
Seed demo data first (dev console at http://localhost:8000/, "Load demo data", then add a
catalog with its API actions `location`, `service`, `slots`).

The two apps copy `styles.css` and `ui.tsx`. Extract `frontend/shared/` when a third app needs them.

## Run the platform console

```
cd frontend/platform
npm install
npm run dev          # http://localhost:5175
```

Sign in as organization `Zoiko Platform`, login `vikram@zoiko.example` (support) or `iyer@zoiko.example`
(safety), password `Demo-Pass-2026!`. Create them with `scripts/seed_platform_tenant.py`. Every view is
recorded in the platform audit log, so the page loads on open and on Refresh, with no polling.

## Signing in (development)

Both apps have a real sign-in form: organization name, login, password. The backend decides
which area a person belongs in (patient, operations, ...) and each app turns away the others.

Demo data created by the scripts in `scripts/` uses the development-only
password `Demo-Pass-2026!`. Never use it anywhere real. Demo logins in the tenant "Alpha Clinics ...":

| Login | Role | App |
|---|---|---|
| asha.patient@example.org | patient (R01) | patient (5174) |
| parent@example.org | guardian (R02) | patient (5174) |
| ops@north.example | clinic operations (R04) | staff console (5173) |
| support@zoiko.example | support (R11) | staff console (5173) |
| dr.rao@north.example | clinician (R03) | no app yet |
| hr@employer.example | employer (R08) | no app yet |

New logins: dev console API action `set-password` (`actor`, `login`, `password`).
Turn patient self-registration on for an organization: dev API action `self-registration` (`tenant`, `enabled`).
The patient app has Create an account; the staff console does not (staff are created by an administrator).
Emails (verify, reset) land in the dev inbox at http://127.0.0.1:8025.
Google sign-in is a disabled button until someone creates a Google OAuth client id (DEV-031).

## Rules for this app

- Every number on screen comes from the backend. A screen with nothing real behind it says
  "not built yet" and names the backend step it waits for. No invented data.
- All HTTP goes through `src/api.ts`. Pages never call `fetch` directly.
- Colors and spacing are tokens in `src/styles.css` (light and dark).
- Sign-in is real (DEV-030) and every staff screen uses authenticated, policy-checked `/api/v1` endpoints
  (DEV-032). Staff screens never show a patient's email. The dev console (port 8000) is a separate dev tool.

Staff console dev link for an account made by the seed scripts: add `&issuer=local` (for example `?tenant=<id>&as=appoloadmin@gmail.com&issuer=local&page=team`).

## Run the doctor app

```bash
cd frontend/clinician
npm install
npm run dev          # http://localhost:5176
```

Sign in with organization `Demo Clinic`, `venkydoctor@gmail.com` / `demo@123`
(create the times with `scripts/seed_doctor_times.py`).
