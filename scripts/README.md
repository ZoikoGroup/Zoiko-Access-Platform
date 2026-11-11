# Development helper scripts

DEVELOPMENT ONLY. They call the dev console API (http://127.0.0.1:8000/dev/api/, which exists only
while DEBUG and DEV_AUTH_STUB are on) and use the demo password `Demo-Pass-2026!`. Never point them at
anything real. They need only Python (no packages), and the backend running (`docker compose up -d`).

| Script | What it does |
|---|---|
| `seed_demo_tenant.py "Alpha Clinics"` | A customer with 2 clinics, 6 demo logins (patient, guardian, clinic ops, support, clinician, employer), a catalog and open times. Prints the tenant id. |
| `set_demo_passwords.py <tenant id>` | Gives every login in a tenant the demo password. |
| `seed_platform_tenant.py` | Zoiko's own platform tenant with two support logins and a safety login. Safe to run again: it adds any that are missing. |

Demo platform logins: organization `Zoiko Platform`, `vikram@zoiko.example` or `priya@zoiko.example` (support, R11:
the two people needed to try the two-person suspension) or `iyer@zoiko.example` (safety, R12: can look, cannot decide).

### Simple demo (easy to remember)

`python scripts/seed_simple_demo.py` creates the organization **Demo Clinic** with:

| Login | Password | Who | Opens |
|---|---|---|---|
| venkydoctor@gmail.com | demo@123 | doctor | no app yet |
| reception@gmail.com | demo@123 | receptionist | staff console (5173) |
| patient@gmail.com | demo@123 | patient | patient app (5174) |

`demo@123` is only 8 characters: it works because the development Docker setup sets
`PASSWORD_MIN_LENGTH=8`. Production keeps the default minimum of 10.

### Administrators

`python scripts/seed_admins.py` adds an administrator login (role R13) to the two demo organizations:

| Organization | Login | Password |
|---|---|---|
| Demo Clinic | clinicadmin@gmail.com | demo@123 |
| appolo | appoloadmin@gmail.com | demo@123 |

Sign in to the staff console (http://localhost:5173/) and open **Team** to invite a doctor or a
receptionist. The invitation email appears in the dev inbox (http://127.0.0.1:8025/).

### Doctors

`python scripts/seed_doctor_times.py` gives each demo organization a doctor with six General
Physician times three days from now (creates `appolodoctor@gmail.com` if missing).

| Organization | Login | Password |
|---|---|---|
| Demo Clinic | venkydoctor@gmail.com | demo@123 |
| appolo | appolodoctor@gmail.com | demo@123 |

A patient books one of those times, a receptionist confirms it, and it appears in the doctor app
(http://localhost:5176/).

### Adding a customer

Sign in to the platform console (http://localhost:5175/) as support (`vikram@zoiko.example`,
organization `Zoiko Platform`), open **Customers** and use **Add a customer**. The new
administrator's invitation appears in the dev inbox (http://127.0.0.1:8025/); its link opens the
staff console (http://localhost:5173/), where they choose a password.

### Partner connections, journeys and integrations (demo data)

`seed_console_demo.py` fills the staff console's **Exceptions**, **Journeys** and **Integrations** screens. It
uses the pretend partner (the simulator), so no real partner is contacted, and it refuses to run unless the
backend is in development mode. It runs inside the backend container:

```
docker compose exec -T backend python manage.py shell < scripts/seed_console_demo.py
docker compose exec -T -e DEMO_TENANT="E2E Clinic" backend python manage.py shell < scripts/seed_console_demo.py
```

It makes five partner operations (confirmed, lost reply, partner down, never answers, refused), three journeys
(one overdue, one confirmed, one cancelled) and a sandbox API client with one webhook. Sign in to the staff console
as `clinicadmin@gmail.com` (Demo Clinic) and open the three pages. Run it again for more rows.

