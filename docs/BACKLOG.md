# Zoiko Access: what is left to build

Written 2026-11-04. Built work is recorded in `docs/decisions/REGISTER.md` (DEV-001 to DEV-073); questions only your lead can answer are in `docs/DECISIONS-NEEDED.md`. The sizes below are our own rough guesses, not measured.

**Size:** S = about a day, M = a few days, L = a week or more.

## A. Can be built now (nothing is waiting on a decision)

| # | Item | Size | Notes |
|---|---|---|---|
| A1 | ~~Platform app **Settings** page~~ | done (first setting) | Built as DEV-079 (open or close a customer's patient sign-up). Suspension with two-person approval built as DEV-080. |
| A2 | ~~Platform app **Incidents** log~~ | done | Built as DEV-078 (provisional low/medium/high labels). |
| A3 | ~~Platform app **Releases** page (log only)~~ | done | Built as DEV-077. |
| A4 | ~~Staff **Reports**: more real numbers~~ | done | Built as DEV-076 (missed visits by doctor, busiest weekdays). |
| A5 | ~~Staff **Compliance** page: more checks~~ | done | Built as DEV-074 (refused checks by person and by day). |
| A6 | ~~Patient **Privacy**: see who looked at my record~~ | done | Already built earlier as DEV-050. (This row was listed by mistake.) |
| A7 | One-time clean **Docker rebuild** check | S | Failed twice on network timeouts; needs a steady connection. Verification only. |
| A8 | **Browser click-through** of the main screens | M | Partly automated: robot 6 opens every menu page and checks for crashes and server errors (DEV-092). A human still has to judge how the screens look and read. |
| A9 | ~~**Full test run** and a fresh-clone check~~ | partly done | Full test run on 2026-11-04: 959 passed. The fresh-clone check is not done (see A7). |
| A10 | ~~Remove a **single** free time (not a range of days)~~ | done | Built as DEV-075. |
| A11 | ~~Tell patients when a free time they had open was removed~~ | dropped | Nobody holds a free time until they book it, and booked times cannot be removed, so there is nobody to tell. |

## B. Waiting for a decision from your lead (do not start)

| # | Item | Size | Waiting for |
|---|---|---|---|
| B1 | Clinical notes and results (doctor "Notes & results") | L | DEV-041: may Zoiko store clinical notes at all, and how they are encrypted. |
| B2 | Doctor messaging (doctor "Messages") | M | Same clinical-safety questions as B1. |
| B3 | Billing, claims and entitlements (staff "Billing & Claims"; patient "Bills & Payments") | L | Legal role of Zoiko per service (DEV-012) and the first market (DEV-011). |
| B4 | Insurance checks with the insurer; submitting claims | L | Same as B3, plus a partner connection. |
| B5 | Patient consent choices (who may see what) | L | First piece built (DEV-096): a patient decides whether clinic staff may see their insurance details; default is not shared. Everything else (appointment history, guardians, other staff roles, a general consent record) still waits on the privacy decisions; today other access follows roles only. |
| B6 | Emailing guardians | S | Consent decision for guardians. |
| B7 | Verified doctor names | M | First version built (DEV-095): one clinic administrator records a check, patients see a Verified mark, and a name change ends it. Decision 25 is still open: who really verifies, whether it takes two people, and what proof counts. Needed before a public doctor directory. |
| B8 | Emergency wording in patient emails | S | Decision 24: clinical safety review of the text. |
| B9 | Google sign-in | M | Choice of sign-in provider (DEV-016). |
| B10 | Real mail provider | S | Choice of provider and sending domain (DEV-016). |
| B11 | Retention periods, key rotation, data transfer rules | L | DEV-013: legal and privacy. |
| B12 | One incident severity scale | S | DEV-014. Built on GHE-20's SEV-0 to SEV-3 as a temporary choice (DEV-118); owners still to confirm. |
| B18 | Seven privacy and safety gaps found by the conformance audit (patient scope per site, care-link site and tail, guardian rights, pending reschedule and cancel states, referral owner and fallback, purpose reasons, role by customer type) | M each | Lead decisions listed in docs/CONFORMANCE-AUDIT.md section 2 and the lead note. |
| B17 | Safety controls, intended-purpose statements and the qualification dossier (the rest of the safety tables) | M | Needs a clinical safety lead to define them; the hazard register itself is built (DEV-103). |
| B13 | Statuses the documents never list (qualifications, claim verification, and similar) | M | DEV-015. |
| B14 | AI features: who may create and approve AI permits, and which AI features, if any, to switch on | M | Lead: roles for Legal, Clinical, QA and SRE sign-off. The rules, tables and a staff screen exist and are off; today any clinic administrator (R13) may use the screen, a temporary rule (DEV-093, DEV-094). |
| B15 | A real AI model provider (today only a fixed template) | M | Lead: cost, contract, region and privacy approval. No patient data may reach an outside model without it. |
| B16 | AI evaluation, drift monitoring, safety events, and carrying out an approved AI action | L | After B14 and B15; clinical safety review for anything beyond admin help. |

## B2. From the Product Orientation document: the minimum vertical slice (section 12)

The source documents say the first deliverable is a working slice of ONE journey with eight parts. Where we stand:

| # | Part (shortened) | Status |
|---|---|---|
| 01 | A signed release scope: market, buyer, operator, pathway, partner, cell | **Not built.** Needs the first market and buyer (G0, DEV-011). |
| 02 | Verified person and organization authenticate; matching, relationship and purpose are checked | Built. |
| 03 | An accurate service listing; a request is accepted; the provider (not us) confirms | Built. |
| 04 | A versioned, durable workflow with accountable parties, transitions and exceptions | Built as an engine on the database (DEV-122): versioned definitions, version-pinned journeys, evidence rules, timers, exceptions, and a Journeys screen. Not yet wired into bookings (the clinic still confirms by hand). Whether to adopt a Temporal-class engine later is still ADR-08. |
| 05 | At least one real contracted partner connector with provenance, timeout and reconciliation | Framework, pretend partner and an Exceptions screen are built (DEV-121, DEV-125): provenance, timeouts, reconciliation, retries, redrive. **A real contracted partner adapter is still not built:** it needs a partner. |
| 06 | Protected UIs with correct statuses and accessibility testing | Built; automated accessibility scan added (DEV-098). A hand test with a screen reader and keyboard is still needed. |
| 07 | Denial of unauthorized employer access and unauthorized cross-cell disclosure, shown by tests | Built: `backend/tests/test_prohibitions.py` has one named test for each of the five product prohibitions (DEV-099). Prohibition 3 (critical results) is only a tripwire because results are not built. |
| 08 | Release evidence: test runs, clinical sign-off, a regional recovery test, country approval | Test runs: yes. Backup-and-restore rehearsal: **done at development size** (`python scripts/recovery_drill.py`, evidence in `docs/evidence/`, DEV-100); a regional failover, scheduled backups and production-size timing still need the hosting decisions. Sign-offs and approval: need people. |

Nothing further in this list can be built without decisions or people: parts 01 and 05 need the first market and a partner, 04 needs the workflow-engine choice (ADR-08), and the sign-offs in 08 need named people.

## C. Needs another team or outside system

| # | Item | Size | Notes |
|---|---|---|---|
| C1 | Staff **Integrations** (connectors, webhooks, partner simulator) | L | Built against the pretend partner (DEV-121, DEV-124, DEV-125): connector framework, signed webhooks, partner API, and the Exceptions, Journeys and Integrations screens. **Still needs a real partner** to test against, and the message broker choice (DEV-016) only if volume ever needs one. |
| C2 | Patients by region and other multi-site views | L | Needs more than one cell; not planned yet. |
| C3 | Forecasts and health overview on the patient dashboard | L | Needs the clinical record service (B1). |

## D0. Built from the schema design (docs/schema)

| Item | Status |
|---|---|
| `scheduling.booking_event` (booking history) | built as DEV-081, with the differences noted there |
| `workflow.workflow_transition` (history of each work item) | built as DEV-082 |
| Move the booking history onto the shared chain helper (`foundation/chain.py`) | built as DEV-086, with a digest version on each row so old rows still check out |
| Database login that cannot skip row-level security: start-up guard (DEV-083), rights list, setup script and guide (DEV-084) | built |
| Reports: count "bookings made each day" by the clinic's own day instead of UTC | built as DEV-133, using the time zone of the clinic's places (UTC when they differ or there are none) |
| Production setup beyond the database (servers, TLS, secrets, backups, mail provider) | not built; waits on decisions DEV-013 and DEV-016 |
| Suspension with two-person approval (design: tenant-level actions need two people) | built as DEV-080 |
| Next candidates from the first-phase list that need no decision: `workflow` instance, transition and timer rows (only if a workflow engine is adopted: ADR-08 is still open); a separate booking intent and partner booking; `trust.facility` (our organization and location already cover it) | not built |
| `catalog.practitioner`, `practitioner_role`, `verification_claim`, `listing` and the directory projection | **blocked**: the design publishes only verified, source-qualified claims, with a second approver for regulated cases. Verifying doctors is open question 25; until then doctors stay self-typed names. (Corrects an earlier note that listed this as decision-free.) |

## D. Data model

The design documents describe 244 tables; about 25 exist (roughly 10%). Most of the other tables belong to items B1 to B5 and C1 to C3, so this number will not move much until the decisions in B are made.

## How we suggest to continue

1. Do **A7 to A9** soon: they are checks, not features, and they find problems the tests cannot.
2. Then build **A1 to A6**, one per step.
3. Ask your lead for answers to **B1, B3 and B9 to B10** first: those unblock the most work.
