# Browser tests ("robots")

These tests open the real apps in a headless browser and click like a person. Development only.

## Before you run them

1. The stack is up: `docker compose up -d` (API on 8000). If the API is on another port, start the apps with `BACKEND_URL=http://127.0.0.1:<port>` and run the robots with `E2E_API=http://127.0.0.1:<port>/api/v1/`.
2. The four apps are running, each on its own port (start each with `npm run dev -- --strictPort --port <n>`):
   staff 5173, patient 5174, platform 5175, doctor 5176.
3. The test clinic exists: `python scripts/seed_e2e.py` (once). The platform robot also needs the
   support logins: `python scripts/seed_platform_tenant.py` (once).

If an app is not on its usual port (for example another project holds 5173), start it elsewhere and tell the robots with `E2E_STAFF`, `E2E_PATIENT`, `E2E_PLATFORM` or `E2E_DOCTOR` (for example `E2E_STAFF=http://localhost:5183`).

## Run

```
cd frontend/e2e
npm install                      # first time only
npx playwright install chromium  # first time only
npx playwright test              # all robots, about one and a half minutes
npx playwright test tests/03     # one robot
```

A failed robot leaves a screenshot and a trace under `test-results/` (not committed).

## The robots

| # | What it does |
|---|---|
| 1 | A patient books a free time and sees "Waiting for the clinic". |
| 2 | Clinic staff confirm it; the patient then sees "Confirmed". |
| 3 | The doctor sees the booking only after the clinic confirms it. |
| 4 | A patient moves an appointment, reads its History, then cancels with a reason. |
| 5 | One support person asks to suspend the test clinic, a different one approves, its staff are refused, and two people reinstate it. |
| 6 | Signs in to each of the four apps and opens every page in its menu; fails on a crash screen, a browser error or a server error (5xx). |
| 7 | The clinic administrator proposes an AI permit, moves it one step, retires it with a reason (it never switches anything on). |
| 8 | The administrator records that a doctor's name was checked; a patient sees "Verified" next to that doctor's free times; withdrawing the check removes it. |
| 9 | Staff see a patient's insurance only after the patient shares it, and lose sight of it when the patient takes the choice back. |
| 10 | An automated accessibility scan (WCAG 2.2 AA rules) of every page of all four apps and the sign-in pages; fails on any serious or critical finding. |
| 11 | Every "Open signed in" button on the local launcher page really lands signed in, and the password leaves the address bar. |
| 12 | One safety officer assesses a hazard, a different one verifies and accepts it, and the first cannot review their own work. |
| 13 | A patient asks the clinic about their own data; staff record the identity check (an administrator cannot close it before that) and close it with a note; the patient sees the outcome. |
| 14 | A patient who ticks "Remember me for 30 days" is still signed in on a device that kept only long-term storage, and "Sign out" ends it; one who does not tick it is not; staff cannot tick it. |
| 15 | The patient and platform apps, when a refresh fails after a good load, keep the old page and say "The information below is old: it was last updated at <time>". |
| 16 | The clinic administrator sets a price, staff make, issue and mark an invoice as paid with a payment reference, and the patient sees it as paid in their own app (invoices from earlier runs stay in the list). |

## Good to know

- They use their own "E2E Clinic", never your demo data. Each robot adds its own free times and
  cancels what it books, so you can run them again and again. Cancelled cards from earlier runs stay
  in the patient's list.
- Robots read screen words, so changing a button's wording means updating the robot.
- One robot at a time (they share one clinic). Nothing runs them automatically yet.
