# Zoiko Access: Data Schema Design (sketch, not code)

Status: DESIGN SKETCH derived from GHE-01..23, R3.0 backend master, GHE-RR-01. Nothing here is approved.
The docs say all types are illustrative, so every column type below is INFERRED. Anything the docs do not specify is marked INFERRED or listed in "Open questions".

## Files in this pack

| File | Contents |
|---|---|
| README.md | This file: conventions, IDs, dates/times, build phases, open questions |
| 01-er-diagrams.md | Mermaid ER diagrams (relationships) per area |
| 02-tables-governance-and-trust.md | Global plane + identity, policy, privacy tables |
| 03-tables-care-flow.md | Catalog, scheduling, referral, workflow, exchange, evidence, infra tables |
| 04-tables-commercial-comms-integration.md | Payer/finance, communications, partner API/connector tables |
| 05-tables-safety-security-ops-quality-ai.md | Safety, security, ops/SRE, verification, go-live, activation, AI tables |
| 06-enums.md | Every status/enum value found in the docs |
| 07-roles-access-matrix.md | R01-R12 x data: who may see/do what |

## 1. Architecture rules that shape the schema (non-negotiable, from the docs)

1. **Two planes.** *Global governance plane* holds NON-patient metadata only (packs, catalogs, schemas, release manifests, hashes). *Regional cells* hold all PHI/person data. One physical DB (or schema set) per cell. No global patient table, no global person key, no foreign key across cells. [GHE-03, 05, 10 DAT-01]
2. **Every cell table carries `cell_id` and `tenant_id`** (NOT NULL, taken from trusted context, never from the request body, part of every index). [GHE-11 CT-04]
3. **Source systems stay authoritative.** We store references + provenance + minimal operational state, never a copy of the clinical truth. Raw payloads live in an encrypted source vault; tables hold `vault_object_ref` + digest.
4. **Append-only evidence.** Transitions, audit, evidence, delivery attempts/receipts, booking events, decisions are insert-only with hash chain (`prev_digest`, `record_digest`). Corrections add a new row linked to the original (`supersedes_id` / `corrected_by`). Nothing is overwritten or deleted by app roles. [GHE-10 §7]
5. **State + evidence + outbox commit in ONE transaction.** [GHE-11 §7]
6. **Optimistic concurrency** (`entity_version`) on every mutable aggregate. [GHE-11]
7. **Idempotency** on every write endpoint: unique (scope, key) + request digest; same key with different body = CONFLICT. [GHE-11, 13]
8. **Four evidence kinds never collapse:** transport receipt, authenticated view, professional acknowledgment, source-confirmed outcome. Different tables/event types, different actor requirements.
9. **Deny by default.** Missing/unknown/expired = DENY. UNKNOWN is a real stored state, never mapped to success.
10. **No PHI in global tables, URLs, logs, telemetry, AI prompts.**

## 2. Identifier convention (answers "IDs / serial no")

Every row has up to three identifiers, each with a different job:

| Column | Type (INFERRED) | Job | Exposed? |
|---|---|---|---|
| `id` | uuid (UUIDv7) | Internal primary key; time-sortable = good index locality | Never outside the cell |
| `public_id` | random 128-bit (UUIDv4 or random base32) | Opaque ID used in URLs, APIs, events, QR/links | Yes |
| `ref_no` | text, e.g. `APT-C01-000042` | Human-readable serial number for staff/support/invoices (prefix + cell code + zero-padded sequence from a per-cell, per-entity DB sequence) | Staff UI, emails to staff, PDFs. NOT in URLs, NOT an auth key |

Why three, not one:
- GHE-10 §4 allows UUIDv7 for internal IDs, but GHE-13 §03 says public IDs must **never encode a timestamp**, national ID, MRN or name. UUIDv7 embeds a timestamp, so it stays internal and a separate random `public_id` faces outward.
- A human serial number (`ref_no`) is useful for support and invoices but is enumerable. So it never grants access and every read still passes the policy check (GHE-15: URL identifiers are opaque cell-scoped refs).
- National IDs, NHS/SSN/MRN, payer member IDs are **never** keys. They live in `source_patient_link.external_id` qualified by `assigning_authority`. [GHE-06, 10]

Proposed `ref_no` prefixes (team decision, see Open questions): PER (person), ORG, FAC, PRC (practitioner), SVC (service), APT (appointment/booking), REF (referral), WFI (workflow instance), TSK (task), EXC (exception), ORD (diagnostic order), RES (result), CLM (claim), AUT (authorization), QTE (quote), INV (invoice), PAY (payment), RFD (refund), MSG (message), INC (incident), HAZ (hazard), TRQ (transfer request), DSR (rights case).
Counter columns (`ref_no`) are not required to be gapless. No year in the format by default (year is a date; add only if the team wants it).

## 3. Date / day / time convention (answers "date, day, time")

| Rule | Detail |
|---|---|
| Store instants as `timestamptz` in **UTC** | Column suffix `_at`. Example `created_at`. |
| Keep the original zone | For anything clinical/scheduling/partner: `source_tz` (IANA, e.g. `Europe/London`) + `source_utc_offset` + `dst_fold` flag. Appointments: `start_at`, `end_at`, `original_zone`, `offset`, `fold`. [GHE-14, 12] |
| Date-only facts stay `date` | A source that only says "2026-10-09" is NOT converted to an instant. Add `time_precision` (year/month/day/minute/second). [GHE-07] |
| Day-of-week is NOT stored | It is derived from the instant + zone at read time (a stored weekday goes stale across DST/zone). Reporting views may add a generated column. |
| Different clocks, different columns | `occurred_at` (real-world event), `effective_at` (clinical applicability), `source_recorded_at` (source's own statement), `received_at`/`ingested_at` (our arrival), `recorded_at` (our commit, trusted time). Never overwrite clinical time with ingest time. [GHE-10 §4] |
| Validity intervals | `valid_from`, `valid_to` / `expires_at` / `revoked_at` on grants, packs, credentials, permits, claims. Open-ended = NULL `valid_to`. |
| Timers | `deadline_utc` + `time_zone` + business calendar; event-time vs processing-time rule stored per timer. Stale timer firing after completion is a no-op. [GHE-12] |

## 4. Standard column sets

**A. Mutable aggregate (most state tables)**
`id, public_id, ref_no?, cell_id, tenant_id, classification (D0-D6), purpose_code?, status, entity_version, country_pack_ref?, policy_version?, correlation_id, created_at, created_by_actor_id, updated_at, updated_by_actor_id`

**B. Append-only evidence/event row**
`id, cell_id, tenant_id, event_id (unique), event_type, schema_version, aggregate_ref, occurred_at, recorded_at, actor_id, actor_authority_ref, policy_decision_ref, evidence_ref, correlation_id, causation_id, prev_digest, record_digest` (no update/delete grants)

**C. Versioned immutable definition (packs, workflow defs, templates, metric defs, manifests)**
`id, key, semver, revision, content_hash, status, signed_by[], signature, valid_from, valid_to, supersedes_id, created_at` (a released revision is read-only; edits make a new revision)

**D. Reference/junction rows**
Junction tables carry `valid_from`, `valid_to`, `created_at`, plus the two FKs.

Data classification tag (D0 public, D1 org confidential, D2 personal, D3 sensitive clinical, D4 financial/insurance, D5 secrets/security, D6 safety/legal evidence): derived data inherits the strictest class. D2/D3/D6 never leave the cell.

## 5. Build phases (which tables first)

| Phase | Goal | Tables (tagged `[P1]` etc. in the catalogs) |
|---|---|---|
| **P1 Foundation + J1 slice** | One cell; patient requests appointment; provider confirms; full audit | cell/pack (minimal), trust (tenant, org, facility, actor, person, source link, role, membership), policy (purpose_grant, access_decision), catalog (practitioner, service, location, listing), scheduling (booking_intent, partner_booking, booking_event), workflow (definition, instance, transition, task, timer, exception), evidence (audit_event, outbox, inbox, idempotency, evidence_record) |
| **P2 More roles + referral/diagnostics** | Clinician, ops, lab, referral loop | referral*, exchange (envelope, vault ref, provenance, mapping), match_case, relationship_grant, comms (intent, message, attempt, receipt, preference), connector/partner basics |
| **P3 Commercial** | Payer, employer, billing | payer/finance tables, entitlements, analytics release |
| **P4 Hardening/gated** | Safety, security programs, SRE, quality, go-live, AI/device | safety, security, ops, quality, activation, ai |

Safety/governance tables (hazard, incident, intended purpose) are needed before any *clinical* capability is switched on, even in P1/P2, so their design is done now and implementation follows the gate.

## 6. Conflicts and gaps found while reading (decide before building)

1. **Workflow states differ across docs.** R3.0/GHE-12 universal: REQUESTED, VALIDATED, ACCEPTED, IN_PROGRESS, OUTPUT_AVAILABLE, CLINICAL_REVIEWED, HANDOFF_ACKNOWLEDGED, CLOSED/CANCELLED/BLOCKED. GHE-01 generic list: REQUESTED, VERIFIED, ROUTED, PARTNER_ACCEPTED, IN_PROGRESS, EVIDENCE_PENDING, COMPLETED, REJECTED, CANCELLED, EXPIRED, EXCEPTION, ESCALATED. Schema stores the GHE-12 set as authoritative and treats the GHE-01 words as journey-level labels mapped in the workflow definition.
2. **Incident severity enums clash.** GHE-18: SEV-C, SEV-1..4. GHE-20: SEV-0..3. GHE-09: P0..P3. Need ONE enum (proposal: `severity_code` lookup table with a per-incident-class mapping).
3. **Identifier rule clash.** UUIDv7 (GHE-10) vs "public IDs never encode timestamp" (GHE-13). Resolved above with `id` + `public_id`.
4. **No explicit tables in GHE-11 for** patient, appointment, slot, invoice, and no column types or retention TTLs. All INFERRED here; retention is a per-market ADR (ADR-10).
5. **Actor categories (GHE-01: 12) vs roles (GHE-15: R01-R12)** are not identical lists. `role` is its own catalog table so either can be configured.
6. **Statuses the docs do not enumerate** (stored as text + CHECK to be defined later): professional qualification status, JIT-grant status, claim verification status, partner operation full list, webhook subscription full list, rights-case state, actor status, SLO alert record.
7. **Evidence is described under 6 different names** (EvidenceRecord, EvidenceArtifact, evidence, evidence_ref...). Schema uses ONE polymorphic `evidence_record` table.
8. **Approvals/sign-offs are described under 5 names** (GateDecision, AssessmentDecision, approval_event, pack_approval, sign-offs). Schema uses ONE `approval_decision` table.
9. **REG-01..08** are defined in the R3.0 master §07 (legal, privacy, clinical/device, identity/language, integration, finance/tax, operations, market readiness). GHE-04 does not redefine them. The pack manifest stores them as 8 evidence slots.
10. **Open business decisions (not engineering):** first market, buyer, journey, framework, DB, workflow engine, IAM vendor (ADR-07, ADR-08 OPEN). Schema is vendor-neutral; Postgres-class SQL is assumed only for sketching.

## 7. Open questions for you / the lead

1. Which journey/market is first (J1 recommended)? This sets which P1 tables need real columns first.
2. `ref_no` prefix list and whether a year is wanted in the serial.
3. One database per cell with schemas per bounded context (recommended for the modular kernel) or separate databases per context?
4. Is Row-Level Security by `tenant_id` + `cell_id` acceptable as a belt-and-braces layer? (GHE-10 notes row isolation alone is NOT enough for multi-role clinical records: field/record policy is still required.)
5. Retention periods per data class (no global value exists in the docs).
