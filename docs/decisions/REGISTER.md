# Deviation register

Seeded 2026-10-29 from the work so far. Entries are PROPOSED unless stated. "Eng" = engineering can decide.

## A. Decided in the build (engineering)

### DEV-001: a separate random `public_id` next to the UUIDv7 `id`
- **Kind:** FIX | **Status:** PROPOSED | **Owner:** lead to confirm
- **Docs:** GHE-10 §4 allows UUIDv7 for internal ids. GHE-13 §03 says public ids must never encode a timestamp, national id, MRN or name.
- **Problem:** UUIDv7 has the creation time in its first 48 bits, so it cannot be a public id.
- **We do:** `id` (UUIDv7) stays inside the cell; `public_id` (128 random bits) is used in URLs, APIs, events.
- **Why:** satisfies both documents. **Risk:** two identifiers per row. **Reversible:** yes, `foundation/ids.py` only.
- **Affects:** every table; `backend/foundation/ids.py`

### DEV-002: human-readable `ref_no` serial numbers
- **Kind:** ADD | **Status:** PROPOSED | **Owner:** lead (format, year or no year)
- **Docs:** silent; the docs only forbid public ids that encode time or personal data.
- **We do:** per-cell, per-entity DB sequence formatted like `APT-C01-000042`, for staff, support and invoices. Never used in URLs, never an access key, no year by default.
- **Why:** people need a short number to quote. **Risk:** enumerable, so every read still passes the policy check.

### DEV-003: package named `foundation`, not `platform`
- **Kind:** FIX | **Status:** ACCEPTED (Eng) | The architecture sketch said `platform`; a top-level `platform` package shadows Python's standard library module. Affects `backend/foundation/`.

### DEV-004: no `django.contrib.auth`, `admin` or `sessions`
- **Kind:** ADD | **Status:** PROPOSED
- **Why:** actors, roles and delegation are our own models and login is OIDC token validation. Django's built-in User model is hard to replace later. **Risk:** we must build our own permission plumbing and any admin screens.

### DEV-005: one polymorphic `evidence_record` table
- **Kind:** MERGE | **Status:** PROPOSED
- **Docs:** the same idea is called EvidenceRecord (GHE-20), EvidenceArtifact (GHE-21), evidence (GHE-19), evidence_ref (GHE-22/23).
- **We do:** one table typed by `evidence_class`. **Risk:** a wide table; add per-class views if it grows awkward.

### DEV-006: one `approval_decision` table and one `hold` table
- **Kind:** MERGE | **Status:** PROPOSED
- **Docs:** GateDecision, AssessmentDecision, approval_event, pack_approval, sign-offs; and Hold, feature suspension, connector/market suspension.
- **We do:** one table each, typed by subject. Same risk and remedy as DEV-005.

### DEV-007: GHE-12 workflow states are authoritative; GHE-01 words are journey labels
- **Kind:** CONFLICT | **Status:** PROPOSED | **Owner:** workflow/clinical-safety owner
- **Docs:** GHE-01 lists REQUESTED, VERIFIED, ROUTED, PARTNER_ACCEPTED...; R3.0 and GHE-12 list REQUESTED, VALIDATED, ACCEPTED, IN_PROGRESS, OUTPUT_AVAILABLE, CLINICAL_REVIEWED, HANDOFF_ACKNOWLEDGED, CLOSED/CANCELLED/BLOCKED.
- **We do:** store the GHE-12 set; map GHE-01 words per journey in the workflow definition.

### DEV-008: column types, field lengths, indexes are ours
- **Kind:** GAP | **Status:** PROPOSED. The docs give no types. All types in docs/schema are INFERRED.

### DEV-009: role is a catalog table, not a hardcoded list
- **Kind:** CONFLICT | **Status:** PROPOSED
- **Docs:** GHE-01 names 12 actor categories; GHE-15 names 12 roles R01..R12; the lists are not identical.
- **We do:** `trust.role` rows, so either list can be configured without code changes.

### DEV-010: working technology defaults (Django, Postgres, own workflow engine, Postgres outbox, OIDC stub)
- **Kind:** ESCALATE (ADR-08 is OPEN in the docs) | **Status:** OPEN
- **We do:** use these to learn and build, each behind an interface. **Owner to confirm:** CTO / Architecture Council.
- **Risk:** rework if another framework or workflow engine is chosen. Keep module boundaries strict to limit it.

### DEV-017: strict handling of impossible and repeated local times
- **Kind:** GAP | **Status:** PROPOSED | **Owner:** workflow / clinical-safety owner to confirm
- **Docs:** GHE-12 §11 says store UTC plus source time, and test DST changes and clock skew. It does not say what to do with a wall-clock time that does not exist or happens twice.
- **We do:** reject a time inside a spring-forward gap; reject a repeated fall-back time unless the caller explicitly picks EARLIER or LATER; reject any datetime without a zone; keep date-only values as dates; require an offset or a default zone for timestamps. Implemented in `backend/foundation/timeutil.py`.
- **Why:** guessing a time on an appointment or a result deadline is a safety risk; an error is better than a silent shift.
- **Risk:** partner data with sloppy times will be rejected or quarantined instead of accepted. A clinical lead may want a different rule for specific journeys.
- **Reversible?** yes, one module.

### DEV-018: PostgreSQL row-level security as a second fence, with two database roles
- **Kind:** ADD | **Status:** PROPOSED | **Owner:** lead / DBA to confirm the role split
- **Docs:** GHE-11 CT-04 requires cell_id and tenant_id on every protected row. GHE-10 notes that row isolation alone is NOT enough for multi-role clinical records.
- **We do:** every tenant table gets a row-level-security policy (`foundation/rls.py`, `EnableRLS`) keyed on per-transaction settings; field and record rules still live in the policy module. The app must connect as a restricted role that is neither superuser nor table owner; migrations run as a separate owner role.
- **Why:** if a developer forgets a filter, the database still hides other tenants. **Risk:** if the app is ever deployed with a superuser or owner login, RLS silently does nothing. A test documents this. Deployment setup must create both roles.
- **Not yet done:** the real role setup in Docker/infra; today only the tests create the restricted role.

### DEV-019: outbox has no row-level security; idempotency failures are not cached; payload guard
- **Kind:** ADD | **Status:** PROPOSED | **Owner:** lead / security to confirm
- **Docs:** GHE-11 §7 requires a transactional outbox, inbox dedupe and idempotency; GHE-13 requires idempotency keys of 16-128 characters; payloads must hold references, not PHI.
- **We do:**
  - The OUTBOX table is not protected by row-level security, because the relay worker must deliver events for every tenant in the cell. Mitigation: payloads are limited to flat, short, reference-only values (`foundation/payloads.py`), and only foundation code reads the table. INBOX and IDEMPOTENCY tables do have row-level security.
  - A failed request is NOT remembered, so the same key can be retried. Only a completed result is saved. Two identical requests at once make the second wait, then replay the first's result.
  - Retention for saved results has no default; each operation must choose.
  - Delivery order is per `ordering_key` (a database sequence). It is not a global order. A quarantined event keeps blocking later events with the same key until a person resolves it.
- **Risk:** the payload guard cannot recognize personal data by meaning, only by shape. A future developer could still put an identifier in a short string. Review event payloads in code review. Nothing yet deletes expired idempotency rows or published outbox rows.
- **Not yet done:** a purge job and the production grants for the restricted app role on these tables and the sequence. (The worker command now exists: see DEV-034.)

### DEV-020: audit log is a per-tenant hash chain, append-only in the database
- **Kind:** ADD | **Status:** PROPOSED | **Owner:** security / privacy to confirm
- **Docs:** the docs require an audit trail of who did what (GHE-11, GHE-20 evidence) but do not say how tampering is detected.
- **We do:** `modules/evidence` stores audit events numbered 1, 2, 3... per tenant. Each row holds the hash of the row before it, so changing or removing an old row breaks everything after it. `verify_chain()` re-checks it. The database also refuses UPDATE and DELETE (trigger, even for the table owner), and the app role is granted only SELECT and INSERT. Writers for one tenant take it in turns (advisory lock).
- **Risk:** someone with full database control can disable the trigger and rewrite the WHOLE chain from some point onward, hashes included. Real protection needs the latest hash copied somewhere they cannot reach (external anchor / WORM storage). TRUNCATE is not trapped. One tenant's audit writes are serialized, so very busy tenants may need batching later.
- **Not yet done:** the external anchor, a scheduled verify job, the disclosure log (who saw which patient data), and partitioning for size.

### DEV-021: tenant table is not row-level secured; roles are seeded per tenant; dev login stub
- **Kind:** ADD | **Status:** PROPOSED | **Owner:** lead / security to confirm
- **Docs:** `trust.tenant`, `organization`, `actor`, `role`, `role_assignment` (docs/schema 02 B); GHE-15 R01..R12; GHE-06 says a role alone is never authorization.
- **We do:**
  - `Tenant` has no row-level security, because login must find the tenant before any tenant is bound. It holds no personal data and only trust code reads it. Everything else in trust has row-level security.
  - The twelve roles are copied into every new tenant as rows (`roles_seed.py`), so a tenant can adjust its own catalog. A later change to the seed does not reach existing tenants by itself.
  - Role assignments are ended, never deleted, and hold start and end times in UTC. An assignment grants nothing alone: `active_roles()` only reports what is in force, and the policy module (next) decides requests.
  - A development login (`Bearer dev:<tenant>:<issuer>:<subject>`, unsigned) exists for building. It works only if BOTH `DEBUG` and `DEV_AUTH_STUB` are on, and every failure gives the same UNAUTHENTICATED answer.
- **Risk:** the unsigned dev login must never be reachable in production; production settings must keep both flags off (base default is off). Seeded roles can drift between tenants.
- **Not yet done:** persons (patients) and links between actors and persons, facilities, qualifications, delegation, sessions, the real OIDC validation, and the HTTP endpoints that use `authenticate`.

### DEV-022: access decisions are default-deny, rules are data, relationships fail closed
- **Kind:** ADD + GAP | **Status:** PROPOSED | **Owner:** privacy / clinical safety / security to confirm the rule table
- **Docs:** docs/schema 07 (role x data matrix, "a role alone is never sufficient"), GHE-06 C9 (access decision inputs), GHE-10 W13 (employer denial tests). The docs give no exact allow-list per role and resource, no verbs, and no purpose-per-role table beyond examples.
- **We do:** `modules/policy` answers "may this actor do this operation for this purpose" (`decide()`). Rules are a table in `rules.py`, not code. Anything unlisted is denied. Order: hard prohibitions, then role rule and verb, then purpose, then conditions (ORG: role held at that organization; OWN/REL/PROXY: a verified relationship). Roles come from the trust module only. Every decision is stored (append-only, enforced by a database trigger) and audited, allowed or denied.
- **Important limit:** the patient and relationship tables do not exist yet, so OWN, REL and PROXY cannot be verified and ALWAYS DENY today. That means no clinician, patient or guardian can read patient data yet. Only organization-scoped operations and public catalog reads can be allowed. This is deliberate until persons and care relationships are built; a `RelationshipChecker` seam is ready and tested with a stub.
- **Our invention:** the verb set (read/write/attest), the resource classes, the purpose list, and which role may use which purpose. They come from reading the matrix, not from the documents directly. The "minimal"/"status only" fields in the matrix are NOT yet modelled (a role either has a class or not); field-level limits come later.
- **Risk:** the rule table is the product's real security policy and nobody authorized has reviewed it. R11 support staff are hard-blocked from patient data until the just-in-time privileged grant exists.
- **Not yet done:** field-level filtering, purpose grants and consent, break-glass, jurisdiction packs, HTTP wiring of `decide`, and caching (none, on purpose).

### DEV-023: a dev-only console to operate the backend by hand
- **Kind:** ADD | **Status:** PROPOSED | **Owner:** lead to confirm it stays dev-only
- **We do:** `backend/devconsole/` serves one page at `/` and a small JSON API under `/dev/api/`, calling the same public module APIs as real code (create tenant, organization, person, role; run an access check; see decisions, the audit log and chain status). Every view returns 404 unless `DEBUG` and `DEV_AUTH_STUB` are both on. There is no login.
- **Risk:** it can create anything in any tenant with no authentication. It must never be reachable in production; production settings keep both flags off (tested). It is not the product frontend and will be deleted when the real one exists. It reads other modules only through their `api.py`, but it sits outside `modules/` so the import rule does not cover it.
- **Not yet done:** nothing it shows is a real clinical screen; the real frontend is a later step.

### DEV-024: patients carry no demographics yet; links are the only way to reach a patient
- **Kind:** ADD + GAP | **Status:** PROPOSED | **Owner:** privacy / DPO / clinical safety to confirm
- **Docs:** `trust.person_subject` (docs/schema 02 B) lists legal name, birth date, sex or gender "encrypted, D2"; GHE-06 separates login, identity proof, record match, delegation and purpose.
- **We do:** `Person` is only an id, a language and a link status. **No name, birth date or contact details are stored**, because the docs require field-level encryption and key handling that is not built. Three link types feed the access decision: OWN (the patient's login), REL (a care relationship for a clinician or home-care role), PROXY (a guardian grant). A link counts only while active, inside its dates, not contested, and with both people active. A proxy needs approval by a different person than the delegate. Ended links keep their history.
- **Risk:** the care-relationship rule is ours: any R03 or R09 role holder can be given a link, and nothing yet checks that the clinician is licensed, at the right facility, or that the patient agreed. A proxy approver can be any active person; the real rule (legal instrument, proof of authority, who may approve) is not defined in the docs. Matching a person to a source record (match cases), identity proofing and merges are not built.
- **Update (DEV-048):** name, birth date and phone are now stored encrypted.
- **Not yet done:** other demographics, identity proofing, record matching, professional qualifications, facility privileges, consent and purpose grants, and the HTTP endpoints that return a person's data (today a check only says allowed or denied; nothing returns patient data).

### DEV-025: first frontend is a Vite single-page app, built in parallel with the backend
- **Kind:** CONFLICT (with our own plan) | **Status:** PROPOSED | **Owner:** lead / CTO (ties to DEV-010, stack not approved)
- **Docs / plan:** `frontend/README.md` said React with Next.js, one app per audience. The product owner supplied a dashboard mockup (navy sidebar, stat cards, charts, tables) to build in parallel with the backend.
- **We do:** `frontend/operations/` is React 18 + TypeScript + Vite (not Next.js): the first app is a staff dashboard behind a future login, so server rendering is not needed yet. Every screen shows only real backend data; menu items with nothing behind them show "not built yet" and the step they wait for. The mockup's sample numbers and names are NOT reproduced. Until real login exists it calls the dev-only API (DEV-023); only `src/api.ts` knows that.
- **Risk:** if the lead picks Next.js, the pages move over but the build tooling changes. The dev API must not be shipped. The patient and clinician apps do not exist yet. Accessibility (WCAG 2.2 AA) has had only a first pass (labels, contrast tokens, keyboard-reachable controls), no audit.
- **Not yet done:** login, per-role navigation, real search, forms for creating things (the old dev console still does that), charts beyond the access-outcome donut, tests for the frontend, a Docker service for it.

### DEV-026: service catalog, slots and bookings, and the first real HTTP API (/api/v1)
- **Kind:** ADD + GAP | **Status:** PROPOSED | **Owner:** workflow / clinical-safety owner (booking states), privacy (what a booking list may show)
- **Docs:** GHE-01 J1 (book a care appointment), GHE-12 (workflow states, partner confirmation, UTC plus source time). The docs do not define the booking states for a first-party clinic, slot generation, or cancellation rules.
- **We do:**
  - `catalog` holds locations (with an IANA time zone) and services. `scheduling` holds slots and bookings. Other modules are referenced by public id, never by a database link.
  - Slots are created from the clinic's wall-clock time and refused if the time does not exist or repeats (DEV-017). Stored in UTC with the clinic zone kept.
  - A booking is `requested`, then `confirmed` by organization staff (role R04 only), or `cancelled`. One active booking per slot is enforced by a database unique index (and a row lock). Cancelling frees the slot.
  - Booking, confirming and cancelling need a **policy receipt**: an allowed decision for exactly that actor, operation, organization and patient, no older than 2 minutes (`policy.authorize` then `check_receipt`). Denials are stored in their own committed transaction so a refused request still leaves a trace.
  - Every change is audited and emits an outbox event (`booking.requested.v1`, `.confirmed.v1`, `.cancelled.v1`) with references only.
  - `/api/v1/` endpoints (`me`, `services`, `services/<id>/slots`, `bookings`, confirm, cancel) use the dev login, bind the tenant, and require an `Idempotency-Key` header on booking.
  - The dev login now allows `@` and `+` in login ids.
- **Risk:** "confirmed" here means the clinic accepted it. The docs' partner-confirmation and clinical-acceptance steps (B8 workflow) are not modelled. The "purpose" of a login is guessed from its type (`api/support.py`), which the real login must replace. The booking list is built per request from several policy checks, which writes many decision rows. Booking list and `me` leak existence of an id by returning 404 vs 403. No reminders, no rescheduling, no waitlist, no capacity above one per slot, no provider (clinician) on a slot.
- **Not yet done:** the staff screens (the operations app still has no Services or Appointments pages), real OIDC, rescheduling, notifications (comms), and the public marketing site.

### DEV-027: staff manage the catalog and confirm bookings through the real API; catalog writes are enforced at the API layer
- **Kind:** ADD + GAP | **Status:** PROPOSED | **Owner:** security (where enforcement lives), clinic-operations lead (who may edit the catalog)
- **We do:** `/api/v1` gains `POST /locations`, `POST /services`, `POST /services/<id>/status`, `POST /services/<id>/slots`, `GET /locations`, and `GET /services?all=1` (includes hidden services). Each asks the policy engine for `write:catalog` at the organization first; only clinic operations (R04) of THAT organization pass. Patients, employers and staff of another organization are refused (tested). The operations app gets live Services and Appointments screens, and an "Acting as" box choosing which staff login its staff calls run as.
- **Gap (CLOSED by DEV-028):** catalog writes were first protected only at the HTTP layer; see DEV-028.
- **Risk:** the "Acting as" box lets whoever opens the operations app act as any staff login (dev stub, no password). A hidden service keeps its existing bookings. Hiding or editing a service does not notify patients. Slots cannot be removed or edited once created, only booked or left open.
- **Not yet done:** withdraw or edit slots, rename or edit a service, receipts on catalog writes, per-role navigation, notifications to patients when a booking is confirmed.

### DEV-028: catalog writes demand a policy receipt in the domain (closes the DEV-027 gap)
- **Kind:** FIX | **Status:** PROPOSED | **Owner:** security to confirm
- **We do:** `catalog.create_location`, `create_service`, `set_service_status` and `scheduling.create_slots` now take `actor_public_id` and a policy `decision` and check it first (`write:catalog` for exactly that organization, recent, allowed, for that actor). They no longer accept `by="system"`. The HTTP endpoints pass the decision from `authorize`. The dev console now runs these actions as a named staff login (`actor` in the request) and authorizes first, so it can no longer write the catalog unchecked. Refused cases are tested: a stored denial, a made-up decision, a receipt for another organization, another operation, or another actor all give POLICY_DENIED and write nothing.
- **Risk:** bootstrap or import jobs have no "system" shortcut: a job must act as a real login holding the right role (intended). A receipt is reusable for 2 minutes, not single-use. Other module writes (trust, people, audit-adjacent) still rely on callers; only scheduling and catalog demand receipts so far.
- **Not yet done:** receipts for trust writes (organizations, actors, roles, care and proxy links), and single-use receipts.

### DEV-029: patient home follows the second design; unbuilt sections say so
- **Kind:** ADD | **Status:** PROPOSED
- **We do:** the patient app's menu has the design's 14 items in its order, and the home screen has the design's sections. Real data: upcoming appointments, bookable services ("Recommended for You" is simply the live catalog, not a recommendation engine), and recent activity (from booking records; `created_at` is now returned by the bookings API). NOT drawn because nothing real exists: doctor profiles and ratings, vitals, referrals, bills, messages, a "Join video visit" button, "Reschedule", and the "Need help?" support card. Each unbuilt section is a visible "not built yet" card.
- **Risk:** the design implies providers (named clinicians with reviews), rescheduling, and a care team; none exist in the backend yet.

### DEV-030: password login with signed, revocable tokens and role-based landing (a stand-in identity provider)
- **Kind:** ADD + CONFLICT (with the docs' "secrets live in the IAM product") | **Status:** PROPOSED | **Owner:** CTO / security (ties to DEV-010, IAM vendor not chosen)
- **Docs:** GHE-06 says authentication is OIDC and "secrets live in the IAM product, not here"; ADR-08 (IAM vendor) is open. The docs do not define lockout, token lifetime or session revocation.
- **We do:** a development-grade identity provider inside `modules/trust` behind one function, `authenticate()`, so a real OIDC verifier can replace it without touching callers.
  - `POST /api/v1/auth/login` takes organization name, login and password. Passwords are salted scrypt hashes. Every failure (unknown organization, unknown login, wrong password, locked, suspended) is the same 401, and unknown logins cost the same hashing time.
  - Five wrong passwords lock a login for 15 minutes (committed even though the request fails).
  - The result is an HMAC-signed token valid 8 hours, signed with `SECRET_KEY`. Every request re-checks the database (tenant active, actor active, session epoch). Sign-out and password change raise the epoch, which kills every token already issued.
  - The response says which area a person belongs in (`patient`, `operations`, `clinician`, `payer`, `employer`, `partner`, `none`), decided from actor type and roles (`home_for`). Each app only admits its own area and points others to the right app.
  - Logins and refusals for known actors are audited; passwords and tokens are never logged.
  - The unsigned dev token (`Bearer dev:...`) still exists but only works when DEBUG and DEV_AUTH_STUB are both on.
- **Risk:**
  - No rate limit per IP or per organization: an attacker can lock any known login (a lockout is also a denial of service) and can try many logins quickly. Failures for UNKNOWN logins are deliberately not audited (they would let an attacker flood the audit chain).
  - Tokens live in `sessionStorage`, readable by any script on the page, so a cross-site-scripting bug would expose them. No CSRF issue because tokens are sent in a header, not a cookie.
  - `SECRET_KEY` signs the tokens: leaking or rotating it signs everyone out or lets anyone forge tokens. Production must set a long random key.
  - No password reset, no invitations, no email verification, no multi-factor step (the docs require step-up for sensitive actions), no password history. Only length (10-128), not-the-login and not-one-repeated-character are checked.
  - The organization is typed by name at login (names are unique per cell). Real login should resolve the organization from an invitation or email domain.
  - `set_password` has no policy receipt: only the dev console calls it.
  - (Closed by DEV-032) The staff console's dashboard, patients, organizations and compliance screens used to read the unauthenticated dev data feed.
- **Not yet done:** authenticated staff data endpoints (then delete the dev feed), per-IP rate limiting, password reset and invitations, MFA and step-up, token refresh, clinician/payer/employer/partner apps.

### DEV-031: patient self-registration (off by default per organization); Google sign-in deferred; who is an admin
- **Kind:** ADD + ESCALATE | **Status:** PROPOSED | **Owner:** product + security + privacy (who may self-register; Google needs the business to create an OAuth client)
- **Docs:** GHE-06 separates login, identity proof and record matching, so a new account proves none of them. The docs do not say who may self-register or how.
- **We do:**
  - `POST /api/v1/auth/register` lets a patient create their own account in an organization, but ONLY if that organization has switched `self_registration` on (default off; dev console action `self-registration`). It creates a patient actor, a patient record linked to it, the patient role (R01) at the organization's first active site, and a password login, all in one transaction, and signs them in. Staff are never self-registered; an administrator creates them.
  - It never takes over an existing account (a taken login or an existing actor gives 409 and changes nothing). A failed registration leaves nothing behind. At most 20 new accounts per organization per 10 minutes (RATE_LIMITED after).
  - The patient record starts `unverified`. The email is NOT verified (no email sending exists), so the login proves nothing about who owns the address.
  - New sign-in and create-account pages in both apps (staff app has no Create account). A "Continue with Google" button is shown DISABLED with the reason. `GET /api/v1/auth/providers` reports whether a `GOOGLE_CLIENT_ID` is configured.
- **Google sign-in is NOT built.** It needs a Google OAuth client id that the business must create, and the server code that verifies Google's token cannot be tested without it. One rule must hold when it is built: never link a Google identity to an existing account just because the email matches unless that account's email was verified, otherwise anyone could pre-register a victim's email and take the account over when the victim signs in with Google.
- **Risk:** registration reveals whether an email is already taken (409). Unverified emails mean spam accounts are possible even with the cap. The new patient's role is held at "the first active site", which is arbitrary for multi-site organizations. There is no password reset, so a forgotten password cannot be recovered.
- **Roles in plain words (the admin question):** the CUSTOMER is the organization (a hospital group, payer, employer). Its ADMIN is its own clinic-operations staff (R04), who manage that organization only. Patients are the customer's users. Zoiko staff have narrow roles (R11 support, R12 safety officer, R10 integration) with no patient data. There is deliberately NO super admin who can open any patient record (docs: "no global admin to PHI"). Checking the platform's health would need a separate platform console with no patient data, which is not built.

### DEV-032: every staff-console screen uses authenticated, policy-checked endpoints; the dev data feed is gone from the app
- **Kind:** ADD + FIX | **Status:** PROPOSED | **Owner:** privacy / security (what staff may see), clinic-operations lead
- **We do:** `GET /api/v1/staff/dashboard | directory | patients | compliance`. Each one (1) requires an operations-area account (R04, R10, R11 or R12) whatever the policy table says, then (2) asks the policy engine: `read:analytics` (dashboard), `read:directory` (directory), `read:person` (patients), `read:evidence` (compliance). The dashboard includes the audit and decision panels only if `read:evidence` is also allowed. The staff console no longer calls `/dev/api` at all.
- **Rule table changes (ours, to be reviewed):** a new resource class `directory` (read, ORG) for R04, R10, R11, R12; and `analytics` (read, ORG) for R11, which matches the role matrix ("Ops: analytics T0/T1").
- **Privacy applied in the data:** staff views never show a patient's or guardian's login. Staff appear by login; patients and guardians appear as "Patient ab12cd" / "Guardian ab12cd". The directory lists organizations and staff logins only. R11 support staff are refused the patients list (hard prohibition) but may see the directory, the dashboard counts and compliance.
- **Risk / limits:**
  - "ORG" conditions are checked against the FIRST organization where the account holds a role; multi-site staff are not handled.
  - Patient records are tenant-wide, not per organization, so any R04 at any site of a tenant sees all of its patients (the patients list also shows which clinicians treat each one).
  - The dashboard shows exact counts; there is no small-number suppression.
  - Compliance shows the most recent 50 decisions and 50 audit entries only; no paging, search or export.
  - The old dev console at port 8000 (DEV-023) still exists, is unauthenticated, and must stay dev-only.
- **Not yet done:** paging and search, per-organization patient scoping, small-number suppression, a platform console for Zoiko staff across tenants (no patient data), and removing the dev console when real tooling exists.

### DEV-033: a platform console for Zoiko's own staff, with aggregate numbers only
- **Kind:** ADD | **Status:** PROPOSED | **Owner:** CTO / security / privacy (who at Zoiko may see what across customers)
- **Docs:** the docs separate a global governance plane (no patient data) from regional cells, forbid a "global admin to PHI", and give R11 (regional operations and support) and R12 (clinical safety officer) narrow, metadata-only roles.
- **We do:** `GET /api/v1/platform/overview` and a third app, `frontend/platform/` (port 5175). One row per customer tenant in the cell with AGGREGATE numbers only (patients, staff, sites, services, bookings by status, free slots, access checks and denials in 24h, failed sign-ins in 24h, audit-chain check, last activity), plus the cell-wide event-delivery backlog (waiting, stuck, oldest age) and system health. A "needs attention" list flags a broken audit chain, a suspended customer, 5+ failed sign-ins in a day, stuck events, and events waiting over 5 minutes. No patient rows, names, emails, ids or booking ids appear (tested).
- **Three independent gates, all required:** the account belongs to a tenant of TYPE `platform` (customers cannot create one), it holds R11 or R12, and the policy engine allows `read:platform` (new resource class; R11 and R12 only). A customer's own R11 would pass the policy table alone, so the tenant-type gate is what stops it (tested).
- **Landing:** `home_for` now returns `platform` for R11/R12 in a platform tenant; the same roles in a customer's tenant land in that customer's staff console. Each app points wrong-area accounts to the right app.
- **Audit:** every view is recorded in the PLATFORM tenant's own audit chain ("platform.overview"). Nothing is written into customers' chains, and the page does not poll (a manual Refresh), so the log is not flooded. Customers therefore cannot see that Zoiko looked at their aggregates.
- **Also changed (a real bug found by the tests):** the "purpose" a person acts under was guessed from the kind of login, which refused a safety officer who is a support-type login. It is now derived from the roles they hold (R12 acts for SAFETY_REVIEW, R11 for SUPPORT, and so on); the login type is only a fallback. Role codes now travel on the authenticated result.
- **Risk / limits:**
  - Read-only. There is no suspend, onboard or configure action, on purpose: suspending a customer should need two people's approval, which is not built.
  - Loading it opens every customer's data context one after another and verifies each full audit chain, which costs more as customers grow. It needs caching or a background job before it is used with many customers.
  - It sees every customer in the CELL; there is no cross-cell view yet.
  - Aggregate counts can still reveal facts about small customers (for example exactly 1 patient). No small-number suppression.
  - Customers are not told that Zoiko viewed their aggregates.
  - Zoiko accounts are created through the dev console; there is no onboarding flow.
- **Real finding it already surfaced:** events waiting 79 minutes. Nothing runs the outbox delivery worker (DEV-019 "Not yet done"), so events pile up; this is expected until the relay worker command is built.

### DEV-034: the event-delivery worker and an in-process subscriber registry
- **Kind:** ADD | **Status:** PROPOSED | **Owner:** CTO / architecture (ties to DEV-010: the message broker is not chosen)
- **Docs:** GHE-11 requires a transactional outbox with at-least-once delivery and consumer dedupe; ADR-08 (message broker) is open.
- **We do:**
  - `python manage.py relay_outbox` runs `relay_once` in a loop (`--once` for a single pass, `--interval`, `--batch`), stops cleanly on Ctrl+C or SIGTERM, and survives a database outage by waiting 5 seconds and retrying. A full batch triggers an immediate next pass. It prints counts only, never a payload or an error message. Several copies may run at once.
  - `foundation/dispatch.py` is a small in-process registry: a module calls `subscribe(consumer_name, event_prefix, handler)` at start-up. For each due event, `dispatch` binds the event's OWN tenant (never anything from the payload) and runs each matching handler inside the inbox's once-per-consumer guard, in a savepoint. A failing handler leaves nothing behind and the event is retried with backoff; a subscriber that already succeeded is not run again; a duplicate delivery does nothing. An event nobody subscribes to is simply marked delivered.
  - Docker Compose has a `worker` service (same image, restarts unless stopped), so the stack delivers events with no manual step. The platform console's backlog warning cleared.
- **Risk / limits:**
  - There are NO subscribers yet, so today the worker only drains the queue; nothing reacts to bookings. Notifications (comms) will be the first.
  - It is in-process, not a broker: handlers run inside the relay's database transaction, so a slow handler holds the claim on its row and a handler must never call a slow outside service directly (it should queue a job instead). Subscribers must be registered in the process that runs the worker.
  - Delivery is at least once and per ordering key only. A stuck (quarantined) event still blocks later events with the same key until a person resolves it; there is no screen or command to inspect, retry or discard a quarantined event.
  - Nothing reports that the worker itself is alive: the platform console only sees lag (oldest waiting event), so a dead worker shows up as growing lag, not as "worker down".
  - No purge of old published events yet; the table grows.
- **Not yet done:** a worker heartbeat, a quarantine inspect/retry tool, a purge job, the first subscriber (patient notifications), and a real broker.

### DEV-035: patient notifications, the first subscriber of the event worker
- **Kind:** ADD + GAP | **Status:** PROPOSED | **Owner:** privacy (what a notification may say), clinical safety (which events notify), product (channels)
- **Docs:** GHE-11 / GHE-13 list messaging and notification channels; they do not define which events notify a patient or what the text may contain.
- **We do:** the comms module's notifier subscribes to `booking.*` events. When a booking is requested, confirmed or cancelled, the worker creates an in-app notification for the PATIENT RECORD (not a login), so the patient's own login and a guardian with an approved proxy both see it. The event carries only references; the text is written in the patient's tenant by reading the booking, service and organization there, so no personal detail travels in an event. `GET /api/v1/notifications` lists notifications for every patient the login may read (policy `read:comms`, per patient); `POST /api/v1/notifications/<id>/read` marks one read (policy receipt for `write:comms` for that patient, checked inside the domain). Staff reach no patient and see none. The patient app has a bell with an unread count, a live Notifications page, and a home card. One notification per event is enforced by the inbox and a database unique constraint.
- **Risk / limits:**
  - In-app only. No email, SMS or push, so a patient who does not open the app learns nothing. Email needs sending, verification and consent that do not exist yet.
  - The text names the service, the organization and the time. That is health information; it is stored in the cell database (row-level security) and shown only to people the policy allows, but it is not encrypted at field level (DEV-024).
  - Only patients are notified. The clinic is not told when a patient books or cancels (staff see it on the Appointments screen only).
  - Every list call records a policy decision and an audit entry per reachable patient, so the app refreshes every 60 seconds (not 15) to limit noise. Reading your OWN data is still logged; an "own reads are not logged" rule would need a policy decision.
  - Changing a notification's text later does not update ones already sent. No deletion or retention rule yet.
  - The worker does not reload code: after a change to a subscriber it must be restarted (`docker compose restart worker`).
- **Not yet done:** email and push channels, clinic-side notifications, preferences and consent, notification retention, paging beyond the latest 50.

### DEV-036: email (a queued, worker-sent mail system), address verification and password reset
- **Kind:** ADD | **Status:** PROPOSED | **Owner:** security / privacy (reset rules, link lifetimes), CTO (mail provider, DEV-016)
- **Docs:** the docs require identity proofing separate from login (GHE-06) but do not say how an email address is verified or how a lost password is recovered.
- **We do:**
  - **Mail queue** (`foundation/mail.py`): an email is queued in the same transaction as the change that needs it, and the worker (`relay_outbox`) sends it over SMTP outside any transaction, retrying with backoff (5 tries, then marked failed). Addresses and subjects containing line breaks are refused. After sending, the body (which holds a one-time link) is erased and the address is masked. Only the failure's exception class is kept. A mail server outage never blocks sign-up.
  - **Verification:** registration emails a link (works once, expires in 48 hours). A newer link cancels the older one. Resending is limited to once a minute and 5 a day. `GET /api/v1/me` reports the masked address and whether it is verified; the patient app shows a banner with a Resend button until it is.
  - **Password reset:** "Forgot password" always gives the same answer whether or not the account exists. A reset email is sent only to a VERIFIED address, at most 3 an hour per person. The link works once and expires in 1 hour. A weak new password is refused without using up the link. A successful reset signs the person out everywhere and clears any lockout.
  - **Tokens:** `<tenant id>.<secret>`; only a hash of the secret is stored; never in logs, audit entries or the database after sending. Every token lookup filters by tenant itself (a test found it relying on row-level security alone).
  - **Dev inbox:** Docker Compose runs Mailpit (http://127.0.0.1:8025): every email is caught there and nothing is delivered to a real address.
  - The platform console shows emails waiting and emails that gave up.
- **Risk / limits:**
  - **No real mail provider.** Production needs one configured (SMTP host, sender domain, SPF/DKIM). Until then, emails only reach the dev inbox.
  - The platform-console and staff apps have no "forgot password" screen; only the patient app does, and staff accounts have no email address yet.
  - An unverified patient can still use the app and book; verification does not yet gate anything. Self-registered accounts with an unverified (possibly someone else's) address are still allowed.
  - A person who registered with a wrong address and never verified it cannot reset their password (by design) and cannot re-register (the login is taken). They need an administrator.
  - Reset-request timing can differ slightly between an existing and a non-existing account (a database write happens only for real accounts); the answer itself is identical.
  - The email text is plain, English only, no unsubscribe or preference handling. Links point at `PATIENT_APP_URL`, which must be set per environment.
  - Mail is at least once: a crash after sending but before recording sends it again.
- **Not yet done:** a production mail provider, staff and platform "forgot password", gating booking on a verified address, HTML and translated templates, bounce handling, Google sign-in (now unblocked: it still needs a Google client id).

### DEV-037: an Organization Administrator role (R13) and a Team screen with email invitations
- **Kind:** ADD + GAP | **Status:** PROPOSED | **Owner:** product, security, privacy (what an organization's administrator may do)
- **Docs:** the role list R01..R12 has no "administrator of a customer"; R04 (clinic operations) is the closest. The docs forbid a platform-wide admin to PHI but are silent on an admin of ONE customer.
- **We do:** a new role `R13 Organization administrator`, held by the customer's own manager. It has every right R04 has (copied) plus `write:staff`. It lands in the staff console like R04 and can confirm bookings. An administrator can invite people as a doctor (R03), a receptionist (R04) or another administrator (R13), switch a person off or back on, and resend an invitation. The roles that can be handed out are an allow-list in code: Zoiko's own roles (R10-R12), patient, guardian, payer, employer and partner roles can never be granted this way (tested).
  - **Invitation, not passwords:** the new person is created with a random password nobody knows and gets an email with a one-time link (valid 7 days) to choose their own. The email names the ORGANIZATION to type at sign-in (a bug where it named the site instead was found by reading the real email and fixed). Using the link also verifies the address (it only ever went there). The Team screen marks "invited, not accepted".
  - **Fences:** every change needs a policy receipt for `write:staff` at the administrator's organization, checked inside the domain as well as at the endpoint. An administrator cannot switch themselves off. Patients, guardians and people with no role at the administrator's organization are "not found". Existing logins are never taken over (409).
  - The role is added to old tenants the first time it is assigned.
- **Risk / limits:**
  - Switching someone off does not delete anything and their access to already-open pages ends at their next request. There is no "remove" and no history screen.
  - An administrator can create other administrators: nothing limits how many, and there is no "owner" or "last administrator" protection (only the self-switch-off rule).
  - Administrators act on their FIRST organization only; multi-site administration is not handled.
  - No approval step: one administrator alone can add a doctor. Clinical staff are not checked against any licence register (the docs require qualification checks, not built).
  - A doctor can be invited and sign in but has no app yet (no clinician console).
  - Invitation resend is limited to once a minute and 5 a day per person.
  - Customers (organizations) themselves are still created only through the developer console; there is no onboarding screen and no Zoiko-side approval flow.
- **Not yet done:** onboarding a new customer with its first administrator by invitation, change or remove a person's role, administrator audit view, two-person approval for adding administrators, multi-site administration.

### DEV-038 Doctor app: doctors on appointment times (ADD)

- **What:** A time slot can name the doctor it belongs to. A doctor signs in to a new app (`frontend/clinician`, port 5176) and sees "My schedule" and "My patients".
- **Rules we added:**
  - Only an active doctor (R03) of the same organization can be put on a time; a doctor cannot be double-booked at the same moment (database constraint), but two doctors can share a time.
  - When the clinic confirms a booking, the doctor gets a care-team link to that patient, valid until 30 days after the visit. A repeat visit reuses the link. If the doctor is suspended, the booking still confirms and no link is made.
  - A doctor sees only CONFIRMED appointments on their own times, and each patient is checked against the access policy before showing.
  - Doctor endpoints answer 403 to anyone who is not a doctor.
- **Why:** The docs require a patient to be visible only through a real relationship; booking confirmation is the first real event that creates one.
- **Limits:** Patients show as ids (no names stored, DEV-024). Notes, results, referrals and messages are marked "soon". The 30-day figure is our choice, not from the docs.
- **Invitations:** a doctor's invitation (and its resend) links to the doctor app (`CLINICIAN_APP_URL`, default http://localhost:5176); receptionists and administrators still go to the staff console. The doctor app now has the "choose your password" and "verify email" link page.

### DEV-039 Referrals: a doctor sends a patient to a service (ADD)

- **What:** New module `modules/referral`. A doctor (R03) refers a patient they have a care relationship with to a service (of any organization of the same customer). The receiving organization's operations staff (R04, R13) accept, ask for more information, or decline. The patient and the doctor see each step; the patient is notified.
- **Rules we added:**
  - Only a doctor may issue; only the receiving organization's staff may decide. Both need a policy receipt for that exact patient and organization.
  - A reason is required (max 500 characters). "Ask for more" and "decline" also need a reason from the clinic.
  - The same patient cannot have two open referrals to the same service (database constraint). After a decline or accept, a new one is allowed.
  - "Ask for more" keeps the referral open; accepted and declined are final.
  - Events carry only the referral id and state, never the reason text. Each patient notification is worded from the state at the time of the event, so a slow worker still tells every step.
- **Differences from the docs (GHE-03 C):** a simple 4-state set (sent, accepted, needs information, declined) instead of the full 10-state list; the latest decision is stored on the referral row (the audit chain keeps the history) instead of an append-only decision table; destinations are our own services, not the `referral_destination` catalog, so no referrals to other customers or outside partners; no documents attached; self-referral not built.
- **Limits:** the doctor cannot withdraw or edit a referral; "needs information" has no reply channel yet (messages are not built); an accepted referral does not yet create a booking by itself.

### DEV-040: Zoiko support adds a customer by invitation (ADD)

- **What:** In the platform console, Customers page, support staff (R11) fill in a customer name, a first site and the first administrator's email. The system creates the customer, the site and an administrator (R13), and emails the administrator the usual invitation link to choose their own password. Endpoint: `POST /api/v1/platform/customers`. Before this, only the developer console could create a customer.
- **Rules we added:**
  - R11 now has WRITE on the `platform` resource (it had read). R12 (safety) stays read only. The rule is checked three ways: platform tenant, role R11 or R12, and a policy receipt for `write:platform`.
  - Everything is checked BEFORE the customer exists (names 2 to 120 characters, no line breaks, a clean email address), so a bad request never leaves a half-made customer. A name already in use gives 409 and sends no email.
  - The customer starts of type `provider_org`, active, with self sign-up closed. Other types (payer, employer, partner) cannot be created here yet.
  - Zoiko staff never see the administrator's address again: it is not returned and the platform audit entry has no personal detail. The new customer's own audit chain records the setup.
- **Limits:** if the database fails after the customer is created but before the invitation is queued, a customer without an administrator remains (the steps are not one transaction, because creating a tenant commits on its own); support would have to remove it by hand. No second approval step: one support login can add a customer (the docs call for two people on tenant-level actions, not built). No way to suspend, rename or delete a customer, and no re-send of the first invitation from the platform console (the administrator can use "Forgot password"). The first site's time zone and address are not asked.

### DEV-041: clinical notes and results are deferred (ESCALATE)

- **What:** We did not build doctor notes and results, although they were planned next.
- **Why:** The docs describe Zoiko Access as a coordination platform, not an electronic health record, and they require encrypted storage of health data with key handling (DEV-024). That is not built. Free-text clinical notes would make us store health records without those protections, and no one has decided whether Zoiko is a record keeper or only a link to the provider's own system (ADR-06, DEV-012).
- **Needed first:** a decision on Zoiko's role for clinical data, field-level encryption and key handling, retention rules, and the clinical exchange design (docs 03 section D).

### DEV-042: rescheduling an appointment (ADD)

- **What:** A patient (or guardian with the right) can change the time of an upcoming appointment, and clinic staff (R04, R13) can move one too. Endpoint: `POST /api/v1/bookings/<id>/reschedule` with the new slot. The patient app has "Change time" on the Appointments page, and the staff console has "Move".
- **Rules we added:**
  - A move is ONE step: the old booking is cancelled and a new one made together. If the new time was taken a moment before, nothing changes (409).
  - The new time must be a free, future time of the SAME service at the SAME organization. Anything else is refused (422) and the old booking is left alone. A cancelled or past booking cannot be moved (409).
  - A move by the patient needs the clinic to confirm again (the new booking is "requested"). A move by clinic staff of a confirmed appointment stays confirmed, and the doctor is linked to the patient as with any confirmation. A move by staff of a waiting one stays waiting.
  - The new booking records the one it replaced (`rescheduled_from`), so history is kept: the old row stays as cancelled.
  - The patient gets ONE notification, "Appointment moved" (not a cancel plus a new request).
- **Limits:** a move is limited to the same service (no switching to a different doctor service); there is no reason field and no clinic-side notification; no limit on how many times it can be moved; a doctor cannot move or ask to move a booking; the confirmed doctor link from the OLD booking is not removed (it lasts until 30 days after the visit it was made for).

### DEV-043: shared frontend code, and a build fix (FIX)

- **What:** `frontend/shared/` now holds the code that four apps had copied: `ui.tsx` (cards, tables, pills, icons, logo), `AuthShell.tsx` (sign-in frame, password field, Google button) and `auth.css`. Each app reaches it as `@shared/...` and keeps one-line `ui.tsx` and `AuthShell.tsx` files that re-export it, so no page import changed. There is no install step and no package manager change: the shared files use each app's own React (set up in `frontend/shared/vite.shared.ts`, each `vite.config.ts` and `tsconfig.json`).
- **Why:** a fix to a shared piece used to be made four times and the copies could drift (the patient app already had one extra icon).
- **Build fix found on the way:** the repository root holds another team's Next.js app, including a Tailwind `postcss.config.mjs`. Vite searches parent folders for a PostCSS config and loaded it, so all four builds failed with "Cannot find module @tailwindcss/postcss". Each app now declares an empty PostCSS config inline, which stops the search. The same would happen to any new app, so it is in the shared config.
- **Not shared yet:** `useLoad.tsx` (the copies differ in polling default and a reload key) and `styles.css` (small per-app differences). The API client files stay per app on purpose (each app has its own login and storage key).
- **Limit:** the apps still cannot be built from one command and share no tests; there are still no frontend tests.

### DEV-044: attendance at a confirmed appointment (ADD)

- **What:** Journey J1 in the docs is "discover, book, **attend**". Clinic staff (R04, R13) can now record whether the patient attended or did not attend a CONFIRMED appointment whose start time has passed. Endpoint: `POST /api/v1/bookings/<id>/attendance` with `attended` or `no_show`. The staff console shows "Attended" and "Did not come" buttons on past confirmed bookings; the patient app shows "Attended" or "Missed"; the doctor's schedule shows the same.
- **Rules we added:**
  - Only clinic operations staff may record it. The patient and the doctor cannot (the doctor's own attestation of a visit is clinical and belongs to a later phase).
  - Only after the start time, and only for a confirmed booking. Before that, or for a waiting or cancelled booking, the answer is 409.
  - It can be corrected, and saying the same thing twice changes nothing. Every change is audited; the audit chain keeps the earlier answer.
  - The patient is told with one notification per recorded change ("Visit recorded" or "Missed appointment"), worded from what the event recorded, so a slow worker still tells each step.
  - A past booking that has a recorded attendance can no longer be cancelled or moved from the staff console (the buttons are hidden; moving a past booking is refused anyway).
- **Limits:** this is administrative only. Nothing here says anything about the visit itself (no notes, no outcome, no diagnosis; see DEV-041). There is no automatic no-show after a deadline, no late-arrival state, no partner (`ATTENDANCE_SUPPORTED`) feed, and no effect yet on billing or reminders. A no-show is recorded with no consequence for the patient.

### DEV-045: appointment reminders, and a recurring-job hook for the worker (ADD)

- **What:** A patient is reminded once about each CONFIRMED appointment that starts within the next 24 hours ("Appointment reminder" in the patient app's notifications). The lead time is a setting (`REMINDER_LEAD_HOURS`, default 24), not a number in code. The docs list reminders under secure communications (GHE-16) but give no timing.
- **How it runs:** a new, small hook `foundation/periodic.py`. A module registers a job at start-up and the worker calls the due jobs on every pass, after events and email. The scheduling module registers `scheduling.reminders` (checked every 5 minutes) which visits every active customer. The foundation depends on no domain module, so the module registers itself.
- **Rules:**
  - One reminder per booking: the "reminded" mark and the event are written together, and rows another worker holds are skipped, so two workers never send two.
  - Only confirmed bookings that have not started. Waiting, cancelled and past bookings get none. A booking cancelled after the reminder was queued but before delivery produces no message.
  - A moved appointment is a new booking and gets its own reminder.
  - The event carries only the booking id, never the service, clinic or time; the text is written from the booking when delivered.
  - A job that fails never stops the worker or other jobs, and only the error CLASS is logged.
- **Limits:** the reminder is an in-app notification only (no email, SMS or push; the mail queue exists but reminders do not use it yet). One fixed lead time for everybody: no per-patient preference, no "remind me earlier", no second reminder, no time-zone or quiet-hours rule. A booking confirmed less than 24 hours before the visit is reminded right away. The recurring-job hook has no persistence: after a restart every job runs once at once.

### DEV-046: provider directory search and filters (ADD)

- **What:** The docs (GHE-14) describe a provider directory for discovery. Our directory is the service catalog, so "Find Healthcare" now filters it: search text (name, description or category), specialty (the categories that have services, with counts), in person or video, "only with free times", and sort by name or soonest free time. API: `GET /api/v1/services?q=&category=&mode=&available=1&sort=name|soonest`, which now also returns the list of categories.
- **Rules:** filters combine; the category filter is exact (spelling and case forgiving, "General physician" equals `general_physician`); an unknown mode or sort is refused (422); inactive services never appear (also in the category menu); only the patient's own customer is searched; search text is cut at 100 characters. With "soonest", services with no free time come last.
- **Limits:** this is a search of the services of ONE customer, not a national directory: no provider (doctor) profiles, no location or distance, no language, price, insurance or rating filter, no ranking beyond the two sorts, no paging, and no partner or outside listings (listing states and evidence in GHE-14 are not built). The filter lists categories as the staff typed them (`general_physician` shown as "general physician"), so inconsistent staff spelling shows as separate entries.

### DEV-047: a waiting list for services with no free time (ADD)

- **What:** The docs list a waitlist capability for partners (`WAITLIST_SUPPORTED`, GHE-14) but give no rules. When a service has no free time, the patient app offers "Tell me when one opens". The patient is told ("A time opened up") when a time appears, sees the list under Appointments, and can leave it. The staff Services page shows how many are waiting for each service. API: `GET/POST /api/v1/waitlist`, `POST /api/v1/waitlist/<id>/leave`; a new module `modules/waitlist`.
- **How it works:** the scheduling module now announces `slot.opened.v1` (carrying only the service id) when times are added, and when a booking is cancelled or moved and its time is free again. The waiting list listens in the worker. Joining and leaving use the same right as booking (`write:booking`), so the patient, or a guardian with a proxy, can do it and nobody else can.
- **Rules we added:**
  - A patient can join only a service that has NO free time right now (otherwise they should just book), once at a time (database constraint), and only an active service.
  - When times open, the people waiting longest are offered them: ONE person for each free time that nobody was told about, so a time is not promised to ten people. Each person is told once per offer.
  - The entry closes when the patient books that service (or moves a booking into it), or leaves. A stale notification is skipped if the entry closed before delivery.
  - An offer nobody uses goes back to waiting after `WAITLIST_OFFER_HOURS` (default 24), so the next opening can be offered again. A recurring job does this (same hook as DEV-045).
  - Events carry only ids; the text is written from the data when delivered.
- **Limits:** an offer is only a message: it does NOT hold the time, so someone else may still book it first (the patient is told to book soon). First come, first served by joining time: no priority, urgency or referral weighting (a referred patient waits like anyone else). One list entry per service, not per doctor or place. Staff have a Waiting list screen (queue order, place per service, remove someone), but cannot add a patient to the list, and a removed patient is not told. Staff removal is recorded in the audit chain under the staff login. No email or SMS for offers. Existing tests that counted events were updated because creating times now emits an event.

### DEV-048: encrypted storage of health data, and patient names (ADD, closes the first half of DEV-024)

- **What:** DEV-024 said names and birth dates were not stored because field-level encryption was not built. It now is (`foundation/crypto.py`), and the first data to use it is a patient's **legal name, birth date and phone**, kept in their own table (`trust_personprofile`). Patients enter their own under "My Profile"; clinic staff can enter or correct a patient's under Patients > Details. Staff screens, the doctor's screens, referrals and the waiting list now show the NAME where the viewer is allowed to see it, and fall back to the id otherwise.
- **How the encryption works:** AES-256-GCM (library `cryptography`; we do not write our own crypto). One master key per key id comes from `ZOIKO_FIELD_KEYS`; each customer gets its own data key derived from it (HKDF); each stored value is bound to its customer, table, field and row, so a ciphertext copied to another row, field or customer fails to decrypt (a test pastes one patient's name into another's row: it shows nothing). A fresh random nonce every time. Keys can be rotated (add a new key id; old values still open and are flagged `needs_rotation`). Errors reveal nothing; a value that cannot be decrypted shows as blank, not as junk.
- **Rules we added:**
  - Patients may read and write their OWN details (`person` is now read+write for R01); clinic operations staff (R04, and R13 who copies R04) may read and write details of patients of their organization. Doctors may READ the details of patients they have a care relationship with, never write. Zoiko support and safety staff, employers and others stay locked out of `person`.
  - Every read passes the policy engine for that patient (and is recorded as an access decision); the audit entry for a save says who changed it, never what it contains. Input is checked: a name of 1 to 120 characters without control characters; a birth date that is a real date between 1900 and today; a phone of 6 to 20 digits and simple punctuation.
  - Saving replaces all three fields together (the staff form loads the current values first, so nothing is blanked by accident).
- **Needs a decision from you or your lead (DEV-016):** where the production master key lives. Today it is an environment variable (`.env`, never committed; `.env.example` has a placeholder). That is only as safe as the server it sits on. The docs expect a managed key service, a rotation schedule and a plan for losing a key (a lost key means lost data). Until then this is good for development and demos, NOT a production claim.
- **Limits:** no search by name (values are random per encryption, so they cannot be looked up; a "blind index" would be needed); no sex or gender, address, national id or email-as-identity; no identity proofing or matching (DEV-024's other half is still open); a guardian cannot edit a child's details yet (read only through the proxy); existing rows are not re-encrypted automatically when keys rotate (`needs_rotation` exists, the job does not); backups contain the ciphertext but also depend on the key backup. The Docker image gained one dependency (`cryptography`); if a full image rebuild fails on a slow network, retry it.

### DEV-049: a patient's insurance coverage (ADD, first slice of D12 / GHE-17)

- **What:** A patient can record the insurance they hold (insurer, plan, member number, valid from and until) under Insurance & Claims, and the front desk can record or correct it for them (staff console, Patients > Details). This follows the docs' `finance.coverage_policy` (person, payer, plan, member reference, valid dates, several policies per person) and nothing more. API: `GET/POST /api/v1/coverage`, `GET /api/v1/people/<id>/coverage`, `POST /api/v1/coverage/<id>/end`; a new module `modules/coverage`.
- **Rules we added:**
  - The member or policy number is stored ONLY encrypted (DEV-048), bound to its own row. Lists show just its last four characters; the full number is never returned by any endpoint.
  - Patients may read and write their own coverage (`coverage` is now read+write for R01); clinic operations staff (R04, R13) may read and write it for patients of their organization. Doctors cannot read or change insurance (they have no `coverage` right), nor can Zoiko support, safety, or employers (R08 and R11 stay hard-prohibited).
  - A patient can hold up to 5 active policies at once; ending one makes room. A coverage past its end date is shown as expired, not deleted. Ending is a status change (history kept); repeating it is harmless.
  - Every add and end is audited with who did it, never what it contains.
- **Not built, and not claimed:** the insurer is just a name (insurers are not customers of the platform yet, so there is no payer organization, no insurer login, and no payer portal, role R07 has rules but no app). No eligibility check (we never ask the insurer whether the policy is valid), no claims, no pre-authorisation, no billing or quotes, no coordination of benefits order, no disputed-policy flag, no copy of the insurance card, and no use of insurance in booking or pricing. A patient's entry is self-declared and unverified.
- **Needs a decision from you or your lead:** the commercial model (who the insurers are, whether they join as customers, how a policy is verified, who pays whom, which markets) before anything beyond recording can be built.

### DEV-050: "My Care Team" and "who looked at my information" for patients (ADD)

- **What:** Two patient pages that were marked "soon". **My Care Team** lists the doctors who can see the patient right now (active, uncontested care relationships) and why (an appointment, a care-team assignment, a referral), with the dates. **Privacy & Consent** shows an access log: every time ANOTHER person looked at, or tried to look at, the patient's information, newest first, with who, when, the plain-words action ("Looked at your details") and the purpose ("to treat you"), and whether it was allowed or blocked. The sign-in page already promised "See who has looked at your information". API: `GET /api/v1/care-team`, `GET /api/v1/access-log`.
- **Rules we added:** the log is built from the access decisions the policy engine already records (nothing new is stored), only for patients this login may read (the patient, or a guardian with an approved proxy); the patient's OWN visits are left out, so the list is about other people; refused attempts are shown, because a blocked attempt is something a patient may want to know about. The log says nothing about WHAT was read, only that an access happened. It is capped at the latest 100.
- **Limits:**
  - People appear by login (an email address for staff and doctors), not by real name or role title: staff names are not stored (DEV-024 covered patients only).
  - The log only holds checks the system made: a read the policy engine never saw (for example a database administrator) is not there; this is evidence of application use, not a guarantee against misuse.
  - Every list read by staff creates one decision per patient shown, so a clinic receptionist opening a list fills the patient's log with "looked at your details" lines; a smarter grouping (one line per day per person) is not built.
  - The patient cannot dispute an access, ask who it was, or change who may see what: consent choices are still not built (the page says so).
  - A care team member is listed for as long as the care link is valid (30 days after the visit); there is no way for the patient to end it.

### DEV-051: family members: a patient acts for a child or relative, after the clinic approves (ADD)

- **What:** The docs (GHE-06) describe delegation: a guardian may act for someone else, only after proof and approval by a different person. The backend already had the building blocks (a guardian grant that is proposed, then approved by someone else). This adds the whole flow. A patient (a login with its own patient record) asks to add a family member under **Family Members**: relationship, full name, birth date. A patient record is created for that person and the request is **waiting**: nothing is shared. Clinic staff (R04, R13) see it under **Family requests**, check proof of identity and authority at the desk, tick a box saying so, optionally set an end date, and approve or reject. API: `GET/POST /api/v1/family`, `POST /api/v1/family/<id>/withdraw`, `GET /api/v1/staff/family-requests`, `POST .../approve`, `POST .../reject`.
- **Rules we added:**
  - Only a patient with a record of their own may ask (a doctor or receptionist cannot). At most 5 requests may wait at once. The relationship is one of parent, guardian, caregiver. The person's name and a real past birth date are required, and stored encrypted (DEV-048).
  - Approval requires the staff member to confirm they checked proof (the request is refused without it), and the approver can never be the person who asked (403). A request is approved or rejected only once (409 after).
  - Approval gives the patient the guardian role (R02). R02 can read the family member's details and book and see their appointments, never edit their details (read-only). It does not give access to the family member's insurance, which the guardian rule also leaves as read-only (`coverage` stays read for R02, read-write only for the patient's own).
  - The requester can withdraw a waiting request, or stop acting for an approved person, at any time; history stays. A rejected or withdrawn request closes the empty patient record that was made for it (it is ended, not deleted).
  - "Book for" now shows people by name, defaults to the patient themselves, and "My Profile" labels family members by name.
  - Audit entries say who requested, approved, rejected or withdrew, never the person's details.
- **Needs a decision from you or your lead (privacy, legal, clinical safety):** what counts as proof of identity and of authority in each market; whether a minor's own login or consent changes at some age (an adolescent may be entitled to privacy even from a parent; the docs list "adolescent services" as an exclusion, not built); how long approval should last by default (today: no end date unless staff set one); who may act for an adult who cannot decide for themselves; and what happens when two people each claim to be the guardian.
- **Limits:** there is no way for a guardian to add a family member who already has their own record or login (a new empty record is always made, so a duplicate record can exist); the clinic is not notified that a request is waiting (staff see it when they open the page); the patient IS told when the clinic approves or declines (an in-app notice that names nobody, written from an event that carries only the request id and the outcome), but not when a request is withdrawn; the guardian cannot edit the family member's phone or details; no documents are uploaded or stored (the check happens at the desk and is only recorded as a tick); and the family member has no login of their own.

### DEV-052: secure messages between a patient and their clinic (ADD, first slice of D16 / GHE-16)

- **What:** A patient (or a guardian, for the person they act for) writes to their clinic under **Messages**; clinic staff answer under **Messages** in the staff console and close a conversation when it is finished. A conversation is a THREAD with a subject and a list of messages. API: `GET/POST /api/v1/messages/threads`, `GET /api/v1/messages/threads/<id>`, `POST .../reply`, `POST .../close`; a new module `modules/messaging`.
- **Rules we added:**
  - The subject and every message are stored ONLY encrypted (DEV-048), each bound to its own row. Events, audit entries and notices carry ids only, never text.
  - Only patients and guardians (R01, R02) start a thread, and only to a clinic they belong to. Only clinic operations staff (R04, R13) answer, and only for threads of their OWN organization, which is checked twice (in the HTTP layer and again in the domain). Doctors and Zoiko staff are not part of this version (a doctor gets 403).
  - Unread is counted per side. Opening a thread marks it read for the side that opened it. The staff dashboard counts conversations with a new patient message; the patient is told ("New message") when the clinic writes, without saying what.
  - A closed thread cannot be replied to (409). A person is limited to 30 messages an hour (429). A message is 1 to 2000 characters, a subject 1 to 120; control characters are refused.
  - The patient page states plainly that messaging is NOT for emergencies or urgent medical problems.
- **Needs a decision from you or your lead (clinical safety, legal, privacy):** whether free-text messages with a clinic are allowed at all in each market and under what notice; what the clinic must do when a message describes a medical problem (who reads it, how fast, what is recorded); how long messages are kept and who may delete them; whether doctors may message patients directly (a message that gives advice may count as a clinical service); and whether patients may attach photos or documents.
- **Limits:** no attachments; no doctor messages; no patient-to-patient or staff-to-staff chat; no assigning a thread to a particular staff member or showing who answered by name (staff appear by login); no reply-by-email or push alert (a notice appears in the app); a closed thread cannot be reopened (the patient starts a new one); messages cannot be edited or deleted; nothing detects or escalates a message that mentions an emergency; the patient list does not refresh by itself except when the page is opened; the 30-per-hour limit is per person across all threads.

### DEV-053: clinic reports, counts only (ADD)

- **What:** The staff **Reports** page (it was marked "soon") shows operational numbers for the staff member's own organization over the last 7, 30 or 90 days: bookings made each day, where those bookings stand now, how many past confirmed appointments were attended, missed or not yet recorded (with an attendance rate), the busiest services, referrals received and how they stand, waiting-list activity, and conversation activity. API: `GET /api/v1/staff/reports?days=`; each module (scheduling, referral, waiting list, messaging) supplies its own `report` function and the endpoint combines them. It needs the `analytics` right, which only clinic operations staff hold.
- **Rules we added:** the result holds COUNTS ONLY: no patient id, name, message text or subject appears (a test checks this); numbers are for one organization, so another customer, or another site of the same customer, sees none of them; a period outside 7 to 90 days is refused (422); the attendance rate uses only confirmed visits whose time has passed.
- **Response times (added after the first version):** the system now records when a booking is confirmed (`confirmed_at`) and when the clinic first answers a message (`first_reply_at`), and Reports shows the middle value and the average of each. Only confirmations of waiting bookings count (a confirmed booking staff moved is not counted as a quick confirmation, and confirming twice changes nothing); only the FIRST reply counts. Earlier bookings and messages have no recorded time and are left out, so the figures start from this change. Hours are shown as minutes, hours or days; with no data the page shows a dash, never a zero.
- **Limits:** days are UTC dates, not the clinic's local days, so a booking made late in the evening can fall on the next day; there is no comparison with a previous period, no export, no per-doctor or per-service filter (a single response target with a dashboard warning exists, see DEV-054; no per-service targets); counts are not suppressed when small, which is acceptable for a clinic's own staff but would not be for any wider audience; the page does not show money, claims or clinical outcomes, because none are stored.

### DEV-054: "running late" warning on the staff dashboard (ADD)

- **What:** the staff Dashboard shows a red-flag line when work has waited longer than the clinic's response target: bookings still waiting for confirmation, and conversations whose newest patient message the clinic has not opened. The target is one setting for the whole deployment, `RESPONSE_TARGET_HOURS` (default 24). API: the `late` block of `GET /api/v1/staff/dashboard` (`hours`, `bookings`, `messages`: numbers only).
- **Rules we added:** a booking counts only after it has waited MORE than the target, counted from when it was made; a message counts only when the clinic has not opened it and its newest message is older than the target; confirming or opening clears it at once.
- **Appointments list:** each booking in the staff list carries `late` (true only for a booking still waiting for confirmation after the target) and the page shows a red "Late: needs confirmation" label, so staff can see WHICH bookings the dashboard count refers to.
- **Usual confirmation time for patients:** the service list (`GET /services`) now carries `usual_confirmation_hours` per service: the middle value of how long that clinic took to confirm bookings made in the last 90 days, shown on Find care as "Usually confirms within about N hours". It appears only when the clinic has at least 5 confirmations in that window, so a few bookings can never be traced to one patient; below that it is null and nothing is shown. It is clinic-wide, not per service, and uses the existing `read:catalog` right (it is aggregate numbers only). Bookings made before response times were recorded are not counted.
- **Limits:** the target is not per clinic or per service (choosing the right number is a business decision, so 24 is only a starting value); a message is timed from the newest patient message, so a thread that has waited long but just got another message is not flagged until that one is old too; no email or push alert, only the dashboard line; bookings are counted for the whole customer, like the existing "to confirm" count.

### DEV-055: add an appointment to the patient's calendar (ADD)

- **What:** an "Add to calendar" button on each upcoming appointment in the patient app. API: `GET /api/v1/bookings/<id>/calendar` returns a standard `.ics` file (service and clinic, place, start and end in UTC, `CONFIRMED` or `TENTATIVE`).
- **Rules we added:** it needs the same `read:booking` right as viewing the booking (the patient who owns it, or the clinic's staff); a cancelled booking has no file (404); the file holds no patient name, id or message text, and no calendar alarm (the system's own reminder does that job).
- **Limits:** it is a one-time download, not a live calendar feed, so it does not update if the appointment is moved (the patient downloads it again; the same booking id as UID lets most calendars replace the old entry); no doctor name in the file (names are not stored, DEV-024); the file is only checked by tests, not opened in every calendar app.

## B. Open and not ours to decide (ESCALATE)

| ID | Question | Docs ref | Owner |
|---|---|---|---|
| DEV-011 | First market, buyer and journey (we use J1 for learning only) | ADR-07 | Board / Commercial |
| DEV-012 | Zoiko's legal operating role per service (infrastructure vs marketplace vs provider) | ADR-06 | Board + Legal |
| DEV-013 | Retention periods, key rotation, transfer destinations per data class and market | ADR-10 | Legal / Privacy / DPO |
| DEV-014 | One incident-severity scale: SEV-C/1..4 (GHE-18) vs SEV-0..3 (GHE-20) vs P0..P3 (GHE-09) | GHE-09/18/20 | Security + SRE + Clinical Safety |
| DEV-015 | Statuses the docs never enumerate: qualification status, JIT-grant status, claim verification status, partner operation full list, webhook subscription full list, rights-case state, actor status | GHE-06/09/13/05 | Module owners |
| DEV-016 | Cloud provider, IAM vendor, message broker | ADR-08 | CTO |
| DEV-041 | May Zoiko store clinical notes and results at all, and with what encryption | ADR-06, DEV-024 | Board + Legal + Privacy |

## C. Verification debt (things we have not checked ourselves)

- GHE-02 (buyer economics and journey prioritization) has not been read.
- Table and field details for GHE-04..09, 11..14, 16..23 came from four agent extractions and were not re-verified line by line against the source files.
- `/readyz` and the Postgres connection have not been run (Docker Desktop was off).
- Nothing has been reviewed by your lead or a clinical-safety owner.
