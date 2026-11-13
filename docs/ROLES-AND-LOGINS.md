# Zoiko Access: roles, logins and what each can do

Written 2026-11-13 by the developer. The specification names 12 roles (R01 to R12). We added R13 (the customer's own administrator, DEV-037). Every one of the 13 can sign in. "Complete" below means built against what the specification asks for; it is the developer's reading, not a measurement.

## Where to sign in (run locally)

| App | Address | Roles |
|---|---|---|
| Patient app | http://localhost:5174 | R01 patient, R02 guardian or caregiver |
| Staff console | http://localhost:5173 | R04 clinic operations, R13 organization administrator |
| Doctor app | http://localhost:5176 | R03 clinician or allied practitioner |
| Platform console | http://localhost:5175 | R11 regional support, R12 clinical safety officer |
| Partner app | http://localhost:5177 | R05 lab, R06 pharmacy, R09 home care, R07 payer, R08 employer, R10 partner developer |

Sign-in asks for the organization (the customer), the login and the password. A person who signs in to the wrong app is told which app is theirs and is given no session. Test logins: `scripts/seed_e2e.py` (clinic, patient, doctor), `scripts/seed_platform_tenant.py` (support and safety), `scripts/seed_partners.py` (one partner, payer, employer and developer customer each). The password for the test accounts is `E2e-Pass-2026!`.

## Role by role

| Role | Home screen | What it can do today | What is not built |
|---|---|---|---|
| R01 Patient | Dashboard | Find care, book and change appointments, referrals and messages, tests and requests made for them (status only), bills, data requests, family and guardians, insurance sharing | Identity matching, emergency access, record exchange |
| R02 Guardian | Patient app in proxy mode | The same, for the person they act for, after the clinic approves | Same as R01 |
| R03 Clinician | My schedule | Schedule, my patients (only through a care relationship), referrals, messages with their own patients, marking a partner's result as reviewed, a stated reason for every look | Notes, orders and results (no clinical record service) |
| R04 Clinic operations | Dashboard with what is waiting | Appointments, people, services and listings, messages, referrals, data requests, billing, waiting list, workflows, reports | Clinical judgments (by design) |
| R13 Organization administrator | Same dashboard, plus admin screens | Everything R04 does, plus team, listing review and publishing, integrations (webhooks, partner keys), workflow definitions | Platform roles (by design) |
| R11 Regional support | Platform overview | Onboard customers of every kind, set a customer's country, country packs, service levels, requests to suspend a customer | Standing clinical access (by design) |
| R12 Clinical safety officer | Platform overview | Sign off country packs and hazards, readiness vetoes | Intended-purpose classification, result review, critical-result routing |
| R05 Lab, R06 Pharmacy, R09 Home care | Overview with numbers | My services (add, ask for a listing, a colleague reviews it), incoming requests from clinics (accept, decline, start, mark a result available: status and references only), team | Patient identity and clinical content, a patient's consent to share |
| R07 Payer | Overview with numbers | Plans, members, a cover check that says why not, team | Claims, a clinic asking the payer (same missing exchange) |
| R08 Employer | Overview with numbers | Enrol people by an opaque reference, counts only with small groups hidden, team | Counts about visits and bookings (same missing link) |
| R10 Partner developer | Overview with numbers | API keys (secret shown once), webhooks and the delivery log, the OpenAPI and AsyncAPI documents, team | A sandbox with synthetic patients, certification, onboarding checks |

## Rules that hold for every role

- What a role may reach comes from one rules table, `backend/modules/policy/rules.py`. The partner app's Overview reads that same table to tell a person what their role may do, so it cannot disagree with the server.
- Employers see counts only and never an individual; a group smaller than 10 is hidden. An employer's login holds only that role.
- Every look at a patient is recorded with a stated reason.
- Anything that grants more than the specification states is marked TEMPORARY in `docs/decisions/REGISTER.md` and waits for the lead's confirmation (`docs/LEAD-NOTE.md`).

## What is left, and who decides

Everything remaining needs a decision or a vendor from outside the project: identity matching and single sign-on, the clinical record and the exchange between separate customers (labs, pharmacies, payers' claims, employer usage counts), clinical safety and regulated AI, real SMS, push and phone channels, and multi-region operations. See `docs/LEAD-NOTE.md`.
