# Conformance audit: the code against the specification

Written 2026-11-06. Four independent reviewers each read one area of the code against the original
specification documents (the 27 Word files in `Downloads/zoiko access`), looking for three things: code that
**deviates** from the specification, code that **invents** something it does not say, and wording that is
**misleading**. I then checked the main claims myself against the code and the spec text. Nothing was run:
this is a reading audit. It is not a security review, and it is not a clinical safety review.

A finding is "fixed" only if the specification is clear and no business decision is needed. Everything
else is listed below with who has to decide, so nothing is built on a guess.

## 1. Fixed now (DEV-104)

| What was wrong | Spec | Fix |
|---|---|---|
| **My own mistake:** I wrote that the docs are silent about where an AI permit goes after a change review. GHE-23 states it. | GHE-23 state model | A change review now goes to **validation** if the change is material, or back to **authorized** if it is approved as not material. Old sign-offs no longer carry over; fresh ones are needed. |
| The AI evidence rows copied the model and prompt fingerprints from the permit, never checking what actually answered. | GHE-23 (the digests must match) | A real model now states its own fingerprints; the gate refuses to call it if they differ from the permit. |
| Roles R05, R06, R07 could read the name of every patient in the customer (no orders or claims exist to justify it). | Roles matrix: "minimal" | That blanket read is removed until orders and claims exist. |
| Support (R11) could read message content. | Roles matrix: "metadata" | Messages are now a hard prohibition for support. |
| "Booked for ... The clinic will confirm it" in green after only a request. | GHE-15: requested and confirmed are different states | "Request sent ... It is not confirmed until the clinic confirms it", in the warning style. |
| A green "In force" label on insurance, computed only from a date the patient typed; nothing is checked with an insurer. | GHE-17: an unchecked coverage must show as unverified | The label is now "Dates current", with a plain note that nothing is checked with the insurer. |
| A green "Verified" mark on doctors, which only means a clinic administrator ticked a desk check. | GHE-06: verified needs a register and privilege check | The mark now says "Name checked" and the pages say it is a desk check, not a licence check by Zoiko. |
| "Referral accepted" shown when a receptionist clicked. | GHE-14: acceptance is clinical | "Referral accepted by the clinic". |
| "Recommended for You" over the first four services (no recommendation exists). | GHE-08: AI is off | "Services you can book (not personalised)". |
| Compliance page said every access is recorded and the audit log cannot be edited. | The chain detects edits; it does not prevent them | Reworded to say what is true. |
| "Waiting for an answer" computed from unread; a stale sentence about patient names; referral hints promising a review and "told at each step". | GHE-15: honest states | Reworded or removed. |
| Moving an appointment released the old time without saying so. | GHE-14 WF-13 | The screen now says the current time is released as soon as a new one is chosen (the behaviour itself is item 4 below). |
| No skip link, no visible focus outline, no reduced-motion or forced-colours rules. | GHE-15 section 04 | Added to all four apps. |
| Out-of-date comments (policy rules said relationships were "not built yet"; encryption notes implied automatic re-encryption). | | Corrected. |

## 2. Found, not fixed: needs a decision or a real build

**Most important (privacy and safety)**

| # | Finding | Spec | Size | Who decides |
|---|---|---|---|---|
| 1 | **Done on a proposed definition (DEV-107):** staff now reach only patients connected to their own site (booking, referral, waiting list, message, family request or care link). The lead still has to confirm what "minimal" means. Original finding: staff of any clinic site could read the name of every patient of the whole customer. The "organization" rule checks only the role's site, never that this patient belongs to it. | GHE-06 I-04; roles matrix | M | Lead: what "minimal" means (a booking? a referral? a consent?). |
| 2 | **Site-tying done and checked (DEV-106):** a link now counts only while the doctor holds a care role at that site. The 30 days is still our own number. Original finding: a doctor's care link was not tied to a site, so a doctor who moved kept access. It is created automatically when a clinic confirms a booking, and lasts 30 days after the visit (a number we made up). | GHE-06 AT-20 | M | Lead: the 30 days; site-tying can be built now once agreed. |
| 3 | **Scopes done on a proposed design (DEV-110):** staff choose what a guardian may do when they approve, and clinical records are closed. Still open: proof of the legal relationship and age or capacity rules. Original finding: a guardian's access had no scope: every guardian can read diagnostics, medication, referrals and messages for the dependent. "Verified" is a tick box with no evidence. | GHE-06 AT-15, AT-18 | M | Lead: the rights list and proof needed. |
| 4 | **Done on a proposed design (DEV-108):** a patient's cancel or move of a confirmed appointment now waits for the clinic and the original stays until then. The lead still has to confirm the details (who answers, deadlines, taking a request back). Original finding: moving a confirmed appointment cancelled the confirmed one first; cancelling was immediate. The spec says the original stays until the new one is confirmed and until the source acknowledges a cancel. | GHE-14 WF-13, T-26, T-27 | M | Lead: this adds "change requested" and "cancel requested" states. |
| 5 | **Done on a proposed design (DEV-109):** a named owner and fallback answer referrals, staff can only mark them received, and an unanswered one is flagged and escalated. The lead still has to confirm who may own an inbox, the 24 hours and what escalation does. Original finding: referrals had no named receiving owner, fallback or acknowledgment step; any clinic administrator could accept. | GHE-14 WF-16/17 | M | Lead: who owns an inbox; fallback. |
| 6 | **Done on a proposed design (DEV-111):** a person can state why they are acting; it is checked against their role, recorded, shown to the patient, and "continuity of care" is read only. A reason per patient opened is not built. Original finding: purpose limitation was cosmetic: the purpose comes from the role, nobody states a reason, and "continuity of care" is never used. | GHE-06 section 12 | M | Lead. |
| 7 | Nothing checks that a role fits the customer type. **Partly done (DEV-105):** the employer and payer roles now go only to employer and payer logins, and those logins hold nothing else. The full table is still open. | Prohibition 1 | S | Lead: the role-to-customer-type table. |

**Important but smaller**

- The "workflow" module is a task list (3 kinds, 3 states, one flat 24-hour target), not the specified durable workflow
  (versioned definitions, timers, exceptions, fallback owner). The 24-hour target is our own choice; the spec says there is no
  universal timeout. (Real engine: needs the workflow-engine choice, ADR-08.)
- The audit record: **partly done (DEV-114):** it now holds actor kind, purpose, session fingerprint, source service, build and policy digests, all inside the hash. Still missing: the organization on each row, a "step-up" result, signed checkpoints, and opening an incident on a broken chain (it only reports).
- Messages: **partly done (DEV-113):** opening is recorded per person and shown as its own state, unread is per person, and an email keeps a fingerprint of what was sent. Email send attempts are now kept one row each (DEV-121). Still not built: provider receipts (delivered, bounced, opened) and acknowledgment states. One staff reply clears the unread count for everyone. The email body is erased after sending, so no evidence of what was sent.
- Releases and incidents are one-person notes, lighter than the spec's signed, dual-control decisions. The severity labels now follow GHE-20's SEV-0 to SEV-3 (DEV-118, temporary until the owners choose one scale, DEV-014).
- Status words: **guardian links now use all eight statuses (DEV-119)**. Still different: an access decision is a yes or no, not permit, deny, step-up, review; and several other enums (booking, referral, notification, escalation) use fewer values than the spec.
  The events the spec names: **delegation revoked, privilege revoked and access denied are now emitted (DEV-116)**; nothing consumes them yet, and the patient-link and identity-proofing events need features that do not exist.
- Encryption uses one master key from the environment: no envelope encryption or key service (DEV-016), and
  login and email are stored in the clear. A re-encrypt command now exists for key changes (DEV-122); it is run
  by hand, not on a schedule.
- Employers have `analytics` and `billing` read with no small-count suppression (nothing uses it yet).
- Data rights: patients can now file the seven kinds of request and the clinic tracks and closes them (DEV-126). Missing: legal deadlines per country, guardian requests, and the actual erasure or export (still done by hand).
- Customers now carry a country and a data request gets its due date from a signed pack for it (DEV-133). Still missing: any other pack setting being applied, reminders or escalation when a request is overdue, and handling of a patient in a different country from the clinic.
- Webhooks: a clinic administrator can now register signed outbound webhooks with retries and a dead-letter list (DEV-127). Missing: inbound callbacks, partner credentials and rate limits, published API documents and SDKs, connector certification.
- Country packs: Zoiko staff can now draft, check, review and approve a country pack on record (DEV-128). Missing: everything from staging onward (nothing can go live), signatures, a resolver that applies a pack, and a link from each customer to a country, so no pack setting affects any customer yet.
- Sign-in: patients can ask for a 30-day login (DEV-129). Missing: a list of signed-in devices, asking for the password again before a sensitive action, and a shorter life on shared devices.
- Observability: request counts and two service-level indicators exist, with a guarded metrics endpoint and a platform page (DEV-130). Missing: the 30-day budget and burn alerts (needs a metrics store), paging, journey indicators that check a booking was actually correct, a clinical safety sentinel, traces, and runbooks.
- Partner messages in: partners can send signed messages that are checked and recorded (DEV-131). Missing: anything acting on a message (the workflow engine), client certificates and proof of key possession, sandbox/production stages and certification, dual-key rotation overlap, and per-partner failure alerts.
- An overdue data request now appears as late work in the clinic's queue (DEV-140). They are also counted in the dashboard's late numbers and named in the daily operations email. Still missing: any escalation, patient reminder or regulator notice when one goes late.
- Billing records exist (DEV-139): prices per service, invoices and credit notes in an append-only ledger, and a patient bills page. Missing: any payment, card or bank handling, a payment provider, refunds, tax, per-country invoice formats, insurer, payer and employer billing, claims, entitlements, marketplace payouts and reconciliation.
- A requirements matrix exists (DEV-138) holding the 124 recorded readings of the workflow and API acceptance tests; a requirement cannot be called met without naming a test. Missing: any run of the tests, the other specifications' requirements, hazard links, and any link to the go-live register.
- A go-live readiness register exists (DEV-137): signed, expiring, release-bound attestations, vetoes and a pack check give NOT READY, READY (G3) or AUTHORISED (G4). Missing: any deploy or enforcement, pilot and live stages, cohort control and rollback, real signatures and build digests, the requirements matrix (GHE-19), a dossier export and real approver roles.
- Starting a workflow is idempotent when given a key, and webhook deliveries that gave up are counted on an administrator's dashboard (DEV-145).
- Approving a guardian now records what proof staff saw and where it is kept (DEV-144). Still missing: any check of the document, and tying a kind of proof to a relationship or an age rule.
- A release is now held at 'not ready' while any hazard is unresolved or the hazard register is empty (DEV-143). It still stops no real release and is not linked to build digests or test runs.
- Secure communications (GHE-16: 3 met, 27 partly, 34 not built of 64), identity and access (GHE-06: 7, 13, 20 of 40) and clinical safety (GHE-08: 8, 19, 27 of 54) have been read against their own acceptance cases (docs/COMMS-TRACE.md, IDENTITY-TRACE.md, SAFETY-TRACE.md, DEV-142). The developer's reading, not a run of those cases; nobody has reviewed it.
- The country-pack work (GHE-04: 6 met, 17 partly, 13 not built of 36) and the cloud-operations specification (GHE-18: 5, 18 and 57 of 80) have been read against their own acceptance cases (docs/PACKS-TRACE.md, docs/OPERATIONS-TRACE.md, DEV-141). The developer's reading, not a run of those cases; nobody has reviewed it.
- The external-API and webhook work has been read against the specification's own 64 acceptance cases (docs/API-TRACE.md, DEV-136, DEV-146): 13 met, 25 partly, 26 not built. This is the developer's reading, not a run of those cases, and nobody has reviewed it.
- The workflow engine has been read against the specification's own 60 acceptance tests (docs/WORKFLOW-TRACE.md, DEV-135, DEV-145, DEV-147): 25 met, 15 partly, 20 not built. This is the developer's reading, not a run of those tests, and nobody has reviewed it.
- Workflow engine: a kernel exists with frozen versioned definitions, evidence-gated moves, an independent health overlay, timers that open escalations for a named person, and an append-only history (DEV-132). Missing: a durable engine vendor and replay, the four-way partner handoff, result review and amendment, outbox-driven external commands and source reconciliation, compensation, retry budgets and dead letters for external calls, definition migration, country-pack timers, a patient-facing status screen, and anything that starts a workflow from a booking or a partner message.
- AI gate: tools are now pinned by fingerprint and checked on every run (DEV-125). Still missing: a step that removes
  or minimises personal details before the model is called, a check that the permit's lane and level agree, and "quarantined" is used for an empty answer (the spec gives it no such meaning). Also an open
  question: should AI permits live in the clinic console (as now) or under platform safety staff?
- All four apps now say when old data was last updated if a refresh fails (done, GHE-15 section 05; DEV-134). The patient app's individual pages still show only their own errors. Patient entry has no language switch or "get assistance"
  control (WF-03).
- The service catalog: **done (DEV-115):** services now go draft, waiting for review, reviewed, published (or withdrawn), with a second person required and bookings and referrals only for published services. Still missing: the UNVERIFIED and REVIEW REQUIRED states, scheduled re-verification, and evidence files or registry checks.
- `identity_status` is never set by anything, so the staff patient list no longer shows it (DEV-123); real identity proofing needs the market and provider decision. The API field `in_force` keeps its old name behind the new label.
- Comments in the append-only migrations say "even for the table owner"; an owner could drop the trigger on purpose (the hash chain would
  then show the edit).

## 3. Checked and fine

Booking confirmation really is limited to clinic staff and a request is never shown as confirmed. Times are stored in UTC with the
clinic's zone. The not-built pages are honestly marked. The development sign-in shortcut exists only in development builds. The
permit states, AI levels and lanes, hazard references and status words match the schema. Support cannot reach patient data. The
two-person approval for customer suspension matches GHE-20. The employer, patient and platform screens showed nothing the roles matrix forbids.

## 4. What I could not check

I found no statement in the documents about who may authorize AI permits, so that temporary rule (DEV-094) cannot be confirmed from them.
The reviewers read most, not all, of the code; patient matching (`match_case`) and several tables the schema lists do not exist at all.
