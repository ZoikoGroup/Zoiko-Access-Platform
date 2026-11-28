# Zoiko Access: handover

Written 2026-11-17. Read with `docs/PROGRESS.md` (how far along), `docs/BACKLOG.md` (what is left) and
`docs/decisions/REGISTER.md` (every decision and change, DEV-001 onwards).

## Where we are

About 35% of the whole product by effort (estimate, see PROGRESS.md). Every one of the 13 roles can sign in and
reach its own home screen. The product is **not ready for real patients**: hosting, a mail and SMS provider,
legal, privacy and clinical-safety reviews, and several clinical features are still to come.

## What works (all in the running system)

| Area | State |
|---|---|
| Patients | Find care, **their own signed notes (24 hours after signing)**, **agreeing to each claim**, **joining an employer programme with a one-time code**, book, move and cancel, referrals, family and guardians, insurance sharing, messages, privacy views, download my data, **tests and requests made for them, with agreement to share their name** |
| Clinic staff | **Claims to payers**, appointments, people, services and listings, waiting list, referrals, messages, team, reports, workflows, partner requests, integrations (webhooks, keys) |
| Doctors | Schedule, patients, referrals, messages, **orders to labs, pharmacies and home care**, **lab summaries with a critical flag**, partner results to review, **consultation notes** |
| Partners (lab, pharmacy, home care, payer, employer, developer) | Own home screen, services, team, incoming requests, plans and members, API keys, webhooks, documents |
| Zoiko platform staff | Customers, country packs, service levels, suspending a customer, hazards, releases |
| Safety | Every access decision recorded; histories cannot be edited; per-customer walls enforced in the database; sensitive fields encrypted; development settings refused in production |

## Decisions the project lead made on 17 Nov 2026 (built)

Further decisions the same day: a patient reads their own signed notes 24 hours after signing (DEV-306); the lab sends a text summary with a rating and a critical result stands out (DEV-307); the payer sees only member id, service, amount and date, and the patient agrees to each claim first (DEV-308, DEV-311); email alerts for a critical result and a request held too long (DEV-310); an employer sees monthly totals for people who join with a one-time code, groups of 10 or more only (DEV-312); the sandbox sign-off stays as the other session built it (DEV-230).

1. A partner learns **only a patient's name and birth date**, **only after the patient agrees for that one order** (DEV-300).
2. The agreement is **per order, lasts 30 days, can be taken back**; taking it back erases what was shared (DEV-300, DEV-301).
3. A partner gets no key until identity, legal and security checks are recorded (already built, DEV-174).
4. Zoiko may **store consultation note text, encrypted**; a signed note is **never edited**, a correction is a new version (DEV-304).
5. A held partner request escalates to the clinic administrator after **3 days** (DEV-303).
6. Data standard to plan for: **FHIR only, one pinned version, one country first** (decided, not started).

## Switches that must be set on purpose

| Setting | Effect | Default |
|---|---|---|
| `ZOIKO_CLINICAL_NOTES=true` | Turns consultation notes on. With it off every notes route answers not-found. | off |
| `CLINICAL_REFERENCES_ENABLED` | Pointers to records in a clinic's own system (DEV-041). | off |

Turn notes on only where the legal and clinical-safety reviews are done.

## What is not built

- **Clinical:** all core backlog items built (result values and trends, DEV-330, DEV-372; structured diagnoses, active medications and document/media attachments, DEV-344, DEV-375, DEV-376; NHS 8-years-after-death retention and AHRA proxy termination, DEV-377).
- **Interoperability:** HL7 v2, imaging, a real adapter for a named lab (only the pretend one exists), re-running held messages once a code mapping is approved.
- **Money and cover:** employer counts by kind of service (declined for now); claim attachments and remittance advice built (DEV-378); patient online bill payment and checkout built (DEV-374).
- **Partners:** a pretend clinic for end-to-end sandbox orders; separate sandbox keys (the synthetic-patient sandbox is built, DEV-329).
- **Channels:** text messages and push (email alerts for critical results and escalations are built, DEV-310).
- **Operations:** more than one region; paging on lists other than the two request lists. (Regional support's view of delivery attempts is built, DEV-328.)

## What only people can supply

Hosting and a production database; a mail provider and SMS provider with registered senders; the privacy,
legal and clinical-safety reviews; partner contracts; FHIR partners to test against; a retention rule for notes.

## Running it

`docker compose up -d` for the backend, then `npm run dev` in each of `frontend/operations`, `patient`,
`platform`, `clinician`, `partners`. One-click test sign-in links for 12 logins: `docs/LOGIN-LINKS.md`.
Test accounts only. Browser tests: `frontend/e2e/README.md` (robots 1 to 23). Run the backend tests in a
one-off container with your own database name: `docker compose run --rm --no-deps -e POSTGRES_DB=<name> backend ...`.

## Working notes for the next developer

- Two Claude sessions worked in this tree. Register numbers: DEV-001 to 230 and DEV-300 onwards; check the register for the last one before adding an entry.
- Never restart the backend or worker containers while another session may be testing in them.
- `ruff` currently reports a few long lines in files from the other session (`api/views.py` identity comment, `modules/trust/oncall.py`, `config/settings/base.py` Twilio URL).
- After changing any route, refresh the OpenAPI snapshot (`ZOIKO_WRITE_OPENAPI=1`) and run `python scripts/consumer_contract.py`.
- New `*_ref` fields must be catalogued in `backend/foundation/identifiers.py`.
