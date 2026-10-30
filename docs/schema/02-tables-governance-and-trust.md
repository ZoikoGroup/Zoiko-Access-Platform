# 02. Tables: global governance plane, identity/tenancy, policy/privacy

Notation: `PK` primary key, `FK→` foreign key, `UQ` unique, `[P1]..[P4]` build phase (README §5). Every CELL table also has the standard columns from README §4 (cell_id, tenant_id, created_at, ...); they are not repeated. "Std-A" = mutable aggregate columns; "Std-B" = append-only columns; "Std-C" = versioned immutable columns.
Source tags: [04] [05] [06] ... = GHE document number. INFERRED = docs imply it but give no fields.

---
## A. GLOBAL GOVERNANCE PLANE (schema `gov`, NO patient data)

Allowed here: pack ids/digests/status, schema ids, release manifests, hashes, aggregate health. Forbidden: identifiers of people, clinical content, raw IPs, free text, support tickets, legal memoranda. [R3.0 §03, GHE-10 W01]

### gov.jurisdiction_pack [P1 minimal] [04]
Std-C. One row per pack.
- `pack_id` (stable key e.g. `COUNTRY-X.PROVIDER-Y`), `market_code` (ISO 3166-1), `subregion_codes[]`, `authority_scope`, `service`, `intended_purpose`
- `status` (see enums: pack lifecycle), `current_revision_id`
### gov.pack_revision [P1 minimal] [04]
Immutable once SIGNED.
- `revision`, `schema_version` (semver), `manifest_json` (capabilities map, forbidden_capabilities, record_exchange_profiles, connectors[]), `digest` (sha256 of canonical bytes), `signing_cert_id`, `starts_at`/`effective_from`, `expires_at`/`effective_until`, `revocation_epoch` (int), `next_review_at`
- Ownership: `legal_entity_id`, `operator_ref`, `clinical_provider_ref` (may be NONE), `licensed_service_provider_ids[]`, `contract_ids[]`
- Deployment: `data_class_allowlist`, `hosting_zones`, `dr_zones`, `support_residency`, `backup_locality`, `cross_cell_default` (= DENY)
- Dependency versions: `kernel_ver`, `iam_policy_ver`, `clinical_scope_ver`, `security_profile_ver`, `connector_ver`, `cell_release_ver`
- `evidence` slots for REG-01..REG-08 (legal, privacy, clinical/device, identity/language, integration, finance/tax, operations, market readiness) each `{state, evidence_hash, approver_role}`; missing slot = capability disabled (fail closed)
- Rules: editing any field makes a new revision and voids signatures; one active revision per pack+cell+market; overlapping packs resolve to the stricter intersection; unknown schema keys rejected.
### gov.pack_approval [P1 minimal] [04]
Append-only. `pack_revision_id FK→`, `approval_role` (legal counsel, clinical lead/CSO, DPO, CISO, platform CTO, country operating exec), `signer_ref` (minimized), `bound_hash`, `signed_at`, `signing_cert_id`, `rationale`. Author cannot self-approve; any legal/clinical/security/privacy veto beats commercial approval.
### gov.pack_cell (junction) [P1]
`pack_revision_id`, `cell_id`, `staged_at`, `activated_at`, `suspended_at`. UQ(active) per pack+cell+market.
### gov.capability_state [P2] [04 §04]
Per country+service+capability: `state` (UNAVAILABLE, ASSESSED, IMPLEMENTED, APPROVED, ACTIVE, SUSPENDED, RETIRED), control sets A..H status (legal, clinical/professional, privacy/data, security, interoperability, financial/payer, users/patients, operational).
### gov.market_readiness [P2] [04 §09]
Per country+service: `readiness` (NOT_ASSESSED .. RETIRED), `gate` G0..G4, `decision` (APPROVE/RETURN/REJECT), `veto_by`. Summary of signed evidence, not a legal authorization.
### gov.market_activation_packet [P2] [04 App.A]
One per country/subregion/service: operator accountabilities, licences (dates), data/hosting (cell ids, flows, controller/processor map), partners/commercial, trust (pack digest, signatures, code digest, backup/restore, suspension runbook), approval status G0-G3 + expiry, scope of approval (enabled/blocked features, populations, sites, roles, channels), synthetic test results, incident contact, SLO.
### gov.activation_request / gov.preflight_result [P2] [04 §07]
Request: target cells, named capabilities, signed pack digest, evidence hashes, approver roles, rationale. Preflight: 7 check rows `{check, status: PASS|BLOCK|PENDING|N/A-REVIEW|EVIDENCE, evidence_version, approver}`; outcome BLOCKED or not; UNKNOWN never PASS.
### gov.cell [P1] [18, 05]
- `cell_id` (opaque), `country`, `subdivision`, `regime_context`, `physical_region`, `hosting_vendor`, `backup_region`, `kms_ref`, `subprocessor_refs`, `support_origins`, `privacy_class`, `clinical_safety_class`, `operator_ref`
- `lifecycle` (Proposed, Privacy assessed, Approved for limited pilot, Active, Suspended, Decommissioning)
- `gov.cell_manifest` (Std-C signed): cell_id, jurisdiction_pack_ref (hash), services, data.{db_local, audit_local, backups}, telemetry, release.{image_digest, approval_id}, failover.{targets[], require_operator}, evidence refs.
- `gov.cell_provision_request`: country, regulatory pack, contract, cell class, approver evidence, expiry, trace id, state (incl. WAITING_APPROVAL).
- IaC records: `module_state` (deployment_project, network, key, data, runtime, observability), `iac_plan_hash`, `applied_state_hash`, `drift_exception`.
### gov.capability_definition [P4] [21]
Std-C. `capability_id`, `domain_id` (D01..D18), `service_code`, `intended_purpose`, `claims`, `version`, `risk_tier`, `owner`, `state` (definition maturity).
### gov.activation_instance [P4] [21]
Cell-scoped runtime instance: `activation_id`, `capability_id/version`, `market_pack_id/version`, `cell_id`, `operator_id`, `partner_refs`, `feature_scope`, `state` (lifecycle enum), `activation_revision` (If-Match). Runtime ALLOW = conjunction (pack valid AND safety release AND legal/privacy AND operator evidence AND identity AND partner dependency AND evidence hash). A feature flag is visibility only.
### gov.feature_safety_manifest [P4] [08]
Signed, immutable, atomic per feature+cell. `feature_key`, `build_digest`, `intended_purpose_id/version`, `owner_legal_entity_id`, `device_manufacturer_id?`, `care_operator_id?`, scope arrays (approved_users, patient_populations, clinical_settings, exclusions, inputs, outputs, foreseeable_misuse), `decision_influence`, `jurisdiction_pack_id/version`, `data_cell_id`, `device_assessment{status, authority, regime, class, decision_ref, assessor, review_date, expires_at}`, `provider_activity_assessment{status, licence_ref, opinion_ref}`, `safety_case_id`, `hazard_register_version`, `control_set_version`, `partner_protocol_versions[]`, `tests_digest`, `monitor_rule_set`, `approved_by` (clinical, regulatory, quality, security, privacy, operations), `lifecycle` (draft, assessment, blocked, approved, suspended, retired), `signed_at`, `effective_from/until`, `suspension_reason`. "approved" is rejected without every compulsory approval.
### gov.release_candidate / gov.release_tuple [P4] [19, 20]
`release_id`, `dossier_revision`, `tuple_digest`, `env`, `scope`, `state` (DRAFT, EVIDENCE_REVIEW, G3_READY, G4_AUTHORIZED, PILOT_ACTIVE, EXPANDING, STEADY_STATE, HOLD, SUSPENDED, ROLLBACK, DEGRADED). The tuple binds: application_digest, deployment_manifest_hash, workflow_version, api_profile_version, country_pack_id+hash, cell_id/region, data_policy_version, enabled_feature_set, intended_purpose_id, connector_digest, partner_contract_ref, clinical_protocol_version, hazard_case_ref, evidence_bundle_hash, qa_attestation_ref, ops_rota_ref, runbook_version, rollback_plan_ref. Evidence from another tuple is not reusable except with a signed equivalence assessment.
### gov.country_permit [P4] [20]
`permit_id`, `pack_hash`, `jurisdiction`, `subregion`, `cell_id`, `features`, `expiry`, `approvers`; signed, time-bound, no inheritance between markets.
### gov.event_schema + gov.event_schema_version [P1] [13]
Immutable: `type`, `major`, `minor`, `breaking`, `review`, `hash`. Global copy holds sanitized metadata only.
### gov.workflow_template [P1] [12]
Global template registry: `template_id`, `schema_hash`, `allowed_markets` (no patient data).
### gov.connector_catalog [P2] [13, 22]
Non-personal connector definitions and certification tuple (partner, market, region, capability, profile, scope, version). See 04 for cell-side tables.
### gov.ai_use_permit / gov.model_card / gov.dataset_card [P4] [23]
See file 05 (AI).

---
## B. IDENTITY, TENANCY, DELEGATION (cell, schema `trust`) [06, 05]

Separate entities with separate IDs: actor, account, patient subject, source record, affiliation, credential, proxy grant, workload (GHE-06 I-02). Login ≠ identity proof ≠ record match ≠ professional credential ≠ delegation ≠ purpose.

### trust.tenant [P1]
`tenant_id`, `name`, `type` (provider-org, payer, employer, partner, platform), `scope`, `status`, `authority_ref`. UQ within cell.
### trust.organization [P1] [06 C6, 14 D1]
Std-A. `public_id`, `ref_no` (ORG), `legal_entity_ref`, `trading_name`, `display_name`, `national_authority_ids[] {issuer, id}`, `entity_status`, `type` (hospital, clinic, lab, imaging, pharmacy, payer, employer, community care, integrator), `parent_org_id FK→ self`, `contract_status` (active/expired/suspended), `country_pack_ref`, `admin_delegation_ref`, `ownership_source_assertions`.
### trust.facility [P1]
Physical/virtual site. `organization_id FK→`, `ref_no` (FAC), `address`, `geo`, `time_zone` (IANA), `licensed_site_scope`, `operating_hours`, `accessibility`, `status`. (Same as catalog.location; keep ONE table, referenced by both. See 03.)
### trust.actor [P1] [06 C1]
Std-A. Any authenticated principal. `actor_id`, `actor_type` (patient, clinician/allied, facility org user, caregiver/guardian/proxy, payer user, employer user, partner/service workload, platform support), `issuer_binding` (IdP subject, never a national id), `identity_assurance` (per-market, per-transaction; proofing confidence + provider), `status` (INFERRED: pending, active, suspended, revoked, recovering), `last_verified_at`.
### trust.account_credential [P1] INFERRED
Auth factors metadata only: `actor_id FK→`, `method` (password/passkey/OIDC/etc), `verified_at`, `locked`, `last_used_at`. Secrets live in the IAM product, not here.
### trust.session / trust.device [P1] INFERRED
`session_id`, `actor_id`, `role_context` (active role), `patient_context`, `cell_id`, `step_up_level`, `started_at`, `expires_at`, `device_id`. Role switch clears protected cached views (GHE-15).
### trust.proofing_session [P2] [06]
Created by `POST /cell/identity/proofing/start`. `actor_id`, `provider`, `outcome`, `confidence`, `started_at`, `completed_at`. Event `identity.subject.proofed.v1`.
### trust.person_subject [P1] [06 C2, 10]
The patient/person inside the cell. `person_key` (cell-scoped random; NOT a national id), `ref_no` (PER), `identity_status`, minimum local demographics (legal name, birth date, sex/gender per local policy; encrypted, D2), `preferred_language`, `accessibility_needs`, `linked_actor_id FK→ actor` (nullable: a person may have no login), `unlinked_account` flag (registered but no match, restricted services). No global MPI: moving cells creates an independent subject.
### trust.source_patient_link [P1] [06 C3, 10]
Std-A (versioned). `person_subject_id FK→`, `source_system`, `assigning_authority`, `external_id` (tokenized/encrypted), `identifier_version`, `match_evidence_ref`, `status` (match enum), `version`. UQ(source_system, assigning_authority, external_id, version). Cross-authority link denied. Never auto-merge.
### trust.match_case [P2] [06]
`case_ref` (e.g. M-1029), `source` (e.g. LAB-A), `result_routing` (QUARANTINED...), `factor_comparison` (legal name, birth date, masked local health id), `conflict_flags[]` (twin, name change, alias, shared address, transposition), `candidates` (redacted), `state` (match enum), `decided_by` (two reviewers if critical), `policy_version`, `not_same_marker` (do not re-propose without new evidence). Events: `patient.link.review_required.v1`, `patient.link.corrected.v1`.
### trust.match_parameter [P2] INFERRED
Versioned, locally tuned matching thresholds.
### trust.role [P1] [15]
Catalog of roles (R01..R12 and later). `role_code`, `name`, `default_prohibitions`, `workspace`. (See file 07.)
### trust.permission [P1] INFERRED
`permission_code` (operation, resource_class, e.g. `read:lab_result`), `description`.
### trust.role_permission [P1]
Junction + `valid_from/valid_to`.
### trust.role_assignment / trust.org_affiliation [P1] [06 C4]
`actor_id`, `organization_id`, `role_id`, `effective_from`, `effective_until`, `sponsor_actor_id`, `removal_reason` (departure, expired licence, sanction, contract end), `status`. A role alone is NEVER sufficient authorization.
### trust.professional_qualification [P2] [06 C5]
`actor_id`, `registration_authority`, `registration_id`, `jurisdiction`, `scope`, `specialty`, `status` (not enumerated: PENDING, active, inactive, suspended INFERRED), `verified_at`, `check_due_at`, `expires_at`, `sanctions`, `facility_privilege_status`, `contracted_clinical_relationship`, `patient_scope` (default NONE). Event `professional.privilege.revoked.v1`. A licence does not grant facility privilege or patient access; suspension revokes immediately.
### trust.relationship_grant (delegation/proxy) [P2] [06 C7]
Std-A (versioned). `grant_id`, `granter_actor_id`, `delegate_actor_id`, `subject_person_id`, `relationship_type` (parent, guardian, caregiver, legal agent, other), `authority_source` (self-grant, legal instrument, provider attestation), `rights[]` (appointments, messages, billing, records), `exclusions[]` (sensitive categories, adolescent services), `purpose`, `status` (PROPOSED, PROOF_REQUIRED, APPROVAL_PENDING, ACTIVE, REJECTED, SUSPENDED, REVOKED, EXPIRED), `valid_from`, `expires_at`, `evidence_ref` (encrypted), `market_policy_ref`, `review_needed`, `issuer`, `version`. Revocation is prospective only and idempotent (event `delegation.revoked.v1`). Eligibility recomputed every request; cached grants never override a local deny.
### trust.care_relationship [P2] [06 C8] INFERRED
From the local "authority graph": `actor_id` (clinician), `person_subject_id`, `basis` (care-team assignment, referral, appointment), `valid_from`, `valid_to`, `status`, `contested` flag. Required for clinician access; expired/contested = deny.
### trust.workload_identity [P2] [06 C10]
`workload_id`, `trust_domain`, `cert_key_ref`, `tenant_scope`, `expires_at`. Short-lived, rotated; a workload token must not act as a human grant.
### trust.break_glass_session [P2→gated] [06 C11, 09]
`applicant_actor_id`, `facility_id`, `person_subject_id` (must be MATCHED), `reason` (required), `scope` (minimum encounter), `step_up_result`, `second_authorizer_actor_id` (if pack requires), `started_at`, `expires_at`, `export_allowed` (default false), `notifications`, `post_use_review` (mandatory: safety team + DPO), `review_outcome`, `market_status` (DISABLED until approved in the active pack).

---
## C. POLICY, PURPOSE, DATA RIGHTS, PRIVACY (cell, schema `policy`) [05, 06, 10]

### policy.flow_register / policy.flow_revision [P1] [05 §04]
Std-C. `flow_id` (`FLOW-nn`), `revision`, `market`, `subdivision`, `service`, `source_entity`, `destination_entity`, `controller`, `zoiko_role` (controller, processor, other, pending; HIPAA BA possible), `subject_category`, `data_classes[]`, `purpose`, `legal_basis` (Art 6, PENDING), `special_category_condition` (Art 9, PENDING), `confidentiality_basis` (PENDING), `agreement_type` (DPA, BAA, JCA), `subprocessor_id`, `residency_cell`, `retention_rule_id`, `rights_owner`, sign-offs (counsel, DPO, clinical). Runtime verdict BLOCKED if no approved revision. Seed: FL-01 provider-to-Zoiko referral, FL-02 lab result to clinician, FL-03 employer purchasing benefit, FL-04 patient overseas transfer, FL-05 customer support, FL-06 de-identified analysis.
### policy.purpose_grant [P1] [10 Consent/Permission/PurposeGrant]
Std-A. `person_subject_id`, `purpose_code` (e.g. DIRECT_CARE, CONTINUITY_OF_CARE, PAYMENT_COVERAGE), `basis_type` (consent, other lawful authority), `flow_revision_id`, `scope`, `controller`, `actor_id`/`recipient`, `valid_from`, `valid_to`, `revoked_at`, `evidence_ref`, `status`. Consent is one part of authorization, never the only legal basis. Revocation reassesses affected pipelines.
### policy.access_decision [P1] [06 C9, 05 B4]
Std-B (append-only, cell-local, tamper-evident; never in global events).
- `decision_id`, `request_id` (cell-only correlation), `occurred_at`, `expires_at` (short-lived)
- inputs: `actor_id`, `organization_id`, `subject_id`, `source_record_ref`, `operation` (e.g. `read:lab_result`), `purpose_code`, `resource_class`, `relationship_grant_id`, `workload_id`, `destination_cell`, `controller_approval_ref`, `transfer_review_ref`
- policy refs: `policy_version`, `policy_pack_hash`, `flow_revision`, `request_hash`
- result: `decision` (PERMIT, DENY, STEP_UP, REVIEW), `reason_codes[]` (e.g. NO_VERIFIED_CARE_RELATIONSHIP, TRANSFER_REVIEW_MISSING, POLICY_DENIED, JURISDICTION_INACTIVE, SUBJECT_LINK_UNVERIFIED), `obligations[]` (NO_EXPORT, NOTIFY_DATA_CONTROLLER), `scoped_fields`
- per-check evidence list `{authn, match, credential, care_relation, proxy, transfer, consent/authority}` each PASS/FAIL/N-A
- A deny must not leak patient existence. Event `access.denied.v1`.
### policy.transfer_request / policy.transfer_permit [P3] [05 W04/W05]
Request: `ref_no` (TRQ), `state` (BLOCKED...), `source_cell`, `source_controller`, `recipient_controller`, `data_classes`, `minimum_record_set`, `pack_version`, `purpose`, `legal_route`, `transfer_assessment_ref` (TRA-REV), `confidentiality_authorization`, `clinical_receipt`, `destination_retention`, `export_expiry`, escalations (controller, DPO, counsel, clinical safety). No override action exists. Permit: `permit_id`, `source_cell`, `destination_cell`, `data_classes[]`, `minimized_payload_schema`, `purpose`, `recipient_principal`, `max_uses` (1) + `use_count`, `expires_at` (short), `policy_digest`, `legal_decision_ref`, `signature` (source-cell key). Reused/tampered permit rejected; destination re-verifies recipient; permits invalidated on consent withdrawal or purpose change.
### policy.rights_case + policy.rights_case_task [P3] [05 §08]
`ref_no` (DSR), `subject_id`, `verification_status`, `request_type` (access, correction, erasure, portability, restriction, objection, consent withdrawal), `controllers[]`, `joint_or_independent`, `data_map`, `exemptions_holds`, `due_at` (computed per pack), `state` (INFERRED, not enumerated), `closure_authority`, `receipt_id`. Task: controller, state (sent/accepted/response delivered), notification state (draft/authorized/delivered). Cross-cell requests create linked case refs, no global identity.
### policy.retention_rule / policy.retention_ledger / policy.legal_hold [P3] [05 W08, 10 §8]
Rule per data class + market + purpose (no global TTL). Ledger: per-dataset eligibility, hold checks, expiry, purge status for each copy type (transactional DB, object store, queues/DLQ, search/cache, backups/DR, analytics exports, audit/safety evidence, devices/notifications), backup expiry or crypto-erasure, vendor notification, signed evidence. Hold: reason, custodians, `expires_at`, re-evaluated automatically. Erasure never authorizes tampering with legally required clinical/audit records.
### policy.dpia [P3] [05 §12]
`dpia_id`, `status` (DRAFT, REVIEW, BLOCKED, APPROVED), service/journeys, country/subdivision, cell, controller/processor matrix, data classes, necessity/proportionality, subject categories (children, employees, vulnerable), transfer map + subprocessors + remote access, harms, controls, likelihood x severity, residual rating, consultation, sign-offs (DPO, Country Legal, Controller, Security, Clinical), change register, review triggers. Unresolved HIGH residual risk blocks. Junction `dpia_flow`.
### policy.subprocessor [P3] [05 §11]
Legal entity, market applicability, data classes, purpose, location, remote-support origin, onward subprocessors, incident timetable, deletion commitments.
### policy.privileged_access_grant [P2] [05 W10, 09 W06]
JIT support. `requester_actor_id`, `target_cell`, `purpose` (incident/approved support), `ticket_ref` (INC-), `resource`, `patient_data_scope`, `duration` (proposed 15 min), `local_approvers[]`, `security_signoff`, `privacy_review`, `exact_grants`, `session_recording_ref`, `status` (INFERRED: requested, approved, active, expired, denied), `started_at`, `expires_at`. Per-cell, dual approval, expiring; no standing sessions; no global admin to PHI.
### policy.role_purpose_registry [P2] [05 §07] INFERRED
Role → allowed lawful purposes per market (employer no D3; payer claims fields only; guardian proxy revocable; clinical emergency = break-glass; sensitive specialty compartmentalized).
### policy.sensitive_category [P2] [06] INFERRED
Country-defined segmentation tags (behavioral health, reproductive health, HIV/genetic, minor confidentiality). No universal taxonomy.
### policy.policy_pack_binding [P1] INFERRED
Cell-local copy of the active signed pack revision + validation result (signature OK, not expired, epoch OK). Packs pulled from global plane; cell validates signature/expiry/version/local approvals.
### policy.pack_audit_event [P1] [04 §13]
Std-B: `local_event_id`, `local_cell_id`, `pack_id`, `pack_digest`, `action`, `actor_role_id`, `timestamp_utc`, `approval_ref`, `evaluated_scopes`, `decision`, `reason_code`, `trace_ref`, `prev_digest`, `immutable_record_digest`.
### policy.incident_suspension [P3] [04 W07]
`incident_id` (LOCAL-nnn), market, capability scope, trigger, immediate actions, open-case count (in-cell only), handoff plan (named provider owner, runbook), alternatives, evidence, authorizer.
