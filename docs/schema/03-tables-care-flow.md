# 03. Tables: catalog, scheduling, referral, workflow, clinical exchange, evidence, platform plumbing

All tables are CELL tables (cell_id, tenant_id, classification, created_at, ... per README §4). `[P#]` build phase. Std-A / Std-B / Std-C as defined in 02.

---
## A. PROVIDER DIRECTORY / CATALOG (schema `catalog`, D03) [14]

Partner remains authoritative; we publish only verified, source-qualified claims. No sensitive personal data of practitioners in the directory.

### catalog.practitioner [P1]
Std-A. `public_id`, `ref_no` (PRC), `actor_id FK→ trust.actor` (nullable), `credential_refs[]` (by jurisdiction + discipline), `display_name`, `languages[]`, `status`.
### catalog.practitioner_role [P1]
`practitioner_id`, `organization_id`, `location_id` (= trust.facility), `service_id`, `valid_from`, `valid_to`, `credential_scope`, `active`, `publication_status`. Licensure is never inherited from an unrelated org/location (test T-03).
### catalog.healthcare_service [P1]
Std-A. `ref_no` (SVC), `organization_id`, `taxonomy_code_set` + `code`, `service_modes` (in-person, remote...), `age_restrictions`/`eligibility` (provider-authored), `languages[]`, `accessibility`, `contact_channel`, `service_area`, `status`.
### catalog.partner_endpoint [P2]
`partner_id`, `organization_id`, `capability` (DISCOVERY_ONLY, AVAILABILITY_READ, REQUEST_TO_BOOK, HOLD_SUPPORTED, COMMIT_SUPPORTED, RESCHEDULE_CANCEL, WAITLIST_SUPPORTED, ATTENDANCE_SUPPORTED), `profile`/`version`, `auth_method`, `health`, `suspended_at`, `contract_limits`. FHIR: Endpoint.
### catalog.verification_claim [P1]
Provenance. `subject_type` (org/practitioner/service/location), `subject_id`, `claim_type`, `issuer`, `source_type` (registry API, certified feed, direct attestation), `document_hash`, `verifier_actor_id`, `verified_at`, `expires_at`, `status` (UNVERIFIED, SUSPENDED, REVIEW REQUIRED; full list INFERRED), `permitted_public_wording`, `conflicts`. Never self-awarded; expiry disables dependent capability; no auto-republish after expiry.
### catalog.listing [P1]
Enrolment state per provider/service: `state` (DRAFT, EVIDENCE_PENDING, REVIEWED, PUBLISHED, + UNVERIFIED/SUSPENDED/REVIEW REQUIRED), `second_approver_actor_id` (required when regulated), `reverify_due_at`.
### catalog.directory_projection [P1]
Search document built from the published index only: approved public fields, `source_version`, `last_verified_at`, `last_synced_at`, `listing_tier` (paid/sponsored label), `language_meta`, `visibility`.
### catalog.capacity_hint [P2]
`source_as_of`, `expires_at`, `designation` (approximate | bookable). Never equal to a reservation.
### catalog.referral_destination [P2]
`service_id`, `receiving_org_id`, `allowed_referrer_types[]`, `intake_constraints`, `document_identity_criteria`, `queue`, `fallback`.
### catalog.search_trace [P2]
`query_id` (regional, redacted), `country_scope`, `language`, `eligible_set`, `ranking_factors`, `policy_version`, `sponsored_overlay`. Replayable with sanitized fixtures; no PHI in search logs.
### catalog.directory_case [P2] INFERRED
Dispute/correction: correction request, evidence upload, verification result (approved/rejected/need-more), effective date, impact assessment, appeal.
### catalog.ops_case [P2] INFERRED
`case_ref` (opaque), `owner`, `severity`, `tab` (booking unknown, referral overdue, licensing alerts).

---
## B. ACCESS & SCHEDULING (schema `scheduling`, D04) [14]

Booking exists ONLY after authoritative partner confirmation. Technical ACK never = CONFIRMED. Slots are never HELD without partner proof.

### scheduling.booking_intent [P1]
Std-A. `ref_no` (APT), `person_subject_id`, `service_id`, `provider/practitioner_role_id`, `location_id`, `requested_start_at` + `requested_zone`, `purpose_grant_ref`, `policy_decision_ref`, `idempotency_key`, `last_actor_id`, `status` (16 internal states, see 06), `version`, `workflow_instance_id`.
### scheduling.availability_snapshot [P1]
`booking_intent_id` or `query_id`, `partner_id`, `slot_ref`, `start_at`, `end_at`, `time_zone`, `observed_at`, `valid_until`, `source_version`, `partner_rules`. FHIR Schedule/Slot.
### scheduling.booking_hold [P2]
Only if partner supports holds. `partner_hold_token`, `expires_at`, `offer_scope`, `status`.
### scheduling.partner_booking [P1]
The confirmed booking, owned by partner. `source_booking_id`, `correlation_id`, `partner_version`, `normalized_status`, `start_at`, `end_at`, `original_zone`, `utc_offset`, `fold`, `participants[]`, `source_evidence_ref`. UQ(partner_id, source_booking_id, partner_version). CONFIRMED requires a partner booking id. FHIR Appointment/AppointmentResponse.
### scheduling.booking_operation [P1]
`type` (create/cancel/change), `expected_version`, `phase`, `idempotency_token`, `request_hash`, `source_outcome`, `backoff`, `retries`, `last_attempt_at`, `next_probe_at`, `dead_letter_owner`, `response_signature`. A cancel stays active until the source acknowledges.
### scheduling.booking_event [P1]
Std-B. `partner_booking_id`/`booking_intent_id`, `state_from`, `state_to`, `authoritative_source`, `evidence_ref`, `receipt_type`, `recorded_at`.
### scheduling.waitlist_entry [P2]
Separate from a booking.

---
## C. REFERRALS & CARE COORDINATION (schema `referral`, D07) [14 §07, 11]

### referral.referral [P2]
Std-A. `ref_no` (REF), `referral_type` (CLINICIAN_ORDER | SELF_REFERRAL), `person_subject_id`, `source_order_ref`, `referrer_actor_id`/`referrer_role_ref`, `destination_id`, `purpose_ref`, `document_refs[]`, `state` (see 06), `clinical_acceptance` (NULL until receiving decision), `idempotency_key`, `workflow_instance_id`. Self-referral is administrative only, never creates a ServiceRequest or clinician signature. FHIR ServiceRequest/Task.
### referral.referral_decision [P2]
Append-only receiving decision: `referral_id`, `receiving_actor_id`, `decision` (accept | request more | decline), `reason`, `protocol`, `sla_policy`, `next_steps`, `decided_at`. Only verified receiving identity.
### referral.referral_document [P2]
`document_ref`, `scan_status` (clean/quarantined), `integrity_check`. Malware or content mismatch = quarantine.
### referral.discharge_transition [P3] INFERRED (J5)
Named recipient acceptance of hand-off; safeguarding obligations never inferred.

---
## D. WORKFLOW FABRIC (schema `workflow`, D05) [12, 11]

The spine. One engine, many journeys (J1..J8). Zoiko coordinates; external actors attest clinical action.

### workflow.workflow_definition [P1]
Std-C. `definition_id`, `semver`, `content_hash`, `owning_team`, `approving_signatures[]`, `journey_code` (J1..J8), `allowed_features`, `market_cell_restrictions`, `required_partner_contract_versions`, `roles` (initiator, owning org/actor, next responsible, approved fallbacks, patient/delegate visibility), `states_transitions` (source_state, trigger, guarded next_state, invariant, evidence_type), `timer_policies`, `exception_policies`, `evidence_requirements`, `compatibility` (schema/adapter versions, contract tests, rollback, migration approval), `closure_definition`. Lifecycle draft → validated → signed → released to cells. Immutable once released; cannot be deleted while instances are in flight.
### workflow.workflow_instance [P1]
Std-A. `ref_no` (WFI), `journey`, `definition_id` (PINNED version N), `country_pack_ref` (X), `safety_policy_ref` (Y), `person_subject_id`, `owner_organization_id`, `accountable_actor_id`, `current_state` (10 R3.0 states), `operational_overlay` (CLEAR, AT_RISK, EXCEPTION, RECONCILING), `purpose_code`, `version` (CAS), `created_at`, `closed_at`, `closure_snapshot_id`. CLINICAL_REVIEWED and HANDOFF_ACKNOWLEDGED are conditional per definition. Unique: owner_organization not null; jurisdiction pack not null; task.owner not null for safety-relevant handoffs.
### workflow.workflow_transition [P1]
Std-B. One row per accepted transition: `event_id`, `workflow_instance_id`, `prior_state`, `new_state`, `expected_version`, `source_event_ref`, `actor_id`, `authority_ref`, `purpose_code`, `country_pack_ref`, `policy_decision_ref`, `evidence_refs[]` (via junction `transition_evidence`), `occurred_at` (source), `recorded_at` (ours), `correlation_id`, `idempotency_ref`, `integrity_proof_ref`. Transition + evidence ref + outbox row commit atomically. Technical receipt can NEVER advance to CLINICAL_REVIEWED or HANDOFF_ACKNOWLEDGED.
### workflow.work_item / workflow.task [P1]
Std-A. `ref_no` (TSK), `workflow_instance_id`, `owner_organization_id`, `assigned_actor_id`, `responsible_clinical_actor_id`, `status`, `status_version`, `status_effective_at`, `purpose_code`, `source_refs[]`, `evidence_refs[]`, `last_transition_id`, `due_at`. Event `WorkItemStateChanged.v1`. `owner_party_id` NOT NULL for safety-relevant handoffs.
### workflow.workflow_timer [P1]
`timer_id`, `workflow_instance_id`, `step`, `policy_id`, `time_zone`, `deadline_utc`, `deadline_source`, `time_basis` (event vs processing), `business_calendar`, `status`, `fired_at`, `lease_owner`. Trigger classes: partner response overdue, clinical review overdue, follow-up handoff overdue, identity/match doubt, cross-border permission invalid, partner downtime/cell outage, policy/consent revoked. A stale timer after completion = no-op; DST handled.
### workflow.exception_case [P1]
Std-A. `ref_no` (EXC), `workflow_instance_id`, `code` (PATIENT_LINK_AMBIGUOUS, PARTNER_NOT_ACKNOWLEDGED, RESULT_SOURCE_CONFLICT, CLINICAL_ATTESTATION_MISSING, HANDOFF_OWNER_MISSING, CROSS_CELL_DENIED, PARTNER_OUTCOME_UNKNOWN, POLICY_REVOKED, DEFINITION_UNSUPPORTED), `severity`, `owner_actor_id`, `risk`, `status`, `opened_at`, `resolved_at`, `resolution_evidence_ref`, `reason`, `independent_check`/`dual_authorization`, `fallback_plan_ref`, `contact_attempts`, `step`. Resolution appends an event; never deleted or suppressed.
### workflow.partner_handoff [P2]
Five facts, each separate and evidenced: transport accepted, receiving system technical ACK, designated owner ACCEPTS (responsibility attestation), clinician REVIEWS (attestation), next party ACCEPTS. `workflow_instance_id`, `handoff_type`, `status`, `evidence_ref`, `actor`, `occurred_at`.
### workflow.pending_external_operation [P1]
`operation_ref`, `workflow_instance_id`, `initiated_at`, `state` (COMMITTED | UNKNOWN | PENDING_RECONCILIATION | RECONCILING | ERROR), `source_system`, `idempotency_proof`, `local_event_version`, `remote_order_status`, `reconciliation_outcome`, `manual_partner_attestation` (flagged manual), `attempts`, `retry_budget`, `retry_class` (TRANSIENT/IDEMPOTENT, UNKNOWN REMOTE RESULT, PERMANENT INVALID, MISMATCHED PATIENT, SOURCE CORRECTION). DLQ entries never deleted. No blind replay of non-idempotent clinical orders.
### workflow.escalation_plan [P2]
`plan_version`, `approved_by_cso`, `staffed_contact_route`, `expires_at`, `geography`, `primary_backup_timers`. Pause/suspend requires safety-officer approval.
### workflow.escalation_instance + workflow.escalation_attempt [P2]
Instance: `escalation_id`, `plan_version`, `trigger`, `clinical_owner_actor_id`, `next_due_at` + `time_zone`, `oncall_target_ref`, `state` (PLAN_ARMED, PRIMARY_PENDING, PRIMARY_ACCEPTED, BACKUP_DISPATCHED, BACKUP_ACCEPTED, SAFETY_EXCEPTION, CLINICAL_OWNER_REQUIRED, human resolution, CLOSED), `closure_basis_ref` (CLOSED only with owner-attested evidence). Attempt: `attempt_id`, `channel`, `delivery_outcome`, `reach_confirmed` (provider attests), `resolved`, `fallback_plan_ref`, timestamps. Four separate facts: attempted, delivered, reached, resolved.
### workflow.oncall_roster [P2]
Provider-verified contact numbers, freshness/verification time, reassignment history. Stale roster blocks assuming an owner.
### workflow.closure_snapshot [P1]
`workflow_instance_id`, `closure_predicate_version`, `satisfied_evidence_refs[]`, `open_risk_determination`, `signature`, `closed_event_id`. CLOSED requires predicate + snapshot. Administrative closure never claims treatment complete.
### workflow.instance_migration [P2]
Explicit per-instance migration record `{from_def, to_def, approval_ref, reauthorized_owner}`; in-flight executions never silently migrate.

---
## E. CLINICAL EXCHANGE & INTEROP (schema `exchange`, D06/D08/D09/D11) [07, 10]

Original untouched payload always preserved. Mapping never overwrites it. Corrections are additive.

### exchange.exchange_envelope [P2]
Std-A (versions append). The "ResultEnvelope" equivalent. `exchange_id`, `cell_id`, `country_pack_version`, `tenant_id`,
- `source`: `org_id`, `system_id`, `assigning_authority`, `record_id`, `record_version`, `reported_status`, `source_profile`
- `subject`: `patient_subject_ref`, `source_identifier_ref`, `match_status`, `match_evidence_ref`
- `context`: `purpose_code`, `policy_decision_ref`, `consent_ref`, `relationship_ref`, `sensitivity_tags[]`, `legal_basis_ref`
- `content`: `media_type`, `standard`, `standard_version`, `profile_url`, `payload_ref` (vault), `digest`, `encryption_key_ref`, `retention_class`
- `timing`: `clinical_effective_at`, `source_issued_at`, `observed_at`, `received_at`, `recorded_at`, `source_timezone`, `time_precision`
- `lifecycle`: `event_type`, `source_status`, `supersedes_exchange_id`, `correction_reason`, `validation_state`, `routing_state`, `delivery_state`
- `provenance`: `source_actor_ref`, `source_signature_ref`, `mapping_id`, `mapping_version`, `transformation_digest`, `trace_id`
- UQ(source system, assigning authority, source id, revision). Same version + same digest = dedupe. Same id + different digest = CONFLICT → quarantine.
### exchange.source_vault_object [P2]
Controlled clinical cache: `object_ref`, encrypted original bytes location, `digest`, `ttl`, `immutable_revision`, `key_ref`. DB holds only the ref. No copying "just in case".
### exchange.source_provenance [P2] [10 W06]
Std-B. `source_system`, `source_org`, `external_record_id`, `source_version`, `received_at`, `original_payload_digest`, `vault_object_ref`, `transport_identity_ref`, `signature_validation_status`, `transform_id`, `transform_version`, `terminology_release`, `derived_record_id`, `derived_record_version`, `parent_evidence_refs[]`, `clinical_attestation_ref`, `region_cell`, `tenant_scope`, `classification`. (FHIR Provenance on request.)
### exchange.terminology_release [P2]
`code_system_uri`, `assigning_authority`, `edition`, `version`, `release_date`, `subset_extension`, `license_rights`, `effective_period`. Systems: SNOMED CT, LOINC, ICD-10/11, UCUM, RxNorm/dm+d/national, CPT/HCPCS/payer catalogs, local codes. Retired codes stay interpretable under their pinned release.
### exchange.terminology_mapping [P2]
`mapping_id`, `mapping_version`, `direction`, source (`system`, `code`, `version`, `text`, `specimen_context`, `method`, `units`), target (`system`, `code`, `version`), `scope`, `reviewer`, `rationale`, `loss_flags`, `confidence`, `state` (UNMAPPED, PROPOSED, CLINICALLY_REVIEWED, VALIDATED, ACTIVE, DEPRECATED, REVOKED). Original code/units always preserved; no confidence-only approval for clinical-risk codes.
### exchange.quarantine_item [P2]
`exchange_id`, `stage`, `reason`, `failure_class` (SECURITY_DENIED, PATIENT_UNRESOLVED, STRUCTURE_INVALID, SEMANTIC_UNSAFE, DELIVERY_UNKNOWN, CORRECTION_CONFLICT, SOURCE_UNAVAILABLE, DISCLOSURE_INCIDENT), `owner`, `timer`. Quarantine never silently becomes a patient-facing result.
### exchange.diagnostic_order [P2]
`ref_no` (ORD), `workflow_instance_id`, `ordering_org`, `ordering_actor`, `source_order_ref`, `specimen_ids`, `accession`, `order_state` (SENT/UNCONFIRMED, REJECTED, PRELIMINARY, FINAL, AMENDED, SOURCE_FLAG_PRESERVED, CANCELLED), the 8 handoff evidence events (ORDER_AUTHORISED, SOURCE_ACCEPTED, SOURCE_REPORTED, TRANSPORT_DELIVERED, CLINICIAN_REVIEW_ATTESTED, PATIENT_RELEASE_AUTHORIZED, PATIENT_NOTIFIED/CONFIRMED_VIEW, FOLLOW_UP_OWNER_ACCEPTED) as `diagnostic_handoff_event` Std-B rows.
### exchange.result_ref [P2]
`ref_no` (RES), `diagnostic_order_id`, `exchange_id`, `source_version`, `supersedes_result_id`, `status`, `interpretation_flag` (as given by source), `safe_viewing_rights`. Source-controlled; no silent interpretation.
### exchange.clinical_attestation [P2]
Std-B. `actor_id` (credential), `result/order digest + version`, `clinical_role`, `scope`, `owning_org`, `delegated_authority`, `signature`, `validated_at`, `validation_policy_ref`, `attested_at`. Rejected for wrong reviewer, old report version, unsigned or revoked professional. Only a verified professional via the GHE-12 command can set `clinical_review_complete`.
### exchange.correction_case [P2]
v1 FINAL → v2 CORRECTED: field comparison (code/value/unit), mapping used, impacted recipients, clinician notification ownership (partner confirmation). Actions: request source verification, propagate, block old presentation, open safety incident.
### exchange.imaging_study_index [P3]
`study_uid`, `accession`, `modality`, `series`, `report_link`. Pixels stay in PACS.
### exchange.document_reference [P3]
`digest`, `mime`, `author_signer`, `source_copy_or_digest`, `status`, `supersedes`, `retention_class`. Not authoritative from OCR/AI.
### exchange.medication_ref [P3] (D09)
Separate refs for MedicationRequest, MedicationDispense, MedicationAdministration, MedicationStatement, allergy entries (`source`, `verificationStatus`, `reaction`, `version`). Reconciled list needs accountable clinical author. Never auto-merged. Zoiko never prescribes/substitutes/dispenses.
### exchange.consultation_ref [P3] (D10) INFERRED
Session logistics + provider-authored encounter reference.
### exchange.navigation_projection [P3] (D11) INFERRED
Read model: source-labelled longitudinal pathway view with source/time/certainty. Not authoritative.
### exchange.cross_border_decision [P4] [07 W13]
`sender_cell`, `destination`, `purpose`, `minimum_dataset`, approvals (GHE-04/05/06), `scoped_transfer_id`, audit in both cells.
### exchange.mapping_defect [P2] INFERRED
Quarantined transform, mapping defect, owner (terminology steward + clinical reviewer).

---
## F. EVIDENCE, AUDIT, EVENTS (schema `evidence` + `platform`) [10, 11, 13]

Four distinct things, never merged: (a) technical delivery receipt, (b) access/security audit, (c) business workflow state change, (d) clinician-signed clinical acknowledgment.

### evidence.audit_event [P1]
Std-B. Who accessed/attempted/modified/disclosed. `event_id`, `occurred_at_utc`, `actor_id`, `actor_kind`, `organization_ref`, `request_class`, `target_class`, `operation`, `purpose_code`, `result` (permit|deny|step_up|error), `reason_code`, `session_ref`, `source_service`, `build_digest`, `correlation_ref`, `policy_digest`, `evidence_chain_hash`. NO PHI content, no raw tokens. Hash-chained, WORM/immutable-capable.
### evidence.disclosure_log [P1]
Std-B. `who`, `org`, `role`, `purpose`, `data_scope`, `method`, `where`, `reason`, `origin`, `access_decision_id`, `evidence_ref`. Clinical reads logged without contents.
### evidence.evidence_record [P1] (ONE polymorphic table)
Replaces EvidenceRecord / EvidenceArtifact / evidence / evidence_ref (docs 19-23). `evidence_id`, `evidence_class` (policy decision, transport receipt, partner business acceptance, clinical review attestation, handoff acknowledgment, escalation attempt/outcome, administrative closure, correction/revocation, test evidence, certification, security attestation, DR rehearsal, ...), `sha256`, `vault_uri`, `issuer`/`origin_tool_person`, `issuing_role`, `signer`, `signatures[]`, `issued_at`, `expires_at`, `tuple_digest`, `retention_class`, `scrub_status`, `data_class`, `reviewer_ref`, `supersedes_id`, `legal_hold`. Patient-level evidence is never exported globally; global tables store only hash + pointer.
### evidence.attestation_ref [P2]
`issuer_org_id`, `qualified_actor_id`, `declared_action`, `source_system_ref`, `source_record_ref`, `source_version`, `patient_binding_ref`, `validated_at`, `validation_policy_ref`. Attestations are bound to a specific source version.
### evidence.evidence_checkpoint [P4] [10 W07]
Signed checkpoints over a partition/sequence range: `partition`, `from_seq`, `to_seq`, `checkpoint_at`, `signer_key_id`, `verified`, `gaps`. Sequence gap/duplicate/hash mismatch freezes evidence promotion and opens an incident.
### platform.domain_event [P1]
Immutable envelope: `event_id`, `schema_id`, `type` (e.g. `booking.partner.confirmed.v1`), `owner`, `version`, `subject_scope`, `aggregate_ref`, `occurred_at`, `recorded_at`, `provenance`, `sensitivity`, `dedupe_key`, `correlation_id`, `causation_id`, `policy_ref`, `evidence_ref`, `payload_ref`, `ack_class` (TRANSPORT_RECEIVED, TECHNICAL_ACCEPTED, BUSINESS_ACCEPTED, CLINICAL_ATTESTED).
### platform.outbox_event [P1]
`event_id`, `aggregate_ref`, `payload_ref` (not PHI), `policy_ref`, `evidence_ref`, `ordering_key`, `contract_version`, `schema_id`, `dedupe_key`, `state` (pending, published, failed, quarantined), `created_at`, `published_at`. Written in the same txn as the owned row. Poison messages quarantined.
### platform.inbox_message [P1]
`consumer`, `event_id` UQ(consumer, event_id), `processed_at`, `result`, `raw_body_digest`, `signature_decision`, `quarantine_flag`.
### platform.idempotency_key [P1]
`scope` (tenant + actor/endpoint or business key), `key_hash`, `request_digest`, `saved_result_ref`/`operation_ref`, `retries`, `retention_class`, `state` (COMMITTED, UNKNOWN, RECONCILING, ERROR), `created_at`, `expires_at`. UQ(scope, key_hash). Same key + different digest = CONFLICT. Retention per operation class, no global TTL.
### platform.command_envelope [P1] (not necessarily persisted)
`contract` (e.g. `core.pathway.command.v1`), `command_id`, `idempotency_key`, `correlation_id`, `causation_id`, `trace_ref`, `actor_ref`, `actor_authority_ref`, `subject_ref`, `purpose_code`, `country_pack_id`, `policy_decision_ref`, `classification`, `expected_entity_version`, `requested_at`, `expiry_at`, `payload`, `signature_or_mac_ref`. Reply: Accepted{operation_ref, entity_version, evidence_ref} | Rejected{stable_error_code, retryable, correlation_id}.
### platform.feature_activation [P1] [11 B2]
Cell-side switch: `domain`, `feature`, `country_pack_id/version`, `clinical_safety_approval_ref`, `data_flow_ref`, `partner_contract_ref`, `code_release`, `controls_tested`, `state` (DISABLED, CANARY, APPROVED, SUSPENDED), dual approvals. Missing/expired attestation fails closed.
### platform.domain_registry [P1] [11 B1]
Per D01..D18: `domain_id`, `name`, `owner_team`, `approved_jurisdictions[]`, `permitted_tenant_types[]`, `classification`, `db_schema_namespace`, `read_model_policy`, `event_subjects[]`, `deployment_artifact`, `feature_manifest`, `on_call_owner`, `version`. Each context owns its own schema namespace; cross-domain writes not allowed.
### platform.approval_decision [P1] (ONE polymorphic sign-off table)
Replaces GateDecision / AssessmentDecision / approval_event / pack_approval / sign-offs. `decision_id`, `subject_type`, `subject_id`, `gate`, `approver_actor_id`, `role`, `outcome`, `reasons`, `version_hash`, `expires_at`, `conflicts`, `created_at`. Four-eyes: approver ≠ creator. Vetoes recorded.
### platform.hold [P4]
Replaces Hold / feature suspension / connector suspension / market suspension. `hold_id`, `scope_type`, `scope_id`, `severity`, `reason`, `imposed_by`, `imposed_at`, `affected_release`, `recovery_approvers[]`, `lifted_at`. A hold overrides all prior approvals.
### platform.error_code [P1]
Lookup: POLICY_DENIED, JURISDICTION_INACTIVE, SUBJECT_LINK_UNVERIFIED, CONFLICT_VERSION, DUPLICATE_COMMAND, UNSAFE_REPLAY, PARTNER_UNCONFIRMED, CLINICAL_ACCEPTANCE_ABSENT, PENDING_RECONCILIATION (state). No PHI in errors (RFC 9457 problem+json).

---
## G. EXPANDED CARE PROGRAMS (schema `program`, D14/D15) INFERRED (docs name the domain, give few fields)
- `program.care_at_home_assignment` [P3]: provider matching, appointment, transition, safeguarding rules, safety/escalation evidence.
- `program.employer_program` [P3]: restricted entitlement, eligible enrolled person, program period; **never** links to clinical tables.
- `program.cohort_metric` [P3]: suppressed/aggregated measure releases only (see analytics).

---
## H. ANALYTICS & EVIDENCE PRODUCTS (schema `analytics`, D17) [10]
Tiered T0 telemetry, T1 tenant ops, T2 care-delivery quality, T3 privacy-protected population, T4 secondary research/AI (OFF by default).
### analytics.metric_definition [P3]
Std-C. `metric_id`, `semantic_version`, `steward`, `purpose`, `numerator_expression_ref`, `denominator_expression_ref`, `inclusion_exclusion`, `granularity`, `time_window`, `timezone_policy`, `source_contract_versions[]`, `missingness_rules`, `population_disclosure_policy`, `approved_audience`, `approval_refs[]`.
### analytics.report_run [P3]
`run_id`, `metric_version`, `code_digest`, `data_snapshot_ref`, `jurisdiction_policy_hash`, `query_digest`, `suppression_log_ref`, `approved_recipient_scope`, `generated_at`, `expires_at`. Every data point has a metric version + provenance.
### analytics.report_request / analytics.release_decision [P3]
Request (report id, audience, benefit, lawful purpose, sources) → disclosure assessment (cohort size, quasi-identifiers, rare-condition risk, differencing risk) → `RELEASE | DENY | GENERALIZE` + approval with expiry → `disclosure_receipt`, revoke/export log. A prior approval does not cover new sub-cohorts.
### analytics.dataset / analytics.dataset_lineage [P3]
Approved dataset versions, purpose, steward, retention, cell, owners, lineage.
### analytics.data_quality_result [P3]
Per dimension (completeness, validity, timeliness, uniqueness, consistency, provenance, reconciliation): measure, gate, status.
### analytics.reconciliation_count [P3]
Source/outbox/consumer counts and failure-queue balance with named owner.
### Named measures (examples, store as metric_definition rows)
Pathway administratively closed; result availability interval; provider review acknowledgment; partner response reliability; access equity; revenue/contribution.
