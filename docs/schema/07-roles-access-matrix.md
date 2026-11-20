# 07. Roles x data: who may see/do what

Source: GHE-15 §02 (R01-R12 "minimum accessible experience" and "prohibited by default"), GHE-01 §03, R3.0 §06. The table/journey mapping columns are my design inference from those, marked (INFERRED) where the docs do not state it.

## Rule that overrides this whole table
**A role alone is never sufficient authorization.** Every request is decided by `policy.access_decision` using: actor + role/membership + patient relationship + purpose + legal basis + contract + current consent/authority + jurisdiction + country-pack feature + source-system rights. UI hiding is never enforcement; the BFF and domain services deny independently. Any uncertainty or expired evidence = DENY.

## Role catalog

| ID | Role | Workspace | Minimum experience | Prohibited by default | Journeys (INFERRED) |
|---|---|---|---|---|---|
| R01 | Patient | Patient front door | Own eligible appointments, pathway status, personal permissions, supported records | No foreign chart access; no unapproved record federation | J1 J2 J3 J4 J7 J8 |
| R02 | Guardian / caregiver | Proxy mode inside patient app | Scoped proxy actions per verified authority | No blanket access; child/adult-capacity rules depend on local law | J1 J2 J5 J7 |
| R03 | Clinician / allied practitioner | Professional workspace | Authorized worklist, referral + care coordination, signed attestation | Professional status alone is not permission for arbitrary patients | J2 J3 J5 J7 |
| R04 | Clinic / hospital operations | Provider/hospital console | Org-specific coordination, bookings, referral, exceptions | No clinical judgments through admin role | J1 J2 J5 |
| R05 | Lab / imaging operator | Partner workspace | Order transmission, technical completion, partner evidence | Do not infer reviewed findings from delivery | J2 |
| R06 | Pharmacy / dispensing partner | Partner workspace | Authorized fulfillment, inventory/availability status | Local licensure and prescription controls; no substitution/dispensing by Zoiko | J3 |
| R07 | Payer / insurer / TPA | Payer portal | Eligible coverage, authorization, status, claims | Only contract/purpose-authorized clinical data | J4 |
| R08 | Employer sponsor | Employer portal | Program eligibility, funding, invoicing, approved cohort analytics | **No individual health records, results or appointment details** | J6 |
| R09 | Community / home-care partner | Partner workspace | Accepted care-transition tasks, service capacity | Safeguarding obligations and local qualification | J5 |
| R10 | Integration partner developer | Developer console | Own connector clients, sandboxes, certification, incidents | No production PHI outside contracted scopes | all (technical) |
| R11 | Regional operations / support | Operations console | Scoped exception investigation, support tickets | No standing clinical access; no global break-glass | all (operational) |
| R12 | Clinical safety officer | Safety console | Approved hazard, incident and release-evidence review | Does not replace provider clinical responsibility | gates |

Also in the docs but not in R01-R12: government/NGO/public-health, research/third-party innovators, platform finance roles, DPO/security/legal approvers, Zoiko engineering/SRE. They appear as approver/steward roles in governance tables, not as product workspaces. (INFERRED: model them in `trust.role` with `workspace = NULL`.)

## Data access by role (R = read, W = create/update own scope, A = attest/approve, X = no access)
Columns are table groups from files 02-05. "own" = only their own subject/org/tenant; "rel" = only with a verified relationship + purpose; "minimal" = aggregate/status/metadata only.

| Table group | R01 Pt | R02 Guard | R03 Clin | R04 Clinic ops | R05 Lab | R06 Pharm | R07 Payer | R08 Employer | R09 Comm care | R10 Dev | R11 Ops | R12 Safety |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| person_subject / demographics | R own | R rel+W grant-limited | R rel | R minimal (booking) | R minimal (order) | R minimal | R coverage fields | X | R rel | X | X default | X (incident-scoped only) |
| source_patient_link / match_case | X | X | R rel (own patients) | R minimal | X | X | X | X | X | X | R exceptions only | R incident-scoped |
| relationship_grant | R/W own | R own | X | R status | X | X | X | X | X | X | R status | X |
| care_relationship | R own | R | R/W | R | X | X | X | X | R | X | R | X |
| catalog (org, service, location, listing) | R | R | R | R/W own org | R/W own | R/W own | R | R (eligible services) | R/W own | R sandbox | R/W verification | R |
| booking_intent / partner_booking | R/W own | R/W proxy | R rel | R/W own org | X | X | X | X (no appointment detail) | R accepted tasks | X | R status | X |
| referral / referral_decision | R own status | R proxy status | R/W + A (receiving decision) | R/W | X | X | X | X | R accepted | X | R status | X |
| workflow_instance / task / exception | R own status | R proxy status | R assigned + A | R/W own org | R own orders | R own fulfillment | R claim-related | X | R accepted | X | R/W exceptions | R |
| diagnostic_order / result_ref | R released only | R proxy released | R rel + A attestation | R status | R/W technical state | X | X | **X** | X | X | R technical state | R incident-scoped |
| clinical_attestation | X | X | A | X | X | X | X | X | X | X | X | R |
| medication_ref | R own | R proxy | R rel | X | X | R/W fulfillment | X | **X** | X | X | X | X |
| coverage / eligibility / authorization / claim | R own benefit | R proxy | X | R/W own billing | X | X | R/W own scope + A decisions | X | X | X | R status | X |
| entitlement / invoice / quote / payment | R own | R proxy | X | R/W own org | X | R own | R | R (aggregate invoice only) | X | X | R/W (finance roles) | X |
| comms (message, thread) | R/W own | R/W proxy | R/W rel | R/W | R/W | R/W | R/W (non-clinical) | R notices (aggregate) | R/W | X | R metadata | X |
| evidence_record / audit_event | R own disclosure log | X | R own actions | R own org | R own | R own | R own | X | R own | R own client | R metadata, A for privileged | R |
| partner/api_client/connector/webhook | X | X | X | R status | R/W own connector | R/W own connector | R/W own | X | R own | R/W own + certification | R/W operations | R |
| hazard / safety_incident / safety_case | X | X | R/W (report) | R/W (report) | R (report) | R (report) | X | X | R (report) | X | R/W (report) | R/W/A |
| security / ops / quality / activation | X | X | X | X | X | X | X | X | X | R own | R/W | R |
| analytics / reports | X | X | X | R T1 own org | X | X | R own (aggregate) | R T3 suppressed aggregate | X | X | R T0/T1 | R |

Legend notes:
- **Employer (R08) has X on every clinical table.** Verified by denial tests (GHE-10 W13): individual result / illness lookup → DENIED + AUDIT.
- **Payer (R07)** sees only claims/coverage fields justified by purpose; unrelated clinical narrative is denied.
- **Ops (R11)**: service health + metadata by default; clinical support access only via `policy.privileged_access_grant` (JIT, time-limited, dual approval, logged).
- **Safety officer (R12)** reads incident-scoped evidence; approves release evidence; does not own provider clinical decisions.
- Break-glass exists for clinicians (R03) only, DISABLED until the country pack approves it; any missing precondition = DENY; mandatory post-use review.

## Build order by role (suggested, for P1→P3)
1. **P1 slice (J1):** R01 Patient, R04 Clinic ops (confirms booking), R11 Ops (exception view). Needs: trust, policy, catalog, scheduling, workflow, evidence tables.
2. **P2:** R03 Clinician (referral + attest), R05 Lab/imaging, R02 Guardian, R10 Developer (connector sandbox), R12 Safety officer (hazards, incidents).
3. **P3:** R07 Payer, R08 Employer (data-limited), R06 Pharmacy, R09 Community care.
4. Each role ships with its own **denial tests** (what it must NOT see) before its happy path is considered done.
