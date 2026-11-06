# Build it yourself: a learning path through Zoiko Access

For a developer who wants to write this kind of project by hand and learn from it. The repository is your
answer key: for each step, build your own version first, then compare with the file named. Do not paste;
type. The whole project is far too big to retype (the backend alone is about 11,000 lines plus as many of
tests). This path rebuilds a **thin slice**, the journey *a patient books an appointment and the clinic
confirms it*, and then shows you where each other feature repeats the same pattern.

## Before you start (about a week)

- Python and Django models and views; PostgreSQL basics (tables, joins, transactions); React with TypeScript.
- Read `README.md`, `docs/RUNNING-LOCALLY.md` and the start of `docs/schema/README.md`.
- Get the finished project running (`docs/RUNNING-LOCALLY.md`) and click through it once, so you know what
  you are aiming at.

## Five rules for every step

1. **Write the "denied" test first**: the test that proves the wrong person is refused. Then build the thing.
2. **One small step at a time**; run the tests after each.
3. **Break your own work on purpose** once a step passes: remove a rule and check that a test fails. A test that
   cannot fail proves nothing.
4. **Read the register entry** for the piece you build (`docs/decisions/REGISTER.md`): it says why it was built
   that way and what its limits are.
5. **Keep a notes file** of what surprised you and what you would do differently.

## The steps

Each step has a goal, tasks, a "done when" check, and the files to compare with afterwards (all under `backend/`
unless stated).

### Step 1: a project that runs
- **Tasks:** a Django project; PostgreSQL in Docker; settings read from environment variables (never hard-coded
  secrets); one test that passes.
- **Done when:** `pytest` is green and the app answers a health URL.
- **Compare:** `../docker-compose.yml`, `config/settings/base.py`, `foundation/health.py`.

### Step 2: small shared building blocks
- **Tasks:** an id generator (random public ids, never guessable numbers); time always stored in UTC with a
  clock you can replace in tests; one error type that carries a code and never leaks details.
- **Done when:** tests prove ids are unique and unguessable, and that a fixed test clock gives fixed times.
- **Compare:** `foundation/ids.py`, `timeutil.py`, `errors.py`.

### Step 3: every row belongs to one customer
- **Tasks:** a base model with a customer (tenant) id on every table; a way to say "I am working for customer X"
  for the length of one transaction; refuse to run a query with no customer chosen.
- **Done when:** a test shows code with no customer chosen cannot read or write.
- **Compare:** `foundation/basemodels.py`, `cell_context.py`.

### Step 4: the database as a second fence
- **Tasks:** PostgreSQL row-level security on a test table; a restricted database login that is not a superuser;
  prove that customer A cannot see customer B's rows even if you forget a filter.
- **Done when:** the test passes **as the restricted login** (a superuser skips this fence, which is the point).
- **Compare:** `foundation/rls.py`, `tests/test_rls.py`, `docs/DEPLOYMENT.md` (why two logins).

### Step 5: who is who
- **Tasks:** tenants, organizations, actors (logins), roles; password sign-in with hashing, a lock after five
  wrong tries, and the same error for a wrong login and a wrong password.
- **Done when:** tests cover a good sign-in, a bad one, the lock, and a stolen-token case.
- **Compare:** `modules/trust/models.py`, `modules/trust/auth.py`, `tests/test_auth.py`.

### Step 6: a tamper-proof log
- **Tasks:** an append-only table where every row stores the digest of the one before it; a function that checks
  the chain; make the database refuse UPDATE and DELETE on it.
- **Done when:** editing, removing or swapping any row makes the check fail.
- **Compare:** `modules/evidence/audit.py`, `foundation/chain.py`, `tests/test_audit.py`.

### Step 7: "may this person do this?"
- **Tasks:** a rule check that **denies by default**: role, purpose, organization and relationship to the patient
  must all fit; record every decision, allowed or refused.
- **Done when:** a table-driven test lists who may and who may not, and the refused cases are the longer list.
- **Compare:** `modules/policy/engine.py`, `rules.py`, `tests/test_policy.py`.

### Step 8: the first real journey
- **Tasks:** services and free times; a booking that goes requested, confirmed or cancelled; two people booking the
  same time at once must not both succeed (a database constraint, not just a check in code).
- **Done when:** the whole journey passes as tests, including the double-booking race.
- **Compare:** `modules/catalog/services.py`, `modules/scheduling/models.py`, `services.py`, `tests/test_scheduling.py`.

### Step 9: put it on the web
- **Tasks:** JSON endpoints for that journey, each one asking the rule check from step 7 before acting; clear
  error replies.
- **Done when:** an API test signs in as a patient and as a receptionist and walks the journey, and a stranger gets
  a refusal.
- **Compare:** `api/views.py`, `api/urls.py`, `tests/test_api.py`.

### Step 10: the patient app
- **Tasks:** a React and TypeScript app: sign in, find a service, pick a time, see your appointments. One file
  talks to the backend; pages never call `fetch` themselves.
- **Done when:** `npx tsc --noEmit` and `npx vite build` pass and you can book by clicking.
- **Compare:** `../frontend/patient/src/App.tsx`, `api.ts`, `pages/FindCare.tsx`, `pages/Appointments.tsx`.

### Step 11: the staff console
- **Tasks:** a second app where the receptionist confirms and cancels; share what is common (buttons, cards) in one
  shared folder.
- **Done when:** you book as the patient and confirm as the receptionist, end to end.
- **Compare:** `../frontend/operations/src/App.tsx`, `../frontend/shared/src/ui.tsx`.

**After step 11 you have the core product.** Everything else repeats these patterns:

| Next idea | Same pattern as | Look at |
|---|---|---|
| Emails and notices after something happens | write an event in the same transaction, a worker delivers it | `foundation/outbox.py`, `foundation/mail.py`, `modules/comms/subscribers.py` |
| Referrals, waiting list, messages, family access | a small module with its own rules and policy check | `modules/referral/`, `waitlist/`, `messaging/` |
| A work queue and histories | events open and close items; every step is chained | `modules/workflow/services.py`, `scheduling/history.py` |
| A staff console for Zoiko itself | a different kind of tenant with its own roles | `modules/trust/releases.py`, `incidents.py`, `suspensions.py` |
| Making it safe to deploy | refuse to start on unsafe settings | `foundation/dbrole.py`, `release_check.py`, `dbgrants.py` |

## If you get stuck
Make the smallest version that fails a test, then fix it. If a step feels too big, split it and write the test for
the first half only. Come back to the register entry for the piece: it lists the mistakes already made on the way.
